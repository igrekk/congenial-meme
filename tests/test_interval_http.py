"""Check the interval contract through the real HTTP adapter."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from server import create_server


class IntervalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server(self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base + path, data=body, method=method,
                                        headers={"Content-Type": "application/json", "X-Time-Tracker": "1"})
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_bounds_are_saved_and_split_at_midnight(self):
        status, created = self.request("/api/time-entries", "POST", {
            "project": "INAL", "title": "HTTP interval", "interval_mode": "bounds",
            "started_at": "2026-11-30T23:30:00", "ended_at": "2026-12-01T02:00:00",
            "request_id": "http-interval-bounds",
        })
        self.assertEqual(status, 200, created)
        _, state = self.request("/api/state?from=2026-11-30&to=2026-12-01")
        self.assertEqual([e["duration_seconds"] for e in state["entries"]], [1800, 7200])
        self.assertTrue(all(e["started_at"] == "2026-11-30T23:30:00" for e in state["entries"]))
        self.assertTrue(all(e["ended_at"] == "2026-12-01T02:00:00" for e in state["entries"]))
        self.assertTrue(all(e["group_duration_seconds"] == 9000 for e in state["entries"]))

    def test_duration_automatically_sets_finish_and_rejects_invalid_finish(self):
        status, created = self.request("/api/time-entries", "POST", {
            "project": "ECGP", "title": "HTTP duration", "interval_mode": "duration",
            "started_at": "2026-12-31T23:00:00", "duration_seconds": 7200,
        })
        self.assertEqual(status, 200, created)
        _, state = self.request("/api/state?from=2026-12-31&to=2027-01-01")
        self.assertEqual([e["duration_seconds"] for e in state["entries"]], [3600, 3600])
        self.assertEqual(state["entries"][0]["ended_at"], "2027-01-01T01:00:00")
        status, error = self.request("/api/time-entries", "POST", {
            "project": "ECGP", "title": "Invalid interval", "interval_mode": "bounds",
            "started_at": "2026-11-20T12:00:00", "ended_at": "2026-11-20T11:00:00",
        })
        self.assertEqual(status, 400)
        self.assertIn("error", error)


if __name__ == "__main__":
    unittest.main()
