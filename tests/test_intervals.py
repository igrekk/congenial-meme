from contextlib import closing
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import unittest

from backend import DomainError, Tracker
from backend.storage import APPLICATION_ID, SCHEMA, SCHEMA_VERSION


class ManualIntervalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tracker = Tracker(self.temp.name)
        self.task = self.tracker.create_task({"project": "INAL", "title": "Документация"})

    def tearDown(self):
        self.temp.cleanup()

    def interval(self, **changes):
        payload = {"task_id": self.task["id"], "started_at": "2026-11-20T09:00",
                   "ended_at": "2026-11-20T11:30", "interval_mode": "bounds"}
        payload.update(changes)
        return self.tracker.create_entry(payload)

    def state(self):
        return self.tracker.state("2026-01-01", "2027-12-31")

    def test_bounds_are_calculated_and_remain_after_reopen(self):
        created = self.interval(note="Проверено")
        self.assertEqual(created["duration_seconds"], 9000)
        self.assertEqual(created["group_duration_seconds"], 9000)
        self.assertEqual(created["started_at"], "2026-11-20T09:00:00")
        self.assertEqual(created["ended_at"], "2026-11-20T11:30:00")
        self.assertEqual(len(created["entries"]), 1)
        reopened = Tracker(self.temp.name).state("2026-11-20", "2026-11-20")
        self.assertEqual(reopened["entries"][0]["manual_group_id"], created["manual_group_id"])
        self.assertEqual(reopened["entries"][0]["note"], "Проверено")

    def test_duration_crosses_month_and_splits_exact_calendar_days(self):
        created = self.interval(started_at="2026-11-30T23:30", ended_at=None,
                                duration_seconds=9000, interval_mode="duration")
        self.assertEqual(created["ended_at"], "2026-12-01T02:00:00")
        self.assertEqual([(e["work_date"], e["duration_seconds"]) for e in created["entries"]],
                         [("2026-11-30", 1800), ("2026-12-01", 7200)])
        self.assertTrue(all(e["group_duration_seconds"] == 9000 for e in created["entries"]))
        self.assertEqual(sum(e["duration_seconds"] for e in self.state()["entries"]), 9000)

    def test_leap_day_and_exact_midnight_do_not_create_empty_rows(self):
        created = self.interval(started_at="2028-02-28T23:00", ended_at="2028-03-01T00:00")
        self.assertEqual([(e["work_date"], e["duration_seconds"]) for e in created["entries"]],
                         [("2028-02-28", 3600), ("2028-02-29", 86400)])

    def test_request_retry_does_not_duplicate_interval_or_resurrect_deleted_parts(self):
        first = self.interval(started_at="2026-11-20T23:00", ended_at="2026-11-21T01:00", request_id="interval-retry")
        second = self.interval(started_at="2026-11-20T23:00", ended_at="2026-11-21T01:00", request_id="interval-retry")
        self.assertEqual(first, second)
        self.assertEqual(len(self.state()["entries"]), 2)
        self.tracker.delete_entry(first["id"])
        self.interval(started_at="2026-11-20T23:00", ended_at="2026-11-21T01:00", request_id="interval-retry")
        self.assertEqual(self.state()["entries"], [])
        with self.assertRaises(DomainError):
            self.interval(request_id="interval-retry")

    def test_editing_any_part_replaces_the_entire_group_and_keeps_clicked_id(self):
        created = self.interval(started_at="2026-11-30T23:30", ended_at="2026-12-01T02:00", request_id="edit-group")
        clicked = created["entries"][1]
        changed = self.tracker.update_entry(clicked["id"], {"started_at": "2026-12-02T09:00",
                                                          "ended_at": "2026-12-02T12:00", "interval_mode": "bounds"})
        entries = self.state()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(changed["id"], clicked["id"])
        self.assertEqual(entries[0]["work_date"], "2026-12-02")
        self.assertEqual(entries[0]["group_duration_seconds"], 10800)
        self.assertEqual(entries[0]["manual_group_id"], created["manual_group_id"])

    def test_delete_and_undo_apply_to_all_parts(self):
        created = self.interval(started_at="2026-11-30T23:30", ended_at="2026-12-01T02:00")
        clicked = created["entries"][1]["id"]
        self.tracker.delete_entry(clicked)
        self.assertEqual(self.state()["entries"], [])
        restored = self.tracker.restore_entry(clicked)
        self.assertEqual(len(restored["entries"]), 2)
        self.assertEqual(sum(e["duration_seconds"] for e in self.state()["entries"]), 9000)

    def test_legacy_entries_keep_unknown_times_and_can_be_converted_both_ways(self):
        legacy = self.tracker.create_entry({"task_id": self.task["id"], "work_date": "2026-11-20", "duration_seconds": 3600,
                                           "request_id": "legacy-request"})
        self.assertIsNone(legacy["started_at"])
        self.assertIsNone(legacy["manual_group_id"])
        converted = self.tracker.update_entry(legacy["id"], {"started_at": "2026-11-20T23:30", "duration_seconds": 7200,
                                                            "interval_mode": "duration"})
        self.assertEqual(len(converted["entries"]), 2)
        returned = self.tracker.update_entry(legacy["id"], {"work_date": "2026-11-21", "duration_seconds": 9000,
                                                           "interval_mode": "duration_only"})
        self.assertEqual(len(returned["entries"]), 1)
        self.assertIsNone(returned["started_at"])
        self.assertEqual(returned["work_date"], "2026-11-21")
        self.assertEqual(len(self.state()["entries"]), 1)

    def test_invalid_boundaries_duration_and_conflicting_inputs_are_rejected(self):
        payloads = [
            {"ended_at": "2026-11-20T08:00"},
            {"ended_at": "2026-11-20T09:00"},
            {"started_at": "2026-02-30T10:00"},
            {"started_at": "2026-11-20T25:00"},
            {"started_at": "2026-11-20T09:00+03:00"},
            {"duration_seconds": 3600},
            {"ended_at": None, "duration_seconds": 0, "interval_mode": "duration"},
            {"ended_at": None, "duration_seconds": 2678401, "interval_mode": "duration"},
            {"started_at": "9999-12-31T23:00", "ended_at": None, "duration_seconds": 7200, "interval_mode": "duration"},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(DomainError):
                self.interval(**payload)
        self.assertEqual(self.state()["entries"], [])


def create_v1_database(path, timer=False):
    schema = re.sub(r"CREATE TABLE IF NOT EXISTS manual_groups \([\s\S]*?\);\n", "", SCHEMA)
    schema = schema.replace("  manual_group_id TEXT REFERENCES manual_groups(id),\n", "")
    connection = sqlite3.connect(path)
    connection.executescript(schema)
    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
    connection.execute("PRAGMA user_version=1")
    connection.executemany("INSERT INTO projects VALUES (?,?)", [(p, p) for p in ("INAL", "INAC", "GCVU", "ECGP")])
    connection.execute("INSERT INTO tasks(id,project,title,created_at) VALUES ('old-task','INAL','Старая задача',0)")
    connection.execute("INSERT INTO time_entries(id,task_id,work_date,duration_seconds,source,created_at,updated_at) "
                       "VALUES ('old-entry','old-task','2026-11-18',9000,'manual',0,0)")
    if timer:
        connection.execute("INSERT INTO timer_sessions(id,task_id,status,started_at) VALUES ('old-timer','old-task','paused',1795006800000)")
        connection.execute("INSERT INTO timer_segments(id,session_id,started_at,ended_at) VALUES ('old-segment','old-timer',1795006800000,1795006810000)")
    connection.commit()
    connection.close()


class IntervalMigrationTests(unittest.TestCase):
    def test_v1_migration_preserves_records_and_paused_timer_and_takes_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracker.sqlite3"
            create_v1_database(path, timer=True)
            tracker = Tracker(directory)
            state = tracker.state("2026-11-18", "2026-11-18")
            self.assertEqual(state["entries"][0]["duration_seconds"], 9000)
            self.assertIsNone(state["entries"][0]["started_at"])
            self.assertEqual(state["timer"]["status"], "paused")
            self.assertEqual(state["timer"]["elapsed_seconds"], 10)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            backups = tracker.list_backups()
            self.assertEqual(len(backups), 1)
            self.assertTrue(backups[0]["name"].startswith("before-migration-v1-"))
            with closing(sqlite3.connect(tracker.backup_path(backups[0]["name"]))) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_old_backup_restores_and_upgrades_without_losing_current_safety_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = Tracker(directory)
            tracker.create_entry({"project": "ECGP", "title": "Новая работа", "started_at": "2026-11-20T10:00",
                                  "ended_at": "2026-11-20T12:00"})
            original = Path(directory) / "original-v1.sqlite3"
            create_v1_database(original)
            shutil.copyfile(original, tracker.storage.backups / "legacy.sqlite3")
            restored = tracker.restore_backup("legacy.sqlite3")
            self.assertTrue(tracker.backup_path(restored["safety_backup"]).is_file())
            state = tracker.state("2026-11-01", "2026-11-30")
            self.assertEqual(len(state["entries"]), 1)
            self.assertEqual(state["entries"][0]["id"], "old-entry")
            self.assertIsNone(state["entries"][0]["started_at"])
            tracker.create_entry({"task_id": "old-task", "started_at": "2026-11-21T09:00", "duration_seconds": 3600})
            self.assertEqual(len(tracker.state("2026-11-01", "2026-11-30")["entries"]), 2)
            with closing(sqlite3.connect(tracker.backup_path("legacy.sqlite3"))) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
