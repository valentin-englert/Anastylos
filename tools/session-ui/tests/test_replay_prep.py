"""Clips are made before anyone asks: one background worker, one clip at a time, ready clips open at once (fake extractor)."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dialogue_flow  # noqa: E402
import rectified_frames  # noqa: E402
import replay  # noqa: E402
import replay_prep  # noqa: E402
import replay_time  # noqa: E402
import server  # noqa: E402
from dialogue.executor import Executor  # noqa: E402
from test_replay import VIDEO_SHA, CellMomentTest, fake_worker, settle_document  # noqa: E402

CLIP_PARTS = ["dec-0001", "dec-0002", "dec-0003", "dec-0005", "dec-0007"]


class Gate:
    """Wraps the fake workers: records overlap, and can hold every call until released."""

    def __init__(self, inner, hold: bool = False, hold_first_clip: bool = False):
        self.inner, self.hold, self.hold_first_clip = inner, hold, hold_first_clip
        self.release, self.first_clip = threading.Event(), threading.Event()
        if not hold:
            self.release.set()
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.order: list[str] = []

    @property
    def calls(self):
        return self.inner.calls

    def __call__(self, command, **kwargs):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.order.append(Path(command[2]).name + (":" + command[command.index("--anchor-s") + 1] if "--anchor-s" in command else ""))
        try:
            if self.hold_first_clip and command[2].endswith("replay_worker.py") and not self.first_clip.is_set():
                self.first_clip.set()
                self.release.clear()
            self.release.wait(30)
            return self.inner(command, **kwargs)
        finally:
            with self.lock:
                self.active -= 1

    def names(self, worker: str) -> list[str]:
        return [o for o in self.order if o.startswith(worker)]


class PrepCase(CellMomentTest):
    def setUp(self):
        super().setUp()
        self.addCleanup(replay_prep.disable, self.root)

    def start(self, **kwargs) -> Gate:
        gate = Gate(fake_worker(settle=settle_document({"cell-1": {"settled_s": 100.0}})), **kwargs)
        replay_prep.enable(self.root, run=gate)
        return gate


class BackgroundTest(PrepCase):
    def test_a_kick_measures_the_cells_then_prepares_every_known_moment_one_at_a_time(self):
        gate = self.start()
        memory_before = self.memory.read_bytes()
        replay_prep.kick(self.root, "s1")
        self.assertTrue(replay_prep.wait_idle(self.root))
        self.assertEqual(len(gate.names("cell_settle_worker.py")), 1)
        self.assertEqual(len(gate.names("replay_worker.py")), len(CLIP_PARTS))
        self.assertEqual(gate.peak, 1)
        self.assertEqual(gate.order[0], "cell_settle_worker.py")  # the time is checked before any clip is made
        self.assertIn("replay_worker.py:100.0", gate.order)      # the part in cell-1 is made around its measured settle time
        moments = replay.replay_moments(self.root, "s1", CLIP_PARTS)["moments"]
        self.assertEqual([m["status"] for m in moments], ["ready"] * len(CLIP_PARTS))
        self.assertEqual(self.memory.read_bytes(), memory_before)

    def test_clips_that_exist_are_skipped_and_a_second_kick_does_nothing(self):
        gate = self.start()
        replay.replay_prepare(self.root, "s1", "dec-0005", run=gate)
        before = len(gate.names("replay_worker.py"))
        replay_prep.kick(self.root, "s1")
        replay_prep.wait_idle(self.root)
        self.assertEqual(len(gate.names("replay_worker.py")) - before, len(CLIP_PARTS) - 1)
        replay_prep.kick(self.root, "s1")
        replay_prep.wait_idle(self.root)
        self.assertEqual(len(gate.names("replay_worker.py")), before + len(CLIP_PARTS) - 1)

    def test_a_kick_returns_at_once_while_a_clip_is_being_made(self):
        gate = self.start(hold=True)
        began = time.monotonic()
        replay_prep.kick(self.root, "s1")
        replay_prep.kick(self.root, "s1")
        self.assertLess(time.monotonic() - began, 0.5)
        gate.release.set()
        self.assertTrue(replay_prep.wait_idle(self.root))

    def test_without_a_worker_a_kick_is_a_no_op(self):
        replay_prep.disable(self.root)
        replay_prep.kick(self.root, "s1")
        self.assertTrue(replay_prep.wait_idle(self.root, 0.1))

    def test_a_failed_clip_is_reported_and_the_queue_goes_on(self):
        inner = fake_worker(settle=settle_document({"cell-1": {"settled_s": 100.0}}))

        def run(command, **kwargs):
            if "--anchor-s" in command and command[command.index("--anchor-s") + 1] == "41.5":
                return type("R", (), {"returncode": 2, "stderr": "replay extraction failed: no frame registers\n", "stdout": ""})()
            return inner(command, **kwargs)

        replay_prep.enable(self.root, run=run)
        replay_prep.kick(self.root, "s1")
        replay_prep.wait_idle(self.root)
        moments = replay_prep.annotate(self.root, "s1", replay.replay_moments(self.root, "s1", CLIP_PARTS))["moments"]
        by_part = {m["part_id"]: m["prep"] for m in moments}
        self.assertEqual(by_part["dec-0005"]["state"], "failed")
        self.assertIn("no frame registers", by_part["dec-0005"]["error"])
        self.assertEqual({p: s["state"] for p, s in by_part.items() if p != "dec-0005"}, {p: "ready" for p in CLIP_PARTS if p != "dec-0005"})

    def test_a_half_written_clip_folder_is_never_served_and_is_replaced(self):
        gate = self.start()
        moment = self.moment("dec-0005")
        leftover = self.session_dir / "evidence" / "replay" / (moment["key"] + ".partial")
        leftover.mkdir(parents=True)
        (leftover / "seq-000.jpg").write_bytes(b"\xff\xd8half")
        self.assertEqual(self.moment("dec-0005")["status"], "not_prepared")
        with self.assertRaises(Exception):
            replay.replay_file(self.root, "s1", moment["key"] + ".partial", "seq-000.jpg")
        with self.assertRaises(Exception):
            replay.replay_file(self.root, "s1", moment["key"], "seq-000.jpg")
        self.assertEqual(replay_prep.open_clip(self.root, "s1", "dec-0005")["prep"]["state"] in ("queued", "preparing", "ready"), True)
        replay_prep.wait_idle(self.root)
        self.assertEqual(self.moment("dec-0005")["status"], "ready")
        self.assertEqual(gate.peak, 1)


class OpenClipTest(PrepCase):
    def test_a_ready_clip_opens_at_once(self):
        gate = self.start()
        replay.replay_prepare(self.root, "s1", "dec-0005", run=gate)
        calls = len(gate.calls)
        began = time.monotonic()
        result = replay_prep.open_clip(self.root, "s1", "dec-0005")
        self.assertLess(time.monotonic() - began, 0.5)
        self.assertEqual(result["status"], "ready")
        self.assertIn("player", result)
        self.assertEqual(len(gate.calls), calls)

    def test_a_clip_still_being_made_answers_preparing_and_moves_to_the_front(self):
        gate = self.start(hold=True)
        replay_prep.kick(self.root, "s1")
        deadline = time.monotonic() + 10
        while not gate.order and time.monotonic() < deadline:
            time.sleep(0.01)
        first = replay_prep.open_clip(self.root, "s1", "dec-0007")
        self.assertNotIn("player", first)
        self.assertIn(first["prep"]["state"], ("queued", "preparing"))
        gate.release.set()
        self.assertTrue(replay_prep.wait_idle(self.root))
        again = replay_prep.open_clip(self.root, "s1", "dec-0007")
        self.assertEqual((again["status"], again["prep"]["state"]), ("ready", "ready"))
        self.assertIn("player", again)

    def test_the_clip_that_was_asked_for_is_made_before_the_rest_of_the_queue(self):
        gate = self.start(hold_first_clip=True)
        replay_prep.kick(self.root, "s1")
        self.assertTrue(gate.first_clip.wait(10))  # the first clip is being made, the other four are queued
        replay_prep.open_clip(self.root, "s1", "dec-0007")
        gate.release.set()
        self.assertTrue(replay_prep.wait_idle(self.root))
        clips = [o for o in gate.order if o.startswith("replay_worker.py")]
        self.assertEqual(len(clips), len(CLIP_PARTS))
        self.assertEqual(clips[:2], ["replay_worker.py:100.0", "replay_worker.py:12.0"])  # dec-0007 jumped the queue

    def test_unprepared_listing_reports_prep_none_and_ready_ones_ready(self):
        self.start()
        listing = replay_prep.annotate(self.root, "s1", replay.replay_moments(self.root, "s1", ["dec-0005"]))
        self.assertEqual(listing["moments"][0]["prep"]["state"], "none")
        replay_prep.open_clip(self.root, "s1", "dec-0005")
        replay_prep.wait_idle(self.root)
        listing = replay_prep.annotate(self.root, "s1", replay.replay_moments(self.root, "s1", ["dec-0005"]))
        self.assertEqual(listing["moments"][0]["prep"]["state"], "ready")

    def test_without_a_background_worker_opening_prepares_synchronously(self):
        from unittest import mock
        gate = Gate(fake_worker(settle=settle_document({"cell-1": {"settled_s": 100.0}})))
        with mock.patch.object(replay.subprocess, "run", gate):
            result = replay_prep.open_clip(self.root, "s1", "dec-0005")
        self.assertIn("player", result)


class VoiceTurnNotBlockedTest(PrepCase):
    def test_a_dialogue_write_only_queues_the_clips(self):
        gate = self.start(hold=True)

        class Api:
            def question_next(self, root, sid):
                return {"done": True}

            def question_overview(self, root, sid):
                return {"questions": []}

        flow = dialogue_flow.DialogueFlow(self.root, "s1", Api(), executor=Executor(None))
        began = time.monotonic()
        flow._closing_finish()
        self.assertLess(time.monotonic() - began, 0.5)
        gate.release.set()
        self.assertTrue(replay_prep.wait_idle(self.root))
        self.assertEqual(len(gate.names("replay_worker.py")), len(CLIP_PARTS))


class RouteTest(PrepCase):
    def setUp(self):
        super().setUp()
        self.httpd = server.serve(self.root, "127.0.0.1", 0)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join, 30)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def call(self, path, body=None):
        import urllib.request
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                         method="GET" if body is None else "POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def test_opening_the_page_prepares_what_is_missing_and_the_list_shows_which_are_ready(self):
        gate = self.start()
        self.call("/api/focus", {"session": "s1", "job": None})  # the explosion page announces itself
        replay_prep.wait_idle(self.root)
        listing = self.call("/api/replay?session=s1&parts=" + ",".join(CLIP_PARTS))
        self.assertEqual([m["prep"]["state"] for m in listing["moments"]], ["ready"] * len(CLIP_PARTS))
        clip = self.call("/api/replay/prepare", {"session": "s1", "part": "dec-0005"})
        self.assertIn("player", clip)
        self.assertEqual(len(gate.names("replay_worker.py")), len(CLIP_PARTS))


class SheetFramesTest(unittest.TestCase):
    """The reader skips frames with grab() and only seeks when it has to (a seek costs about a hundred grabs)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        video = Path(tmp.name) / "clip.avi"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (16, 12))
        for i in range(300):
            writer.write(np.full((12, 16, 3), i % 250, np.uint8))
        writer.release()
        frames = rectified_frames.SheetFrames.__new__(rectified_frames.SheetFrames)
        frames.capture, frames.position = cv2.VideoCapture(str(video)), None
        self.seeks = []
        real = frames.capture

        class Counting:
            def set(self_inner, prop, value):
                self.seeks.append(value)
                return real.set(prop, value)

            def __getattr__(self_inner, name):
                return getattr(real, name)

        frames.capture = Counting()
        self.frames = frames
        self.addCleanup(real.release)

    def level(self, index: int) -> int:
        return int(round(float(self.frames._frame(index).mean())))

    def test_nearby_frames_are_grabbed_and_far_or_earlier_ones_seek(self):
        self.assertAlmostEqual(self.level(5), 5, delta=2)
        self.assertAlmostEqual(self.level(9), 9, delta=2)
        self.assertAlmostEqual(self.level(40), 40, delta=2)  # 30 frames ahead: grab
        self.assertEqual(self.seeks, [5])
        self.assertAlmostEqual(self.level(20), 20, delta=2)  # backwards: seek
        self.assertAlmostEqual(self.level(200), 200, delta=2)  # far ahead: seek
        self.assertEqual(self.seeks, [5, 20, 200])

    def test_a_frame_past_the_end_is_none(self):
        self.assertIsNone(self.frames._frame(299 + 50))


if __name__ == "__main__":
    unittest.main()
