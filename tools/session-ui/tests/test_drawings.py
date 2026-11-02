"""Session-UI part drawings: job listing, binding rules, no duplicate start, no paths from the browser (offline)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))

import drawings  # noqa: E402
from adapter import AdapterError  # noqa: E402


class DrawingsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.session = self.root / "s1"
        (self.session / "drawings" / "specs").mkdir(parents=True)
        (self.session / "agent-session.json").write_text("{}", encoding="utf-8")
        (self.session / "drawings" / "specs" / "front-shell.json").write_text("{}", encoding="utf-8")

    def _job(self, name: str, state: str, status: str | None = None, pid: int | None = None) -> Path:
        job = self.session / "drawings" / "jobs" / name
        job.mkdir(parents=True)
        doc = {"state": state, "pid": pid or os.getpid(), "heartbeat_at": time.time(), "stage": "sam" if state == "running" else None,
               "stage_started_at": time.time(), "done": 7, "total": 7, "stage_s": {}}
        if status:
            doc["result"] = {"status": status, "reasons": ["r"], "spec": {"part": "front shell", "scope": "assembly"}}
        (job / "progress.json").write_text(json.dumps(doc), encoding="utf-8")
        (job / "drawing.svg").write_text("<svg/>", encoding="utf-8")
        return job

    def test_listing_shows_specs_jobs_and_files(self):
        self._job("front-shell-1", "done", "drawn_unconfirmed")
        view = drawings.list_drawings(self.root, "s1")
        self.assertEqual(view["specs"], ["front-shell"])
        self.assertEqual(view["jobs"][0]["status"], "drawn_unconfirmed")
        self.assertEqual(view["jobs"][0]["files"], ["drawing.svg"])

    def test_bind_records_once_and_never_writes_memory(self):
        job = self._job("front-shell-1", "done", "drawn_unconfirmed")
        first = drawings.bind(self.root, "s1", "front-shell-1", "confirm", "looks right")
        self.assertFalse(first["memory_written"])
        again = drawings.bind(self.root, "s1", "front-shell-1", "reject", "")
        self.assertTrue(again["already_recorded"])
        self.assertEqual(json.loads((job / "binding.json").read_text())["decision"], "confirm")
        self.assertFalse((self.session / "memory").exists())

    def test_needs_capture_review_and_failed_cannot_be_bound(self):
        for name, state, status in (("a", "done", "NEEDS_CAPTURE"), ("b", "done", "NEEDS_REVIEW"), ("c", "failed", None)):
            self._job(name, state, status)
            with self.assertRaises(AdapterError):
                drawings.bind(self.root, "s1", name, "confirm", "")

    def test_running_job_is_not_started_twice(self):
        self._job("front-shell-20261101-000000", "running")
        result = drawings.start_drawing(self.root, "s1", "front-shell")
        self.assertTrue(result["already_running"])

    def test_paths_are_refused(self):
        for bad in ("../s1", "a/b", "..", ""):
            with self.assertRaises(AdapterError):
                drawings.drawing_status(self.root, "s1", bad)
        self._job("j", "done", "drawn_unconfirmed")
        with self.assertRaises(AdapterError):
            drawings.drawing_bytes(self.root, "s1", "j", "progress.json")

    def test_only_paused_or_failed_jobs_resume(self):
        from unittest import mock
        self._job("done-job", "done", "drawn_unconfirmed")
        with self.assertRaises(AdapterError):
            drawings.resume_drawing(self.root, "s1", "done-job")
        self._job("paused-job", "paused")
        with mock.patch.object(drawings.job_control, "start") as start:
            self.assertEqual(drawings.resume_drawing(self.root, "s1", "paused-job")["state"], "resumed")
            start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
