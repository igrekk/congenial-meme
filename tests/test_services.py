import concurrent.futures
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend import DomainError, Tracker


class Clock:
    def __init__(self, timestamp=1795006800):
        self.timestamp = timestamp

    def __call__(self):
        return self.timestamp

    def advance(self, seconds):
        self.timestamp += seconds


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.tracker = Tracker(self.temp.name, self.clock)
        self.task = self.tracker.create_task({"project": "INAL", "title": "Документация"})

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        return self.tracker.state("2026-01-01", "2026-12-31")

    def manual(self, **changes):
        payload = {"task_id": self.task["id"], "work_date": "2026-11-18", "duration_seconds": 9000}
        payload.update(changes)
        return self.tracker.create_entry(payload)

    def test_manual_is_durable_and_keeps_exact_seconds(self):
        entry = self.manual(note="Проверка")
        reopened = Tracker(self.temp.name, self.clock)
        data = reopened.state("2026-11-18", "2026-11-18")
        self.assertEqual(data["entries"][0]["id"], entry["id"])
        self.assertEqual(data["entries"][0]["duration_seconds"], 9000)
        self.assertEqual(data["tasks"][0]["total_seconds"], 9000)

    def test_initial_state_defaults_to_current_moscow_week(self):
        self.clock.timestamp = datetime(2026, 11, 15, 22, 0, tzinfo=timezone.utc).timestamp()
        self.manual(work_date="2026-11-15", duration_seconds=60)
        self.manual(work_date="2026-11-16", duration_seconds=120)
        self.manual(work_date="2026-11-22", duration_seconds=180)
        self.manual(work_date="2026-11-23", duration_seconds=240)
        data = self.tracker.state()
        self.assertEqual(data["today"], "2026-11-16")
        self.assertEqual((data["from_date"], data["to_date"]), ("2026-11-16", "2026-11-22"))
        self.assertEqual([entry["duration_seconds"] for entry in data["entries"]], [120, 180])
        with self.assertRaises(DomainError):
            self.tracker.state("2026-11-16")
        with self.assertRaises(DomainError):
            self.tracker.state(to_date="2026-11-22")

    def test_manual_request_retry_does_not_duplicate(self):
        first = self.manual(request_id="req-1")
        self.assertEqual(first, self.manual(request_id="req-1"))
        self.assertEqual(len(self.state()["entries"]), 1)
        with self.assertRaises(DomainError):
            self.manual(request_id="req-1", duration_seconds=100)

    def test_validation(self):
        for value in (0, -1, 86401, True, 2.5, "9000", None):
            with self.subTest(value=value), self.assertRaises(DomainError):
                self.manual(duration_seconds=value)
        for value in ("2026-02-30", "20261118", "x", None):
            with self.subTest(value=value), self.assertRaises(DomainError):
                self.manual(work_date=value)
        with self.assertRaises(DomainError):
            self.tracker.create_task({"project": "OTHER", "title": "x"})
        with self.assertRaises(DomainError):
            self.tracker.create_task({"project": "INAL", "title": " "})

    def test_edit_delete_and_restore_recalculate_totals(self):
        entry = self.manual()
        updated = self.tracker.update_entry(entry["id"], {"project": "INAC", "title": "Проверка", "duration_seconds": 1200})
        self.assertNotEqual(updated["task_id"], self.task["id"])
        original_task = next(task for task in self.state()["tasks"] if task["id"] == self.task["id"])
        self.assertEqual(original_task["total_seconds"], 0)
        self.tracker.delete_entry(entry["id"])
        self.assertEqual(self.state()["entries"], [])
        self.tracker.restore_entry(entry["id"])
        self.assertEqual(self.state()["entries"][0]["duration_seconds"], 1200)

    def test_archiving_keeps_history_and_blocks_new_work(self):
        self.manual()
        self.tracker.update_task(self.task["id"], {"archived": True})
        self.assertEqual(len(self.state()["entries"]), 1)
        with self.assertRaises(DomainError):
            self.tracker.start_timer({"task_id": self.task["id"]})
        with self.assertRaises(DomainError):
            self.manual()
        self.tracker.update_task(self.task["id"], {"archived": False, "title": "Новое название"})
        self.assertEqual(self.state()["tasks"][0]["title"], "Новое название")

    def test_pause_resume_and_repeated_stop(self):
        timer = self.tracker.start_timer({"task_id": self.task["id"]})
        self.clock.advance(600)
        self.tracker.pause_timer(timer["id"])
        self.clock.advance(300)
        self.assertEqual(self.state()["timer"]["elapsed_seconds"], 600)
        self.tracker.resume_timer(timer["id"])
        self.clock.advance(300)
        stopped = self.tracker.stop_timer(timer["id"])
        again = self.tracker.stop_timer(timer["id"])
        self.assertEqual(stopped["entries"], again["entries"])
        self.assertEqual(self.state()["entries"][0]["duration_seconds"], 900)
        self.assertIsNone(self.state()["timer"])

    def test_midnight_split_uses_moscow_dates(self):
        self.clock.timestamp = datetime(2026, 11, 18, 20, 50, tzinfo=timezone.utc).timestamp()
        timer = self.tracker.start_timer({"task_id": self.task["id"]})
        self.clock.advance(1200)
        rows = self.tracker.stop_timer(timer["id"])["entries"]
        self.assertEqual([(r["work_date"], r["duration_seconds"]) for r in rows],
                         [("2026-11-18", 600), ("2026-11-19", 600)])

    def test_fractional_segments_are_rounded_once(self):
        timer = self.tracker.start_timer({"task_id": self.task["id"]})
        self.clock.advance(0.6)
        self.tracker.pause_timer(timer["id"])
        self.clock.advance(30)
        self.tracker.resume_timer(timer["id"])
        self.clock.advance(0.6)
        rows = self.tracker.stop_timer(timer["id"])["entries"]
        self.assertEqual(sum(row["duration_seconds"] for row in rows), 1)

    def test_fractional_midnight_parts_preserve_session_sum(self):
        self.clock.timestamp = datetime(2026, 11, 18, 20, 59, 59, 400000, tzinfo=timezone.utc).timestamp()
        timer = self.tracker.start_timer({"task_id": self.task["id"]})
        self.clock.advance(1.2)
        rows = self.tracker.stop_timer(timer["id"])["entries"]
        self.assertEqual(sum(row["duration_seconds"] for row in rows), 1)

    def test_short_timer_cannot_create_invented_time(self):
        timer = self.tracker.start_timer({"task_id": self.task["id"]})
        self.clock.advance(0.1)
        with self.assertRaises(DomainError):
            self.tracker.stop_timer(timer["id"])
        self.assertEqual(self.state()["entries"], [])
        self.assertEqual(self.state()["timer"]["status"], "running")

    def test_timer_restores_running_and_paused(self):
        timer = self.tracker.start_timer({"task_id": self.task["id"]})
        self.clock.advance(123)
        reopened = Tracker(self.temp.name, self.clock)
        self.assertEqual(reopened.state("2026-01-01", "2026-12-31")["timer"]["elapsed_seconds"], 123)
        reopened.pause_timer(timer["id"])
        self.clock.advance(30)
        again = Tracker(self.temp.name, self.clock)
        self.assertEqual(again.state("2026-01-01", "2026-12-31")["timer"]["status"], "paused")
        self.assertEqual(again.state("2026-01-01", "2026-12-31")["timer"]["elapsed_seconds"], 123)

    def test_two_windows_cannot_start_two_timers(self):
        second = self.tracker.create_task({"project": "GCVU", "title": "Модель"})
        other = Tracker(self.temp.name, self.clock)
        def start(tracker, task_id):
            try:
                return tracker.start_timer({"task_id": task_id})
            except DomainError as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda pair: start(*pair),
                                        [(self.tracker, self.task["id"]), (other, second["id"])]))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn(409, results)

    def test_backup_restore_and_safety_copy(self):
        original = self.manual()
        backup = self.tracker.create_backup()
        self.manual(duration_seconds=100)
        self.assertEqual(len(self.state()["entries"]), 2)
        result = self.tracker.restore_backup(backup["name"])
        self.assertEqual(self.state()["entries"][0]["id"], original["id"])
        self.assertEqual(len(self.state()["entries"]), 1)
        self.assertTrue(self.tracker.backup_path(result["safety_backup"]).is_file())
        self.tracker.restore_backup(result["safety_backup"])
        self.assertEqual(len(self.state()["entries"]), 2)

    def test_restore_rejects_bad_files_traversal_and_active_timer(self):
        backup = self.tracker.create_backup()
        for name in ("../tracker.sqlite3", "missing.sqlite3", "..\\tracker.sqlite3"):
            with self.subTest(name=name), self.assertRaises(DomainError):
                self.tracker.restore_backup(name)
        malformed = Path(self.temp.name) / "backups" / "broken.sqlite3"
        malformed.write_bytes(b"not a database")
        with self.assertRaises(DomainError):
            self.tracker.restore_backup(malformed.name)
        self.tracker.start_timer({"task_id": self.task["id"]})
        with self.assertRaises(DomainError):
            self.tracker.restore_backup(backup["name"])
        self.assertIsNotNone(self.state()["timer"])

    def test_stop_does_not_resurrect_deleted_entry(self):
        timer = self.tracker.start_timer({"task_id": self.task["id"]})
        self.clock.advance(10)
        row = self.tracker.stop_timer(timer["id"])["entries"][0]
        self.tracker.delete_entry(row["id"])
        self.tracker.stop_timer(timer["id"])
        self.assertEqual(self.state()["entries"], [])


if __name__ == "__main__":
    unittest.main()
