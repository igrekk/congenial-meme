"""Officially sourced country calendars with a local, reviewable data snapshot.

The application does not treat an arbitrary web page as a calendar API. Its
bundled facts carry official source links and a review date. Refresh checks the
official documents, records availability and changes, and keeps the reviewed
facts when a document cannot be automatically interpreted.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

from .services import DomainError


COUNTRIES = {"BY": "Беларусь", "GE": "Грузия", "KZ": "Казахстан", "CZ": "Чехия"}
OFFICIAL_HOSTS = frozenset({
    "pravo.by", "etalonline.by", "government.by", "mintrud.gov.by", "president.gov.by",
    "matsne.gov.ge", "gov.ge", "moh.gov.ge",
    "gov.kz", "egov.kz", "adilet.zan.kz", "akorda.kz",
    "gov.cz", "e-sbirka.gov.cz", "mpsv.cz", "ppropo.mpsv.cz", "mzv.gov.cz",
})
MIN_YEAR, MAX_YEAR = 2025, 2030
MAX_SOURCE_BYTES = 2_500_000
KINDS = {"weekend", "holiday", "transfer", "workday"}


def official_url(value):
    if not isinstance(value, str):
        raise DomainError("Источник календаря должен быть официальным HTTPS-адресом.")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = -1
    allowed = any(host == base or host.endswith("." + base) for base in OFFICIAL_HOSTS)
    allowed = allowed or (host == 't.me' and parsed.path.startswith('/s/MintrudSocBelarus/'))
    if (parsed.scheme != "https" or not allowed or parsed.username or parsed.password
            or port not in (None, 443)):
        raise DomainError("Загрузка разрешена только с официальных сайтов выбранных стран.")
    return value


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_official(url):
    official_url(url)
    request = urllib.request.Request(url, headers={
        "User-Agent": "WorkTimeTracker/0.3 (official calendar document check)",
        "Accept": "text/html,application/pdf,application/json,text/plain;q=0.8,*/*;q=0.2",
    })
    opener = urllib.request.build_opener(OfficialRedirects())
    with opener.open(request, timeout=2.5) as response:
        official_url(response.geturl())
        body = response.read(MAX_SOURCE_BYTES + 1)
    if len(body) > MAX_SOURCE_BYTES:
        raise ValueError("Официальный документ превышает допустимый размер.")
    return body


def western_easter(year):
    """Gregorian Easter date; calendar rules are supplied by the source snapshot."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, remainder = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, remainder + 1)


def orthodox_easter(year):
    # Supported years are 2025–2030, whose Julian/Gregorian difference is 13 days.
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, remainder = divmod(d + e + 114, 31)
    return date(year, month, remainder + 1) + timedelta(days=13)


def _iso_day(value, year):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("В исходных данных календаря указана некорректная дата.")
    parsed = date.fromisoformat(value)
    if parsed.year != year:
        raise ValueError("Дата исходного календаря относится к другому году.")
    return parsed.isoformat()


