"""Loopback HTTP adapter for the local work-time application (stdlib only)."""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import traceback
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from backend import DomainError, Tracker
from backend.holidays import HolidayService

APP_NAME = "work-time"
VERSION = "0.2.0"
APP_DIR = Path(__file__).resolve().parent
MAX_BODY_BYTES = 1_048_576
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


class WorkTimeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, tracker, static_dir):
        self.tracker = tracker
        self.holidays = HolidayService(tracker.storage.directory)
        self.static_dir = Path(static_dir).resolve()
        super().__init__(address, WorkTimeHandler)


class WorkTimeHandler(BaseHTTPRequestHandler):
    server_version = "WorkTime/" + VERSION
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Request bodies and notes are never logged.
        sys.stderr.write("[work-time] " + (fmt % args) + "\n")

    def _headers(self, status, content_type, length, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True

    def _json(self, status, value):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(payload))
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _check_origin(self):
        port = self.server.server_port
        host = self.headers.get("Host", "").lower()
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise DomainError("Недопустимый адрес локального приложения.", 403)
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + host:
            raise DomainError("Запрос с другого сайта запрещен.", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise DomainError("Запрос с другого сайта запрещен.", 403)

    def _body(self):
        if self.headers.get("X-Time-Tracker") != "1":
            raise DomainError("Откройте приложение и повторите действие.", 403)
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise DomainError("Ожидается запрос в формате JSON.", 415)
        if self.headers.get("Transfer-Encoding"):
            raise DomainError("Этот формат передачи запроса не поддерживается.", 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise DomainError("Неверный размер запроса.", 400) from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise DomainError("Слишком большой запрос.", 413)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DomainError("Не удалось прочитать JSON запроса.", 400) from exc
        if not isinstance(body, dict):
            raise DomainError("Ожидается объект JSON.", 400)
        return body

    def _dispatch_get(self, path, query):
        tracker = self.server.tracker
        if path == "/api/health":
            return self._json(200, {"ok": True, "app": APP_NAME, "version": VERSION})
        if path == "/api/state":
            params = parse_qs(query, keep_blank_values=True)
            return self._json(200, tracker.state(params.get("from", [None])[0], params.get("to", [None])[0]))
        if path == '/api/holidays':
            params = parse_qs(query)
            return self._json(200,self.server.holidays.calendar(params.get('country',['BY'])[0],params.get('year',['2026'])[0]))
        if path == "/api/backups":
            return self._json(200, {"backups": tracker.list_backups()})
        match = re.fullmatch(r"/api/backups/([A-Za-z0-9_.-]{1,180})/download", path)
        if match:
            name = match.group(1)
            source = tracker.backup_path(name)
            with source.open("rb") as stream:
                length = source.stat().st_size
                self._headers(200, "application/vnd.sqlite3", length, {"Content-Disposition": f'attachment; filename="{name}"'})
                if self.command != "HEAD":
                    while chunk := stream.read(65536):
                        self.wfile.write(chunk)
            return
        if path.startswith("/api/"):
            raise DomainError("Неизвестный адрес API.", 404)
        relative = "index.html" if path == "/" else path.lstrip("/")
        if "\x00" in relative or "\\" in relative:
            raise DomainError("Страница не найдена.", 404)
        base = self.server.static_dir
        source = (base / relative).resolve()
        if not source.is_relative_to(base) or source.suffix.lower() not in {".html", ".css", ".js", ".svg", ".png", ".ico", ".webmanifest"} or not source.is_file():
            raise DomainError("Страница не найдена.", 404)
        payload = source.read_bytes()
        content_type = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}.get(source.suffix.lower()) or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        if source.suffix.lower() in {".html", ".css", ".js", ".svg"}:
            content_type += "; charset=utf-8"
        self._headers(200, content_type, len(payload))
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _dispatch_mutation(self, path):
        body = self._body()
        tracker = self.server.tracker
        method = self.command
        if method == "POST":
            if path == '/api/holidays/load':
                return self._json(200,self.server.holidays.calendar(body.get('country','BY'),body.get('year',2026),refresh=bool(body.get('refresh',True))))
            exact = {
                "/api/tasks": lambda: tracker.create_task(body),
                "/api/time-entries": lambda: tracker.create_entry(body),
                "/api/timer/start": lambda: tracker.start_timer(body),
                "/api/backups": tracker.create_backup,
                "/api/backups/restore": lambda: tracker.restore_backup(body.get("name")),
            }
            if path in exact:
                return self._json(200, exact[path]())
        match = re.fullmatch(r"/api/tasks/([A-Za-z0-9_-]{1,100})", path)
        if match and method == "PATCH":
            return self._json(200, tracker.update_task(match.group(1), body))
        match = re.fullmatch(r"/api/time-entries/([A-Za-z0-9_-]{1,100})", path)
        if match and method in {"PATCH", "DELETE"}:
            entry_id = match.group(1)
            return self._json(200, tracker.update_entry(entry_id, body) if method == "PATCH" else tracker.delete_entry(entry_id))
        match = re.fullmatch(r"/api/time-entries/([A-Za-z0-9_-]{1,100})/restore", path)
        if match and method == "POST":
            return self._json(200, tracker.restore_entry(match.group(1)))
        match = re.fullmatch(r"/api/timer/([A-Za-z0-9_-]{1,100})/(pause|resume|stop)", path)
        if match and method == "POST":
            timer_id, operation = match.groups()
            fn = {"pause": tracker.pause_timer, "resume": tracker.resume_timer, "stop": tracker.stop_timer}[operation]
            return self._json(200, fn(timer_id))
        raise DomainError("Действие не найдено.", 404)

    def _handle(self):
        try:
            self._check_origin()
            parts = urlsplit(self.path)
            if parts.scheme or parts.netloc:
                raise DomainError("Недопустимый адрес запроса.", 400)
            path = unquote(parts.path)
            if self.command in {"GET", "HEAD"}:
                self._dispatch_get(path, parts.query)
            elif self.command in {"POST", "PATCH", "DELETE"}:
                self._dispatch_mutation(path)
            else:
                raise DomainError("Метод не поддерживается.", 405)
        except DomainError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._json(500, {"error": "Не удалось выполнить действие. Проверьте окно приложения и повторите попытку."})

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle
    do_PATCH = _handle
    do_DELETE = _handle
    do_OPTIONS = _handle
    do_PUT = _handle


def create_server(data_dir=None, port=0, *, tracker=None, static_dir=None):
    """Create a loopback-only server; callers own serve_forever/shutdown."""
    tracker = tracker if tracker is not None else Tracker(data_dir or APP_DIR / "data")
    return WorkTimeServer(("127.0.0.1", port), tracker, static_dir or APP_DIR / "static")


def existing_app(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as response:
            return json.load(response).get("app") == APP_NAME
    except (OSError, ValueError, urllib.error.URLError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Локальное приложение учета рабочего времени")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=APP_DIR / "data")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("порт должен находиться в диапазоне 0–65535")
    if args.port and existing_app(args.port):
        url = f"http://127.0.0.1:{args.port}"
        print("Приложение уже запущено: " + url, flush=True)
        if args.open_browser:
            webbrowser.open(url)
        return 0
    server = None
    last_error = None
    ports = [0] if args.port == 0 else range(args.port, min(args.port + 10, 65535) + 1)
    for port in ports:
        try:
            server = create_server(args.data_dir, port)
            break
        except OSError as exc:
            if getattr(exc, "winerror", None) != 10048 and exc.errno not in {48, 98, 10048}:
                raise
            last_error = exc
    if server is None:
        raise SystemExit("Не удалось выбрать свободный локальный порт: " + str(last_error))
    url = f"http://127.0.0.1:{server.server_port}"
    print("Рабочее время: " + url, flush=True)
    print("Данные: " + str(args.data_dir.resolve()), flush=True)
    print("Закройте это окно или нажмите Ctrl+C, чтобы остановить приложение.", flush=True)
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nПриложение остановлено. Данные сохранены.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
