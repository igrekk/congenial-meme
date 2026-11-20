"""Small domain services exposed through Tracker, independent of the HTTP layer."""

from datetime import date, datetime, time as datetime_time, timedelta, timezone
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid

from .storage import Storage

try:
    from zoneinfo import ZoneInfo
    MOSCOW = ZoneInfo("Europe/Moscow")
except (ImportError, KeyError):
    # Windows without IANA data still supports the application's fixed Moscow zone.
    MOSCOW = timezone(timedelta(hours=3), "Europe/Moscow")

PROJECTS = ("INAL", "INAC", "GCVU", "ECGP")


class DomainError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def identifier(prefix):
    return prefix + uuid.uuid4().hex


def text(value, label, maximum=160, required=True):
    if not isinstance(value, str):
        raise DomainError(f"Поле «{label}» должно быть текстом.")
    result = value.strip()
    if (required and not result) or len(result) > maximum:
        raise DomainError(f"Укажите «{label}»: от {1 if required else 0} до {maximum} символов.")
    return result


def work_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DomainError("Укажите корректную дату в формате ГГГГ-ММ-ДД.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise DomainError("Укажите существующую календарную дату.") from None
    return value


def duration(value):
    if type(value) is not int or not 1 <= value <= 86400:
        raise DomainError("Длительность должна быть целым числом секунд: от 1 секунды до 24 часов.")
    return value


MAX_MANUAL_INTERVAL = 31 * 86400


def interval_duration(value):
    if type(value) is not int or not 1 <= value <= MAX_MANUAL_INTERVAL:
        raise DomainError("Длительность интервала должна быть от 1 секунды до 31 дня.")
    return value


def local_timestamp(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", value):
        raise DomainError(f"Укажите дату и время поля «{label}».")
    try:
        return datetime.fromisoformat(value).replace(tzinfo=MOSCOW)
    except ValueError:
        raise DomainError(f"Некорректная дата или время поля «{label}».") from None


def local_iso(value):
    return value.astimezone(MOSCOW).replace(tzinfo=None).isoformat(timespec="seconds")


def interval_utc(value):
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise DomainError("Дата интервала выходит за допустимый диапазон.") from None


def manual_interval(payload, old_group=None):
    mode = payload.get("interval_mode")
    if mode not in (None, "bounds", "duration", "legacy", "duration_only"):
        raise DomainError("Неизвестный способ расчета длительности.")
    if mode in ("legacy", "duration_only"):
        return None
    start_value = payload.get("started_at", old_group["started_at"] if old_group else None)
    if not start_value:
        if mode in ("bounds", "duration") or payload.get("ended_at"):
            raise DomainError("Укажите дату и время начала.")
        return None
    start = local_timestamp(start_value, "Начало")
    end_value = payload.get("ended_at")
    supplied_duration = payload.get("duration_seconds")
    if end_value:
        end = local_timestamp(end_value, "Окончание")
        seconds = int((interval_utc(end) - interval_utc(start)).total_seconds())
        interval_duration(seconds)
        if supplied_duration is not None and interval_duration(supplied_duration) != seconds:
            raise DomainError("Окончание и указанная длительность не совпадают.")
    elif supplied_duration is not None or mode == "duration":
        seconds = interval_duration(supplied_duration)
        try:
            end = (interval_utc(start) + timedelta(seconds=seconds)).astimezone(MOSCOW)
        except (OverflowError, ValueError):
            raise DomainError("Дата окончания выходит за допустимый диапазон.") from None
    elif old_group and old_group["ended_at"]:
        end = local_timestamp(old_group["ended_at"], "Окончание")
        seconds = interval_duration(int((interval_utc(end) - interval_utc(start)).total_seconds()))
    else:
        raise DomainError("Укажите дату и время окончания либо длительность.")
    return {"started_at": local_iso(start), "ended_at": local_iso(end), "duration_seconds": seconds,
            "start": start, "end": end}


def split_manual_interval(interval):
    cursor, end = interval_utc(interval["start"]), interval_utc(interval["end"])
    result = {}
    while cursor < end:
        local = cursor.astimezone(MOSCOW)
        try:
            midnight = datetime.combine(local.date() + timedelta(days=1), datetime_time(), MOSCOW)
            boundary = min(end, midnight.astimezone(timezone.utc))
        except (OverflowError, ValueError):
            boundary = end
        seconds = int((boundary - cursor).total_seconds())
        duration(seconds)
        result[local.date().isoformat()] = seconds
        cursor = boundary
    return result


class ProjectTaskService:
    def task(self, connection, task_id, allow_archived=False):
        if not isinstance(task_id, str):
            raise DomainError("Укажите задачу.")
        item = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if item is None:
            raise DomainError("Задача не найдена.", 404)
        if item["archived"] and not allow_archived:
            raise DomainError("Задача в архиве. Сначала верните ее в работу.", 409)
        return item

    def ensure(self, connection, payload, now):
        project = payload.get("project")
        if project not in PROJECTS:
            raise DomainError("Выберите проект INAL, INAC, GCVU или ECGP.")
        title = text(payload.get("title"), "Задача")
        description = text(payload.get("description", ""), "Описание", 2000, False)
        for item in connection.execute("SELECT * FROM tasks WHERE project=? AND archived=0", (project,)):
            if item["title"].casefold() == title.casefold():
                return item
        task_id = identifier("t_")
        connection.execute("INSERT INTO tasks(id,project,title,description,created_at) VALUES (?,?,?,?,?)",
                           (task_id, project, title, description, now))
        return self.task(connection, task_id)

    def view(self, connection, item):
        result = {key: item[key] for key in ("id", "project", "title", "description")}
        result["archived"] = bool(item["archived"])
        result["total_seconds"] = connection.execute(
            "SELECT COALESCE(SUM(duration_seconds),0) FROM time_entries WHERE task_id=? AND deleted_at IS NULL",
            (item["id"],)).fetchone()[0]
        return result


class TimeEntryService:
    def __init__(self, tasks):
        self.tasks = tasks

    @staticmethod
    def entry(connection, entry_id, allow_deleted=False):
        item = connection.execute("SELECT * FROM time_entries WHERE id=?", (entry_id,)).fetchone()
        if item is None or (item["deleted_at"] is not None and not allow_deleted):
            raise DomainError("Запись времени не найдена.", 404)
        return item

    @staticmethod
    def view(item, connection=None):
        result = {key: item[key] for key in
                  ("id", "task_id", "work_date", "duration_seconds", "source", "note", "session_id")}
        result.update({"started_at": None, "ended_at": None, "manual_group_id": item["manual_group_id"],
                       "group_duration_seconds": None})
        if item["manual_group_id"] and connection is not None:
            group = connection.execute("SELECT * FROM manual_groups WHERE id=?", (item["manual_group_id"],)).fetchone()
            if group:
                result.update({key: group[key] for key in ("started_at", "ended_at")})
                result["group_duration_seconds"] = group["duration_seconds"]
        return result

    def resolve_task(self, connection, payload, now, existing_task_id=None):
        if payload.get("task_id"):
            return self.tasks.task(connection, payload["task_id"],
                                   allow_archived=payload["task_id"] == existing_task_id)
        return self.tasks.ensure(connection, payload, now)

    @staticmethod
    def request_fields(payload):
        request_id = payload.get("request_id")
        if request_id is not None:
            request_id = text(request_id, "Идентификатор запроса", 120)
        fingerprint = hashlib.sha256(json.dumps(
            {key: value for key, value in payload.items() if key != "request_id"},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return request_id, fingerprint

    def create(self, connection, payload, now):
        request_id, fingerprint = self.request_fields(payload)
        if request_id:
            group = connection.execute("SELECT * FROM manual_groups WHERE request_id=?", (request_id,)).fetchone()
            if group:
                if group["request_fingerprint"] != fingerprint:
                    raise DomainError("Идентификатор запроса уже использован для другой записи.", 409)
                return self.group_result(connection, group["id"])
            old = connection.execute("SELECT * FROM time_entries WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["request_fingerprint"] != fingerprint:
                    raise DomainError("Идентификатор запроса уже использован для другой записи.", 409)
                return self.view(old, connection)
        interval = manual_interval(payload)
        if interval:
            note = text(payload.get("note", ""), "Примечание", 4000, False)
            task = self.resolve_task(connection, payload, now)
            group_id = identifier("m_")
            self.write_group(connection, group_id, task["id"], note, interval,
                             split_manual_interval(interval), now, request_id=request_id, fingerprint=fingerprint)
            return self.group_result(connection, group_id)
        day = work_date(payload.get("work_date"))
        seconds = duration(payload.get("duration_seconds"))
        note = text(payload.get("note", ""), "Примечание", 4000, False)
        task = self.resolve_task(connection, payload, now)
        entry_id = identifier("e_")
        connection.execute(
            "INSERT INTO time_entries(id,task_id,work_date,duration_seconds,source,note,request_id,"
            "request_fingerprint,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (entry_id, task["id"], day, seconds, "manual", note, request_id, fingerprint, now, now))
        return self.view(self.entry(connection, entry_id), connection)

    def group_result(self, connection, group_id, preferred_id=None):
        entries = [self.view(row, connection) for row in connection.execute(
            "SELECT * FROM time_entries WHERE manual_group_id=? ORDER BY work_date,id", (group_id,))]
        if not entries:
            raise DomainError("Записи ручного интервала не найдены.", 404)
        first = next((entry for entry in entries if entry["id"] == preferred_id), entries[0])
        return {**first, "entries": entries}

    def write_group(self, connection, group_id, task_id, note, interval, allocations, now,
                    request_id=None, fingerprint=None, anchor=None):
        existing = connection.execute("SELECT * FROM manual_groups WHERE id=?", (group_id,)).fetchone()
        old_rows = list(connection.execute("SELECT * FROM time_entries WHERE manual_group_id=?", (group_id,)))
        if anchor is not None and not anchor["manual_group_id"]:
            old_rows.append(anchor)
        ids = {row["work_date"]: row["id"] for row in old_rows}
        if anchor is not None and anchor["work_date"] not in allocations:
            ids[next(iter(allocations))] = anchor["id"]
        total = sum(allocations.values())
        start_value, end_value = (interval["started_at"], interval["ended_at"]) if interval else (None, None)
        if existing:
            connection.execute("UPDATE manual_groups SET task_id=?,started_at=?,ended_at=?,duration_seconds=?,"
                               "note=?,updated_at=? WHERE id=?",
                               (task_id, start_value, end_value, total, note, now, group_id))
        else:
            connection.execute("INSERT INTO manual_groups(id,task_id,started_at,ended_at,duration_seconds,note,"
                               "request_id,request_fingerprint,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (group_id, task_id, start_value, end_value, total, note, request_id, fingerprint, now, now))
        connection.execute("DELETE FROM time_entries WHERE manual_group_id=?", (group_id,))
        if anchor is not None and not anchor["manual_group_id"]:
            connection.execute("DELETE FROM time_entries WHERE id=?", (anchor["id"],))
        created_at = min((row["created_at"] for row in old_rows), default=now)
        for day, seconds in allocations.items():
            entry_id = ids.get(day, identifier("e_"))
            connection.execute("INSERT INTO time_entries(id,task_id,work_date,duration_seconds,source,note,"
                               "manual_group_id,created_at,updated_at) VALUES (?,?,?,?,'manual',?,?,?,?)",
                               (entry_id, task_id, day, seconds, note, group_id, created_at, now))

    def update(self, connection, entry_id, payload, now):
        old = self.entry(connection, entry_id)
        group = (connection.execute("SELECT * FROM manual_groups WHERE id=?", (old["manual_group_id"],)).fetchone()
                 if old["manual_group_id"] else None)
        interval = manual_interval(payload, group)
        if interval and old["source"] == "timer":
            raise DomainError("Для записи таймера измените дату и длительность без ручного интервала.")
        if group or interval:
            note = text(payload.get("note", group["note"] if group else old["note"]), "Примечание", 4000, False)
            if "task_id" in payload or "project" in payload or "title" in payload:
                task_id = self.resolve_task(connection, payload, now, old["task_id"])["id"]
            else:
                task_id = old["task_id"]
            allocations = (split_manual_interval(interval) if interval else
                           {work_date(payload.get("work_date", old["work_date"])):
                            duration(payload.get("duration_seconds", group["duration_seconds"] if group else old["duration_seconds"]))})
            group_id = old["manual_group_id"] or identifier("m_")
            self.write_group(connection, group_id, task_id, note, interval, allocations, now,
                             request_id=old["request_id"], fingerprint=old["request_fingerprint"], anchor=old)
            return self.group_result(connection, group_id, entry_id)
        day = work_date(payload.get("work_date", old["work_date"]))
        seconds = duration(payload.get("duration_seconds", old["duration_seconds"]))
        note = text(payload.get("note", old["note"]), "Примечание", 4000, False)
        if "task_id" in payload or "title" in payload or "project" in payload:
            task = self.resolve_task(connection, payload, now, old["task_id"])
            task_id = task["id"]
        else:
            task_id = old["task_id"]
        # A manually corrected timer row can move dates without changing its provenance.
        if old["session_id"] and day != old["work_date"]:
            conflict = connection.execute("SELECT 1 FROM time_entries WHERE session_id=? AND work_date=? AND id!=?",
                                          (old["session_id"], day, entry_id)).fetchone()
            if conflict:
                raise DomainError("На эту дату уже есть часть той же сессии таймера. Измените длительности отдельно.", 409)
        connection.execute("UPDATE time_entries SET task_id=?,work_date=?,duration_seconds=?,note=?,updated_at=? WHERE id=?",
                           (task_id, day, seconds, note, now, entry_id))
        return self.view(self.entry(connection, entry_id), connection)


class TimerService:
    def __init__(self, tasks, entries):
        self.tasks, self.entries = tasks, entries

    @staticmethod
    def session(connection, session_id):
        row = connection.execute("SELECT * FROM timer_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise DomainError("Сессия таймера не найдена.", 404)
        return row

    @staticmethod
    def current(connection):
        return connection.execute("SELECT * FROM timer_sessions WHERE status!='finished'").fetchone()

    @staticmethod
    def milliseconds(connection, session_id, now):
        return sum(max(0, (row["ended_at"] if row["ended_at"] is not None else now) - row["started_at"])
                   for row in connection.execute("SELECT * FROM timer_segments WHERE session_id=?", (session_id,)))

    def view(self, connection, session, now):
        if session is None:
            return None
        segment = connection.execute("SELECT started_at FROM timer_segments WHERE session_id=? AND ended_at IS NULL",
                                     (session["id"],)).fetchone()
        return {"id": session["id"], "task_id": session["task_id"], "status": session["status"],
                "timezone": session["timezone"], "elapsed_seconds": self.milliseconds(connection, session["id"], now) // 1000,
                "started_at": session["started_at"] / 1000,
                "segment_started_at": segment[0] / 1000 if segment else None}

    @staticmethod
    def open_segment(connection, session_id, now):
        connection.execute("INSERT INTO timer_segments(id,session_id,started_at) VALUES (?,?,?)",
                           (identifier("g_"), session_id, now))

    @staticmethod
    def close_segment(connection, session_id, now):
        segment = connection.execute("SELECT * FROM timer_segments WHERE session_id=? AND ended_at IS NULL",
                                     (session_id,)).fetchone()
        if segment:
            if now < segment["started_at"]:
                raise DomainError("Время компьютера изменилось назад. Исправьте часы перед завершением таймера.", 409)
            connection.execute("UPDATE timer_segments SET ended_at=? WHERE id=?", (now, segment["id"]))

    def start(self, connection, payload, now):
        task = self.tasks.task(connection, payload.get("task_id"))
        existing = self.current(connection)
        if existing:
            if existing["task_id"] == task["id"]:
                return self.view(connection, existing, now)
            raise DomainError("Уже есть незавершенный таймер. Сначала завершите его.", 409)
        session_id = identifier("s_")
        connection.execute("INSERT INTO timer_sessions(id,task_id,status,started_at) VALUES (?,?,'running',?)",
                           (session_id, task["id"], now))
        self.open_segment(connection, session_id, now)
        return self.view(connection, self.session(connection, session_id), now)

    def pause(self, connection, session_id, now):
        row = self.session(connection, session_id)
        if row["status"] == "finished":
            raise DomainError("Таймер уже завершен.", 409)
        if row["status"] == "running":
            self.close_segment(connection, session_id, now)
            connection.execute("UPDATE timer_sessions SET status='paused' WHERE id=?", (session_id,))
        return self.view(connection, self.session(connection, session_id), now)

    def resume(self, connection, session_id, now):
        row = self.session(connection, session_id)
        if row["status"] == "finished":
            raise DomainError("Таймер уже завершен.", 409)
        if row["status"] == "paused":
            self.open_segment(connection, session_id, now)
            connection.execute("UPDATE timer_sessions SET status='running' WHERE id=?", (session_id,))
        return self.view(connection, self.session(connection, session_id), now)

    @staticmethod
    def split_seconds(segments):
        allocations = {}
        for segment in segments:
            cursor, end = segment["started_at"], segment["ended_at"]
            while cursor < end:
                local = datetime.fromtimestamp(cursor / 1000, timezone.utc).astimezone(MOSCOW)
                next_day = local.date() + timedelta(days=1)
                midnight = datetime.combine(next_day, datetime_time(), MOSCOW)
                boundary = min(end, round(midnight.timestamp() * 1000))
                day = local.date().isoformat()
                allocations[day] = allocations.get(day, 0) + boundary - cursor
                cursor = boundary
        result = {day: milliseconds // 1000 for day, milliseconds in allocations.items()}
        # Round once for the whole session; preserve its exact integer-second total.
        remaining = sum(allocations.values()) // 1000 - sum(result.values())
        for day in sorted(allocations, key=lambda d: (-(allocations[d] % 1000), d))[:remaining]:
            result[day] += 1
        return {day: seconds for day, seconds in result.items() if seconds > 0}

    def stop(self, connection, session_id, now):
        row = self.session(connection, session_id)
        if row["status"] == "finished":
            return {"session_id": session_id, "entries": [self.entries.view(e, connection) for e in connection.execute(
                "SELECT * FROM time_entries WHERE session_id=? ORDER BY work_date", (session_id,))], "already_finished": True}
        if self.milliseconds(connection, session_id, now) < 1000:
            raise DomainError("Прошло меньше секунды. Дождитесь 1 секунды работы и завершите таймер.")
        self.close_segment(connection, session_id, now)
        segments = connection.execute("SELECT * FROM timer_segments WHERE session_id=? ORDER BY started_at", (session_id,)).fetchall()
        allocations = self.split_seconds(segments)
        for day, seconds in allocations.items():
            connection.execute(
                "INSERT INTO time_entries(id,task_id,work_date,duration_seconds,source,session_id,created_at,updated_at) "
                "VALUES (?,?,?,?,'timer',?,?,?)",
                (identifier("e_"), row["task_id"], day, seconds, session_id, now, now))
        connection.execute("UPDATE timer_sessions SET status='finished',finished_at=? WHERE id=?", (now, session_id))
        return {"session_id": session_id, "entries": [self.entries.view(e, connection) for e in connection.execute(
            "SELECT * FROM time_entries WHERE session_id=? ORDER BY work_date", (session_id,))], "already_finished": False}


class SummaryService:
    @staticmethod
    def summarize(entries):
        days, projects = {}, {}
        for entry in entries:
            days[entry["work_date"]] = days.get(entry["work_date"], 0) + entry["duration_seconds"]
            if "project" in entry:
                projects[entry["project"]] = projects.get(entry["project"], 0) + entry["duration_seconds"]
        return {"total_seconds": sum(days.values()), "days": days, "projects": projects}


class Tracker:
    def __init__(self, data_dir, clock=None):
        self.storage = Storage(data_dir)
        self.clock = clock or time.time
        self.tasks = ProjectTaskService()
        self.entries = TimeEntryService(self.tasks)
        self.timers = TimerService(self.tasks, self.entries)
        self.summaries = SummaryService()

    def now(self):
        value = self.clock()
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise DomainError("Невозможно прочитать часы компьютера.", 500)
        return round(value * 1000)

    @staticmethod
    def payload(value):
        if not isinstance(value, dict):
            raise DomainError("Ожидается объект с полями запроса.")
        return value

    def state(self, from_date=None, to_date=None):
        now = self.now()
        local_day = datetime.fromtimestamp(now / 1000, timezone.utc).astimezone(MOSCOW).date()
        if from_date is None and to_date is None:
            week_start = local_day - timedelta(days=local_day.weekday())
            from_date, to_date = week_start.isoformat(), (week_start + timedelta(days=6)).isoformat()
        elif from_date is None or to_date is None:
            raise DomainError("Укажите обе границы периода: начало и окончание.")
        start, end = work_date(from_date), work_date(to_date)
        if start > end:
            raise DomainError("Начало периода должно быть не позже окончания.")
        with self.storage.transaction() as connection:
            entries = [self.entries.view(e, connection) for e in connection.execute(
                "SELECT * FROM time_entries WHERE work_date>=? AND work_date<=? AND deleted_at IS NULL "
                "ORDER BY work_date,created_at,id", (start, end))]
            tasks = [self.tasks.view(connection, t) for t in connection.execute("SELECT * FROM tasks ORDER BY created_at,id")]
            return {"projects": [dict(p) for p in connection.execute("SELECT * FROM projects ORDER BY rowid")],
                    "tasks": tasks, "entries": entries,
                    "timer": self.timers.view(connection, self.timers.current(connection), now),
                    "server_now": now / 1000, "timezone": "Europe/Moscow",
                    "today": local_day.isoformat(), "from_date": start, "to_date": end}

    def create_task(self, payload):
        payload = self.payload(payload)
        with self.storage.transaction(True) as connection:
            return self.tasks.view(connection, self.tasks.ensure(connection, payload, self.now()))

    def update_task(self, task_id, payload):
        payload = self.payload(payload)
        with self.storage.transaction(True) as connection:
            old = self.tasks.task(connection, task_id, True)
            if "project" in payload and payload["project"] != old["project"]:
                raise DomainError("Проект существующей задачи менять нельзя. Создайте задачу в другом проекте.")
            title = text(payload.get("title", old["title"]), "Задача")
            description = text(payload.get("description", old["description"]), "Описание", 2000, False)
            archived = payload.get("archived", bool(old["archived"]))
            if type(archived) is not bool:
                raise DomainError("Признак архива должен быть true или false.")
            if archived and connection.execute("SELECT 1 FROM timer_sessions WHERE task_id=? AND status!='finished'", (task_id,)).fetchone():
                raise DomainError("Сначала завершите таймер этой задачи.", 409)
            if not archived:
                for row in connection.execute("SELECT id,title FROM tasks WHERE project=? AND archived=0 AND id!=?", (old["project"], task_id)):
                    if row["title"].casefold() == title.casefold():
                        raise DomainError("В этом проекте уже есть задача с таким названием.", 409)
            connection.execute("UPDATE tasks SET title=?,description=?,archived=? WHERE id=?",
                               (title, description, int(archived), task_id))
            return self.tasks.view(connection, self.tasks.task(connection, task_id, True))

    def create_entry(self, payload):
        payload = self.payload(payload)
        with self.storage.transaction(True) as connection:
            return self.entries.create(connection, payload, self.now())

    def update_entry(self, entry_id, payload):
        payload = self.payload(payload)
        with self.storage.transaction(True) as connection:
            return self.entries.update(connection, entry_id, payload, self.now())

    def delete_entry(self, entry_id):
        with self.storage.transaction(True) as connection:
            old = self.entries.entry(connection, entry_id, True)
            now = self.now()
            if old["manual_group_id"]:
                connection.execute("UPDATE manual_groups SET deleted_at=COALESCE(deleted_at,?),updated_at=? WHERE id=?",
                                   (now, now, old["manual_group_id"]))
                connection.execute("UPDATE time_entries SET deleted_at=COALESCE(deleted_at,?),updated_at=? WHERE manual_group_id=?",
                                   (now, now, old["manual_group_id"]))
            else:
                connection.execute("UPDATE time_entries SET deleted_at=COALESCE(deleted_at,?),updated_at=? WHERE id=?",
                                   (now, now, entry_id))
        return {"deleted": entry_id}

    def restore_entry(self, entry_id):
        with self.storage.transaction(True) as connection:
            old = self.entries.entry(connection, entry_id, True)
            now = self.now()
            if old["manual_group_id"]:
                connection.execute("UPDATE manual_groups SET deleted_at=NULL,updated_at=? WHERE id=?", (now, old["manual_group_id"]))
                connection.execute("UPDATE time_entries SET deleted_at=NULL,updated_at=? WHERE manual_group_id=?", (now, old["manual_group_id"]))
                return self.entries.group_result(connection, old["manual_group_id"], entry_id)
            connection.execute("UPDATE time_entries SET deleted_at=NULL,updated_at=? WHERE id=?", (now, entry_id))
            return self.entries.view(self.entries.entry(connection, entry_id), connection)

    def start_timer(self, payload):
        payload = self.payload(payload)
        with self.storage.transaction(True) as connection:
            return self.timers.start(connection, payload, self.now())

    def pause_timer(self, session_id):
        with self.storage.transaction(True) as connection:
            return self.timers.pause(connection, session_id, self.now())

    def resume_timer(self, session_id):
        with self.storage.transaction(True) as connection:
            return self.timers.resume(connection, session_id, self.now())

    def stop_timer(self, session_id):
        with self.storage.transaction(True) as connection:
            return self.timers.stop(connection, session_id, self.now())

    def list_backups(self):
        return self.storage.list_backups()

    def create_backup(self):
        return self.storage.create_backup()

    def restore_backup(self, name):
        try:
            return self.storage.restore_backup(name)
        except (ValueError, sqlite3.DatabaseError) as error:
            raise DomainError(str(error)) from None

    def backup_path(self, name):
        try:
            return self.storage.backup_path(name)
        except ValueError as error:
            raise DomainError(str(error), 404) from None
