"""SQLite persistence and consistent, atomic local backups (stdlib only)."""

from contextlib import contextmanager
from pathlib import Path
import os
import re
import sqlite3
import threading
import time
import uuid


APPLICATION_ID = 0x57544B52
SCHEMA_VERSION = 2
_locks = {}
_locks_guard = threading.Lock()


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  code TEXT PRIMARY KEY CHECK(code IN ('INAL','INAC','GCVU','ECGP')),
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL REFERENCES projects(code),
  title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 160),
  description TEXT NOT NULL DEFAULT '',
  archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS timer_sessions (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  status TEXT NOT NULL CHECK(status IN ('running','paused','finished')),
  timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
  started_at INTEGER NOT NULL,
  finished_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS one_unfinished_timer
  ON timer_sessions ((1)) WHERE status IN ('running','paused');
CREATE TABLE IF NOT EXISTS timer_segments (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES timer_sessions(id),
  started_at INTEGER NOT NULL,
  ended_at INTEGER CHECK(ended_at IS NULL OR ended_at >= started_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_segment
  ON timer_segments(session_id) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS manual_groups (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  started_at TEXT,
  ended_at TEXT,
  duration_seconds INTEGER NOT NULL CHECK(duration_seconds BETWEEN 1 AND 2678400),
  note TEXT NOT NULL DEFAULT '',
  request_id TEXT UNIQUE,
  request_fingerprint TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  deleted_at INTEGER
);
CREATE TABLE IF NOT EXISTS time_entries (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  work_date TEXT NOT NULL,
  duration_seconds INTEGER NOT NULL CHECK(duration_seconds BETWEEN 1 AND 86400),
  source TEXT NOT NULL CHECK(source IN ('manual','timer')),
  note TEXT NOT NULL DEFAULT '',
  session_id TEXT REFERENCES timer_sessions(id),
  request_id TEXT UNIQUE,
  request_fingerprint TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  deleted_at INTEGER,
  manual_group_id TEXT REFERENCES manual_groups(id),
  UNIQUE(session_id,work_date)
);
CREATE INDEX IF NOT EXISTS entries_by_date ON time_entries(work_date);
CREATE INDEX IF NOT EXISTS entries_by_task ON time_entries(task_id);
"""


class Storage:
    def __init__(self, data_dir):
        self.directory = Path(data_dir).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "tracker.sqlite3"
        self.backups = self.directory / "backups"
        self.backups.mkdir(exist_ok=True)
        with _locks_guard:
            self.lock = _locks.setdefault(str(self.path).casefold(), threading.RLock())
        with self.lock:
            with self.connect() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                app_id = connection.execute("PRAGMA application_id").fetchone()[0]
                if version not in (0, 1, SCHEMA_VERSION) or app_id not in (0, APPLICATION_ID):
                    raise ValueError("Неподдерживаемая версия или формат базы данных.")
            if version == 1:
                self._backup_locked("before-migration-v1")
            with self.connect() as connection:
                self.initialize_connection(connection, version)

    @staticmethod
    def initialize_connection(connection, version):
        """Upgrade in one transaction; callers have already protected the old file."""
        migration = ""
        if version == 1:
            migration = "ALTER TABLE time_entries ADD COLUMN manual_group_id TEXT REFERENCES manual_groups(id);"
        sql = ("BEGIN IMMEDIATE;\n" + SCHEMA + "\n" + migration + "\n"
               "CREATE UNIQUE INDEX IF NOT EXISTS manual_group_day ON time_entries(manual_group_id,work_date) "
               "WHERE manual_group_id IS NOT NULL;\n"
               f"PRAGMA application_id={APPLICATION_ID};\nPRAGMA user_version={SCHEMA_VERSION};\n"
               "INSERT OR IGNORE INTO projects(code,name) VALUES "
               "('INAL','INAL'),('INAC','INAC'),('GCVU','GCVU'),('ECGP','ECGP');\nCOMMIT;")
        try:
            connection.executescript(sql)
        except BaseException:
            connection.rollback()
            raise

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, write=False):
        with self.lock:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise

    def backup_path(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.sqlite3", name):
            raise ValueError("Недопустимое имя резервной копии.")
        candidate = (self.backups / name).resolve()
        if candidate.parent != self.backups.resolve() or not candidate.is_file():
            raise ValueError("Резервная копия не найдена.")
        return candidate

    def describe_backup(self, path):
        stat = path.stat()
        return {"name": path.name, "size": stat.st_size, "created_at": stat.st_mtime}

    def list_backups(self):
        with self.lock:
            return sorted((self.describe_backup(p) for p in self.backups.glob("*.sqlite3")
                           if p.is_file() and p.resolve().parent == self.backups.resolve()),
                          key=lambda item: (item["created_at"], item["name"]), reverse=True)

    def _backup_locked(self, prefix="backup"):
        name = f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.sqlite3"
        target = self.backups / name
        try:
            with self.connect() as source:
                destination = sqlite3.connect(target)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return self.describe_backup(target)

    def create_backup(self):
        with self.lock:
            return self._backup_locked()

    @staticmethod
    def validate_backup(path):
        # Read-only URI prevents a malformed or missing file from being created.
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError("Файл не является резервной копией учета времени.")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (1, SCHEMA_VERSION):
                raise ValueError("Версия резервной копии не поддерживается.")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Резервная копия повреждена.")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Связи в резервной копии повреждены.")
            for sql in (
                "SELECT code,name FROM projects",
                "SELECT id,project,title,description,archived,created_at FROM tasks",
                "SELECT id,task_id,status,timezone,started_at,finished_at FROM timer_sessions",
                "SELECT id,session_id,started_at,ended_at FROM timer_segments",
                "SELECT id,task_id,work_date,duration_seconds,source,note,session_id,"
                "request_id,request_fingerprint,created_at,updated_at,deleted_at FROM time_entries",
            ):
                connection.execute(sql + " LIMIT 1").fetchone()
            if version >= 2:
                connection.execute("SELECT id,task_id,started_at,ended_at,duration_seconds,note,request_id,"
                                   "request_fingerprint,created_at,updated_at,deleted_at FROM manual_groups LIMIT 1").fetchone()
                connection.execute("SELECT manual_group_id FROM time_entries LIMIT 1").fetchone()
            required_indexes = {"one_unfinished_timer", "one_open_segment", "entries_by_date", "entries_by_task"}
            if version >= 2:
                required_indexes.add("manual_group_day")
            indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            if not required_indexes.issubset(indexes):
                raise ValueError("В резервной копии отсутствуют обязательные индексы.")
            codes = {row[0] for row in connection.execute("SELECT code FROM projects")}
            if codes != {"INAL", "INAC", "GCVU", "ECGP"}:
                raise ValueError("В резервной копии отсутствуют проекты.")
        finally:
            connection.close()

    def restore_backup(self, name):
        with self.lock:
            source_path = self.backup_path(name)
            self.validate_backup(source_path)
            with self.connect() as connection:
                if connection.execute("SELECT 1 FROM timer_sessions WHERE status != 'finished'").fetchone():
                    raise ValueError("Сначала завершите текущий таймер, затем восстановите копию.")
            safety = self._backup_locked("before-restore")
            temporary = self.directory / ("restore-" + uuid.uuid4().hex + ".sqlite3")
            try:
                source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
                destination = sqlite3.connect(temporary)
                try:
                    source.backup(destination)
                    version = destination.execute("PRAGMA user_version").fetchone()[0]
                    if version == 1:
                        destination.execute("PRAGMA foreign_keys=ON")
                        self.initialize_connection(destination, version)
                finally:
                    source.close()
                    destination.close()
                self.validate_backup(temporary)
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return {"restored": name, "safety_backup": safety["name"]}
