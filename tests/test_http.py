"""HTTP integration checks using an isolated database and a real loopback server."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from server import create_server


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.static = Path(self.temp.name) / "static"
        self.static.mkdir()
        (self.static / "index.html").write_text("<!doctype html><title>Work Time</title>", encoding="utf-8")
        (Path(self.temp.name) / "private.html").write_text("private", encoding="utf-8")
        self.server = create_server(Path(self.temp.name) / "data", static_dir=self.static)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None, headers=None):
        defaults = {"X-Time-Tracker": "1", "Content-Type": "application/json"}
        defaults.update(headers or {})
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=body, method=method, headers=defaults)
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = response.read()
            value = json.loads(data) if "application/json" in response.headers.get("Content-Type", "") else data
            return response.status, value, response.headers

    def test_health_and_static_are_available(self):
        status, value, _ = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(value["app"], "work-time")
        status, value, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Work Time", value)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])

    def test_manual_entry_is_durable_and_delete_is_reversible(self):
        status, value, _ = self.request("/api/time-entries", "POST", {"project": "INAL", "title": "HTTP scenario", "work_date": "2026-11-18", "duration_seconds": 9000, "request_id": "http-entry-1"})
        self.assertEqual(status, 200, value)
        status, snapshot, _ = self.request("/api/state?from=2026-11-16&to=2026-11-22")
        self.assertEqual(status, 200)
        entry = snapshot["entries"][0]
        self.assertEqual(entry["duration_seconds"], 9000)
        self.assertTrue((Path(self.temp.name) / "data" / "tracker.sqlite3").is_file())
        status, _, _ = self.request("/api/time-entries/" + entry["id"], "DELETE", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/state?from=2026-11-16&to=2026-11-22")[1]["entries"], [])
        self.assertEqual(self.request("/api/time-entries/" + entry["id"] + "/restore", "POST", {})[0], 200)
        self.assertEqual(len(self.request("/api/state?from=2026-11-16&to=2026-11-22")[1]["entries"]), 1)

    def test_missing_mutation_header_and_foreign_origin_are_rejected(self):
        payload = {"project": "INAL", "title": "Blocked"}
        self.assertEqual(self.request("/api/tasks", "POST", payload, {"X-Time-Tracker": ""})[0], 403)
        self.assertEqual(self.request("/api/tasks", "POST", payload, {"Origin": "https://example.org"})[0], 403)
        self.assertEqual(self.request("/api/tasks", "POST", payload, {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("/api/state")[1]["tasks"], [])

    def test_domain_validation_returns_json_error(self):
        status, result, _ = self.request("/api/time-entries", "POST", {"project": "INAL", "title": "Invalid", "work_date": "2026-11-18", "duration_seconds": -1})
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        self.assertEqual(self.request("/api/tasks", "POST", {"project": "OTHER", "title": "Invalid"})[0], 400)

    def test_static_traversal_and_database_paths_are_not_served(self):
        for path in ("/../private.html", "/%2e%2e/private.html", "/data/tracker.sqlite3", "/%2e%2e%5cprivate.html"):
            self.assertEqual(self.request(path)[0], 404, path)

    def test_backups_can_be_downloaded(self):
        status, backup, _ = self.request("/api/backups", "POST", {})
        self.assertEqual(status, 200, backup)
        status, listings, _ = self.request("/api/backups")
        self.assertEqual(status, 200)
        self.assertIn(backup["name"], [item["name"] for item in listings["backups"]])
        status, data, headers = self.request("/api/backups/" + backup["name"] + "/download")
        self.assertEqual(status, 200)
        self.assertTrue(data.startswith(b"SQLite format 3"))
        self.assertIn("attachment", headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