class HolidayService:
    def __init__(self, data_dir, data_path=None, *, data=None, fetcher=None, clock=None):
        self.directory = Path(data_dir).resolve() / "holiday-cache"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.data_path = Path(data_path) if data_path else Path(__file__).with_name("holiday_data.json")
        self.data = deepcopy(data) if data is not None else None
        self.fetcher = fetcher or download_official
        self.clock = clock or time.time
        self.lock = threading.RLock()

    @staticmethod
    def validate(country, year):
        if not isinstance(country, str) or country.upper() not in COUNTRIES:
            raise DomainError("Выберите Беларусь, Грузию, Казахстан или Чехию.")
        if type(year) is int:
            value = year
        elif isinstance(year, str) and re.fullmatch(r"\d{4}", year):
            value = int(year)
        else:
            raise DomainError("Укажите год календаря.")
        if not MIN_YEAR <= value <= MAX_YEAR:
            raise DomainError(f"Календари доступны за {MIN_YEAR}–{MAX_YEAR} годы.")
        return country.upper(), value

    def _snapshot(self):
        if self.data is not None:
            return deepcopy(self.data)
        try:
            data = json.loads(self.data_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise DomainError("Официальные данные календарей еще не подготовлены.", 503) from error
        if not isinstance(data, dict) or not isinstance(data.get("countries"), dict):
            raise DomainError("Не удалось прочитать данные официальных календарей.", 503)
        return data

    @staticmethod
    def _sources(config, annual):
        result, seen = [], set()
        for source in list(config.get("sources", [])) + list(annual.get("sources", [])):
            url = official_url(source.get("url"))
            if url in seen:
                continue
            seen.add(url)
            item = {"title": str(source.get("title") or url), "url": url}
            if isinstance(source.get("sha256"), str):
                item["sha256"] = source["sha256"]
            result.append(item)
        return result

    @staticmethod
    def _build_days(config, annual, year):
        days = {}
        cursor = date(year, 1, 1)
        while cursor.year == year:
            if cursor.weekday() >= 5:
                days[cursor.isoformat()] = {
                    "date": cursor.isoformat(), "kind": "weekend", "name": "Выходной",
                    "names": ["Выходной"], "is_working": False,
                }
            cursor += timedelta(days=1)

        def add(item, kind):
            day = _iso_day(item.get("date"), year)
            name = str(item.get("name") or {"holiday": "Праздничный день", "transfer": "Перенесенный выходной", "workday": "Перенесенный рабочий день"}[kind])
            old = days.get(day)
            names = list(old["names"]) if old and old["kind"] == kind else []
            if name not in names:
                names.append(name)
            result = {"date": day, "kind": kind, "name": " / ".join(names),
                      "names": names, "is_working": kind == "workday"}
            if item.get("source_url"):
                result["source_url"] = official_url(item["source_url"])
            days[day] = result

        holidays = []
        if not annual.get("replace_holidays", False):
            for fixed in config.get("fixed", []):
                item = dict(fixed)
                item["date"] = date(year, int(item["month"]), int(item["day"])).isoformat()
                holidays.append(item)
            pascha = {"western": western_easter, "orthodox": orthodox_easter}.get(config.get("easter"))
            if pascha:
                easter = pascha(year)
                for offset in config.get("easter_offsets", []):
                    item = dict(offset)
                    item["date"] = (easter + timedelta(days=int(item["offset"]))).isoformat()
                    holidays.append(item)
        holidays.extend(annual.get("holidays", []))
        excluded = set(annual.get("excluded_dates", []))
        for item in holidays:
            if item.get("date") not in excluded:
                add(item, "holiday")
        for item in annual.get("transfers", []):
            # A transferred day never hides an explicitly designated holiday.
            old = days.get(item.get("date"))
            if not old or old["kind"] != "holiday":
                add(item, "transfer")
        for item in annual.get("workdays", []):
            add(item, "workday")
        return [days[key] for key in sorted(days)]

    def _read_cache(self, country, year, snapshot_hash):
        path = self.directory / f"{country}-{year}.json"
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("snapshot_hash") == snapshot_hash:
                return cached
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def _write_cache(self, country, year, snapshot_hash, result):
        path = self.directory / f"{country}-{year}.json"
        temporary = self.directory / (f"{country}-{year}-{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps({"snapshot_hash": snapshot_hash, "result": result},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _check_sources(self, sources, previous):
        checked_at = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
        old_checks = {item.get("url"): item for item in previous.get("source_checks", [])}
        checks = []
        for source in sources[:4]:
            url = official_url(source["url"])
            try:
                body = self.fetcher(url)
                if not isinstance(body, bytes) or len(body) > MAX_SOURCE_BYTES:
                    raise ValueError("Некорректный или слишком большой официальный документ.")
                digest = hashlib.sha256(body).hexdigest()
                expected = source.get("sha256") or old_checks.get(url, {}).get("sha256")
                checks.append({"url": url, "status": "changed" if expected and expected != digest else "available",
                               "checked_at": checked_at, "sha256": digest})
            except (OSError, ValueError, urllib.error.URLError, DomainError):
                checks.append({"url": url, "status": "unavailable", "checked_at": checked_at})
        return checks

    def calendar(self, country, year, refresh=False):
        country, year = self.validate(country, year)
        if type(refresh) is not bool:
            raise DomainError("Параметр обновления календаря должен быть логическим.")
        snapshot = self._snapshot()
        config = snapshot["countries"].get(country)
        if not isinstance(config, dict):
            raise DomainError("Официальный календарь этой страны еще не подготовлен.", 503)
        annual = config.get("years", {}).get(str(year), {})
        sources = self._sources(config, annual)
        snapshot_hash = hashlib.sha256(json.dumps({"config": config, "reviewed_at": snapshot.get("reviewed_at")},
                                                 ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self.lock:
            cached = self._read_cache(country, year, snapshot_hash)
        previous = cached.get("result", {}) if cached else {}
        now = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
        coverage = "verified" if annual.get("coverage") == "verified" else "baseline"
        result = {
            "country": country, "name": str(config.get("name") or COUNTRIES[country]), "year": year,
            "week_model": "mon-fri", "days": self._build_days(config, annual, year),
            "sources": [{key: source[key] for key in ("title", "url")} for source in sources],
            "reviewed_at": annual.get("reviewed_at") or snapshot.get("reviewed_at"),
            "loaded_at": now, "coverage": coverage, "source_coverage": coverage,
            "is_cached": bool(cached), "checked_at": previous.get("checked_at"),
            "source_checks": previous.get("source_checks", []),
            "requires_review": bool(previous.get("requires_review", False)),
        }
        if coverage == "verified":
            result["status_message"] = "Праздники и переносы сверены с официальными источниками."
        else:
            result["status_message"] = "Базовые праздники по официальным правилам; ежегодные переносы не подтверждены."
        if refresh:
            checks = self._check_sources(sources, previous)
            result["source_checks"] = checks
            available = bool(checks) and all(item["status"] != "unavailable" for item in checks)
            changed = any(item["status"] == "changed" for item in checks)
            if available:
                result["checked_at"] = now
                result["requires_review"] = changed
                result["status_message"] += (" Текст источника изменился: даты требуют сверки."
                                             if changed else " Источники доступны; загружена сверенная версия дат.")
            else:
                result["coverage"] = "cached"
                result["is_cached"] = True
                result["status_message"] += " Связь с официальным источником недоступна; используется сохраненная версия."
        with self.lock:
            self._write_cache(country, year, snapshot_hash, result)
        return result
