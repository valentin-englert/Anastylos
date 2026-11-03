"""Evidence replay: what t_s means, cached frame folders, whitelist serving, worker frame choice (fake extractor, no real video)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))

import assembly_help  # noqa: E402
import replay  # noqa: E402
import replay_time  # noqa: E402
import server  # noqa: E402
from adapter import AdapterError  # noqa: E402
from state import SessionState  # noqa: E402

SESSION = "memory"
VIDEO_SHA = "ab" * 32


def removal(event_id: str, label: str, source: str | None, t_s: float | None, note: str, supersedes: str | None = None) -> dict:
    return {"type": "declared_removal", "id": event_id, "session": SESSION, "timestamp": "2026-11-02T18:22:42Z",
            "operator_label": label, "source": source, "phase": "phase-0001", "t_s": t_s, "observation_id": None,
            "declared_by": "workstation", "evidence_note": note, "supersedes": supersedes}


QUESTIONS = [
    {"id": "Q-P1-01", "kind": "source_of_part", "part": "P1-C01", "cell": "cell-1", "deposit_window_s": [22.2, 60.0],
     "candidates": [{"removal_event": "E-0007", "window_s": [30.0, 34.5], "sources": []}]},
    {"id": "Q-P1-02", "kind": "source_of_part", "part": "P1-C02", "cell": "cell-2", "deposit_window_s": [70.0, 72.0],
     "candidates": [{"removal_event": "E-0007", "window_s": [30.0, 34.5], "sources": []}]},
    {"id": "Q-P1-03", "kind": "destination_of_removal", "removal_event": "E-0009", "window_s": [90.0, 95.0], "sources": []},
]
EVENTS = [
    {"type": "phase", "id": "phase-0001", "session": SESSION, "timestamp": "2026-11-02T16:42:41Z", "label": "recorded on the phone",
     "pose_note": None, "starts_after": None, "supersedes": None},
    removal("dec-0001", "screw", "back shell", 22.2, "answer to Q-P1-01; live op_key=x; operator input typed"),
    removal("dec-0002", "button", "automatic candidate E-0007 (SRC-1), chosen by the operator", 30.0,
            "answer to Q-P1-02; live op_key=x"),
    removal("dec-0003", "foil", "front shell", 90.0, "answer to Q-P1-03; live op_key=x"),
    removal("dec-0004", "back shell", None, None, "operator-stated at the end of the video, not observed"),
    removal("dec-0005", "rubber", "front shell", 41.5, "operator-added part that raised no question; replayed"),
    removal("dec-0006", "membrane", "front shell", 12.0, "typed"),
    removal("dec-0007", "white rubber membrane", "L/R button", None, "operator correction", supersedes="dec-0006"),
]


def settle_document(cells: dict, status: str = "ok", reason: str = "") -> dict:
    return {"schema": replay_time.SETTLE_SCHEMA, "video": {"sha256": VIDEO_SHA}, "reference": {"status": status, "reason": reason},
            "cells": {name: {"reference_ok": True, "occupied": True, "settled_s": 100.0, **v} for name, v in cells.items()}}


def fake_worker(status: str = "ready", frames: int = 4, returncode: int = 0, settle: dict | None = None):
    """A stand-in for the two workers: writes what they would write (JPEG-looking bytes, the cell measurement)."""
    calls = []

    def run(command, **kwargs):
        calls.append({"command": command, "env": kwargs.get("env")})
        if command[2].endswith("cell_settle_worker.py"):
            out = Path(command[command.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(settle or settle_document({})), encoding="utf-8")
            return SimpleNamespace(returncode=0, stderr="", stdout="{}")
        if returncode:
            return SimpleNamespace(returncode=returncode, stderr="replay extraction failed: the video cannot be read\n", stdout="")
        args = dict(zip(command[3::2], command[4::2]))
        out = Path(args["--out"])
        out.mkdir(parents=True)
        start, end, anchor = float(args["--start-s"]), float(args["--end-s"]), float(args["--anchor-s"])
        count = frames if status == "ready" else 0
        kept = [{"file": f"seq-{i:03d}.jpg", "t_s": round(start + i * (end - start) / max(count - 1, 1), 3), "frame_index": i,
                 "skin": 0.0} for i in range(count)]
        for item in kept:
            (out / item["file"]).write_bytes(b"\xff\xd8" + item["file"].encode())
        picks = {"before": None, "after": None}
        for name, at in (("before", anchor - 0.5), ("after", anchor + 0.5)):
            if status == "ready":
                (out / f"{name}.jpg").write_bytes(b"\xff\xd8" + name.encode())
                picks[name] = {"file": f"{name}.jpg", "t_s": at, "frame_index": 1, "skin": 0.0, "low_skin": name == "before"}
        manifest = {"schema": "anastylos-replay-evidence@2", "status": status, "start_s": start, "end_s": end, "anchor_s": anchor,
                    "rate_fps": 2.5, "px_per_mm": 3.0, "video": {"name": "video.mp4", "sha256": args["--video-sha256"]},
                    "frames": kept, "skipped": [{"t_s": 1.0, "frame_index": 3, "reason": "rms 1.3 mm > 0.5 mm"}],
                    "before": picks["before"], "after": picks["after"], "generated_at": "2026-11-02T20:00:00"}
        (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="{}")

    run.calls = calls
    return run


class ReplayCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "workspace"
        self.session_dir = self.root / "s1"
        state = SessionState.create(self.session_dir, "controller", "workstation")
        self.video = Path(tmp.name) / "video.mp4"
        self.video.write_bytes(b"not a real video")
        state.begin_phase("P1", "recorded on the phone", self.video, VIDEO_SHA)
        state.finish_phase("P1", {"parts": 3})
        run_dir = state.run_dir("P1")
        run_dir.mkdir(parents=True)
        (run_dir / "questions.json").write_text(json.dumps({"schema": "x", "phase": "P1", "questions": QUESTIONS}), encoding="utf-8")
        self.memory = state.memory_dir / "memory.jsonl"
        self.memory.write_text("".join(json.dumps(e) + "\n" for e in EVENTS), encoding="utf-8")

    def moment(self, part: str) -> dict:
        return replay.replay_moments(self.root, "s1", [part])["moments"][0]


class MomentMeaningTest(ReplayCase):
    def test_typed_source_marks_a_deposit_not_a_removal(self):
        moment = self.moment("dec-0001")
        self.assertEqual((moment["status"], moment["meaning"], moment["observed_s"]), ("not_prepared", "deposit_window_start", 22.2))
        self.assertEqual(moment["window_s"], [22.2, 60.0])
        self.assertIn("being put down", moment["meaning_text"])
        self.assertIn("It marks the deposit, not the removal.", moment["meaning_text"])
        self.assertIn("22.2", moment["meaning_text"])

    def test_chosen_candidate_and_destination_mark_a_removal(self):
        chosen = self.moment("dec-0002")
        self.assertEqual((chosen["meaning"], chosen["window_s"]), ("removal_window_start", [30.0, 34.5]))
        self.assertIn("being removed", chosen["meaning_text"])
        self.assertNotIn("deposit", chosen["meaning_text"])
        destination = self.moment("dec-0003")
        self.assertEqual((destination["meaning"], destination["window_s"]), ("removal_window_start", [90.0, 95.0]))

    def test_stated_and_unspecified_times_are_labelled_as_such(self):
        stated = self.moment("dec-0005")
        self.assertEqual(stated["meaning"], "operator_stated")
        self.assertIn("stated by the operator, not observed", stated["meaning_text"])
        unspecified = self.moment("dec-0007")
        self.assertEqual(unspecified["meaning"], "unspecified")
        self.assertIn("does not say whether it marks the removal or the deposit", unspecified["meaning_text"])

    def test_part_without_a_time_says_plainly_that_nothing_can_be_shown(self):
        moment = self.moment("dec-0004")
        self.assertEqual((moment["status"], moment["observed_s"], moment["key"]), ("no_moment", None, None))
        self.assertEqual(moment["message"], replay.NO_MOMENT_TIME)

    def test_a_correction_inherits_the_time_of_the_record_it_replaced(self):
        moment = self.moment("dec-0007")
        self.assertEqual((moment["observed_s"], moment["via_record"], moment["meaning"]), (12.0, "dec-0006", "unspecified"))
        self.assertIn("comes from record dec-0006, which dec-0007 replaced", moment["meaning_text"])
        with self.assertRaises(AdapterError) as replaced:
            self.moment("dec-0006")
        self.assertEqual(replaced.exception.status, 404)

    def test_missing_video_means_no_moment(self):
        self.video.unlink()
        moment = self.moment("dec-0002")
        self.assertEqual((moment["status"], moment["message"]), ("no_moment", replay.NO_MOMENT_VIDEO))

    def test_fixed_label_is_always_present(self):
        self.assertEqual(self.moment("dec-0001")["label_fixed"], "Reverse replay of the recorded disassembly, not a verified installation procedure.")
        self.assertEqual(self.moment("dec-0004")["label_fixed"], replay.LABEL)

    def test_listing_is_read_only(self):
        before = {p: p.read_bytes() for p in self.session_dir.rglob("*") if p.is_file()}
        replay.replay_moments(self.root, "s1", ["dec-0001", "dec-0002", "dec-0004"])
        self.assertEqual(before, {p: p.read_bytes() for p in self.session_dir.rglob("*") if p.is_file()})
        self.assertFalse((self.session_dir / "evidence").exists())
        with self.assertRaises(AdapterError):
            replay.replay_moments(self.root, "s1", [])


class ClipPlanTest(ReplayCase):
    DEPOSIT = "deposit_window_start"

    def test_a_deposit_clip_starts_at_the_previous_deposit_within_twelve_seconds(self):
        questions = [{"deposit_window_s": [20.0, 25.0]}, {"deposit_window_s": [30.1, 31.0]}, {"deposit_window_s": [35.4, 36.0]}]
        clip = replay._clip(self.DEPOSIT, 35.4, None, questions)
        self.assertEqual((clip["start_s"], clip["end_s"], clip["anchor_s"]), (30.1, 37.4, 35.4))

    def test_without_a_close_previous_deposit_the_lead_is_twelve_seconds_and_never_below_zero(self):
        self.assertEqual(replay._clip(self.DEPOSIT, 35.4, None, [{"deposit_window_s": [5.0, 6.0]}])["start_s"], 23.4)
        self.assertEqual(replay._clip(self.DEPOSIT, 5.0, None, [])["start_s"], 0.0)

    def test_a_previous_deposit_right_before_still_leaves_three_seconds(self):
        self.assertEqual(replay._clip(self.DEPOSIT, 35.4, None, [{"deposit_window_s": [34.5, 35.0]}])["start_s"], 32.4)

    def test_removal_and_other_times_get_their_own_clips(self):
        removal = replay._clip("removal_window_start", 30.0, [30.0, 34.5], [])
        self.assertEqual((removal["start_s"], removal["end_s"]), (29.0, 35.5))
        self.assertEqual(replay._clip("removal_window_start", 30.0, [30.0, 80.0], [])["end_s"], 42.0)
        around = replay._clip("unspecified", 41.5, None, [])
        self.assertEqual((around["kind"], around["start_s"], around["end_s"]), ("around", 40.5, 42.5))

    def test_the_label_says_the_removal_is_not_marked_and_the_key_changed(self):
        moment = self.moment("dec-0001")
        self.assertIn("Recorded seconds before this part was put down; the removal itself is not marked.", moment["clip_text"])
        self.assertIn("10.2–24.2", moment["clip_text"])
        self.assertTrue(moment["key"].endswith("-v2"))
        old = self.session_dir / "evidence" / "replay" / f"{VIDEO_SHA[:12]}-22200-v1"
        old.mkdir(parents=True)
        (old / "manifest.json").write_text(json.dumps({"status": "ready"}), encoding="utf-8")
        self.assertEqual(self.moment("dec-0001")["status"], "not_prepared")  # a version 1 folder is never read
        self.assertIn("around this time", self.moment("dec-0005")["clip_text"])


def storage(event_id: str, target: str, cell: str) -> dict:
    return {"type": "storage_link", "id": event_id, "session": SESSION, "timestamp": "2026-11-02T18:22:43Z", "target_id": target,
            "location": cell, "basis": {"kind": "human", "confirmed_by": "workstation"}, "supersedes": None}


class CellMomentTest(ReplayCase):
    """The time comes from the storage cell when the video has an empty-sheet reference; memory is never changed."""

    def setUp(self):
        super().setUp()
        with self.memory.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(storage("store-0001", "dec-0001", "cell-1")) + "\n")

    def write_settle(self, document: dict) -> None:
        path = replay_time.settle_path(self.session_dir, VIDEO_SHA)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")

    def commands(self, run, name):
        return [c["command"] for c in run.calls if c["command"][2].endswith(name)]

    def test_before_any_measurement_the_detector_time_is_an_estimate(self):
        moment = self.moment("dec-0001")
        self.assertEqual((moment["time_basis"], moment["observed_s"], moment["cell"]), ("detector_estimate", 22.2, "cell-1"))
        self.assertIn("have not been measured yet", moment["meaning_text"])

    def test_the_first_prepare_measures_the_cells_once_and_shows_the_settle_time(self):
        run = fake_worker(settle=settle_document({"cell-1": {"settled_s": 100.0}}))
        memory_before = self.memory.read_bytes()
        result = replay.replay_prepare(self.root, "s1", "dec-0001", run=run)
        self.assertEqual(len(self.commands(run, "cell_settle_worker.py")), 1)
        self.assertEqual((result["time_basis"], result["observed_s"], result["recorded_t_s"], result["settled_s"]),
                         ("storage_cell_settle", 100.0, 22.2, 100.0))
        self.assertIn("Time measured from the storage cell", result["meaning_text"])
        extraction = self.commands(run, "replay_worker.py")[0]
        self.assertEqual(extraction[extraction.index("--anchor-s") + 1], "100.0")
        self.assertEqual((result["clip"]["start_s"], result["clip"]["end_s"]), (88.0, 102.0))
        replay.replay_prepare(self.root, "s1", "dec-0001", run=run)
        self.assertEqual((len(self.commands(run, "cell_settle_worker.py")), len(self.commands(run, "replay_worker.py"))), (1, 1))
        self.assertTrue((self.session_dir / "evidence" / "cell-settle" / f"{VIDEO_SHA[:12]}-v2.json").is_file())
        self.assertEqual(self.memory.read_bytes(), memory_before)

    def test_a_video_without_a_reference_keeps_the_detector_time_labelled_as_an_estimate(self):
        run = fake_worker(settle=settle_document({}, "no_reference", "the sheet is not empty at the start of the video"))
        result = replay.replay_prepare(self.root, "s1", "dec-0001", run=run)
        self.assertEqual((result["time_basis"], result["observed_s"]), ("detector_estimate", 22.2))
        self.assertIn("This time is a detector estimate", result["meaning_text"])
        self.assertIn("the sheet is not empty at the start of the video", result["meaning_text"])
        self.assertEqual(len(self.commands(run, "replay_worker.py")), 1)

    def test_a_cell_without_lasting_change_shows_no_clip(self):
        self.write_settle(settle_document({"cell-1": {"occupied": False, "settled_s": None}}))
        run = fake_worker()
        result = replay.replay_prepare(self.root, "s1", "dec-0001", run=run)
        self.assertEqual(result["status"], "no_moment")
        self.assertIn("moment is not known", result["message"])
        self.assertEqual(run.calls, [])

    def test_parts_without_a_cell_or_a_deposit_time_are_not_measured(self):
        run = fake_worker()
        replay.replay_prepare(self.root, "s1", "dec-0003", run=run)  # a removal window, no storage cell
        replay.replay_prepare(self.root, "s1", "dec-0005", run=run)  # stated by the operator, no cell
        self.assertEqual(self.commands(run, "cell_settle_worker.py"), [])

    def test_a_failing_measurement_is_reported(self):
        def broken(command, **kwargs):
            return SimpleNamespace(returncode=2, stderr="cell measurement failed: the video cannot be read\n", stdout="")
        with self.assertRaisesRegex(AdapterError, "could not be measured"):
            replay.replay_prepare(self.root, "s1", "dec-0001", run=broken)

    def test_several_parts_in_a_cell_use_the_fitting_window_or_say_the_moment_is_not_known(self):
        questions = [{**QUESTIONS[0], "cell": "cell-1", "deposit_window_s": [22.2, 30.0]}, *QUESTIONS[1:],
                     {"id": "Q-P1-04", "kind": "source_of_part", "part": "P1-C01b", "cell": "cell-1",
                      "deposit_window_s": [50.0, 52.0], "candidates": []}]
        (self.session_dir / "runs" / "P1" / "questions.json").write_text(json.dumps({"questions": questions}), encoding="utf-8")
        with self.memory.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(removal("dec-0008", "screw", "back shell", 50.0, "answer to Q-P1-04; typed")) + "\n")
            handle.write(json.dumps(storage("store-0008", "dec-0008", "cell-1")) + "\n")
        self.write_settle(settle_document({"cell-1": {"settled_s": 44.0}}))
        fits = self.moment("dec-0008")
        self.assertEqual((fits["time_basis"], fits["observed_s"]), ("detector_fitting_cell", 50.0))
        self.assertEqual(fits["window_s"], [50.0, 52.0])
        early = self.moment("dec-0001")  # its own window at 22.2 s is before the cell's first lasting change at 44 s
        self.assertEqual((early["status"], early["time_basis"], early["key"]), ("no_moment", "unknown", None))
        self.assertIn("no detector window fits", early["message"])


class PrepareTest(ReplayCase):
    def test_extracts_once_and_serves_only_names_times_and_hashes(self):
        run = fake_worker()
        memory_before = hashlib.sha256(self.memory.read_bytes()).hexdigest()
        first = replay.replay_prepare(self.root, "s1", "dec-0001", run=run)
        self.assertEqual(first["status"], "ready")
        self.assertEqual(len(run.calls), 1)
        command = run.calls[0]["command"]
        self.assertEqual(command[command.index("--video") + 1], str(self.video))
        self.assertEqual(command[command.index("--video-sha256") + 1], VIDEO_SHA)
        self.assertEqual([command[command.index(flag) + 1] for flag in ("--start-s", "--end-s", "--anchor-s")],
                         ["10.2", "24.2", "22.2"])
        self.assertTrue(Path(run.calls[0]["env"]["ANASTYLOS_DRAWING_CACHE"]).is_relative_to(self.session_dir / "evidence"))
        player = first["player"]
        self.assertEqual([f["file"] for f in player["frames"]], ["seq-000.jpg", "seq-001.jpg", "seq-002.jpg", "seq-003.jpg"])
        self.assertEqual((player["before"]["t_s"], player["after"]["t_s"]), (21.7, 22.7))
        self.assertEqual((player["before"]["low_skin"], player["after"]["low_skin"]), (True, False))
        self.assertEqual(player["playback_fps"], replay.MIN_PLAYBACK_FPS)
        self.assertEqual(player["skipped_frames"], 1)
        shown = json.dumps(first)
        for secret in (str(self.video), "video.mp4", str(self.session_dir), "\\\\"):
            self.assertNotIn(secret, shown)
        self.assertEqual(first["key"], f"{VIDEO_SHA[:12]}-22200-10200-24200-v2")
        self.assertTrue((self.session_dir / "evidence" / "replay" / first["key"] / "manifest.json").is_file())
        again = replay.replay_prepare(self.root, "s1", "dec-0001", run=run)
        self.assertEqual((len(run.calls), again["status"]), (1, "ready"))
        self.assertEqual(self.moment("dec-0001")["status"], "ready")
        self.assertEqual(hashlib.sha256(self.memory.read_bytes()).hexdigest(), memory_before)

    def test_unregistered_frames_say_no_moment_can_be_shown_and_are_cached(self):
        run = fake_worker(status="unregistered")
        result = replay.replay_prepare(self.root, "s1", "dec-0002", run=run)
        self.assertEqual(result["status"], "unregistered")
        self.assertNotIn("player", result)
        self.assertEqual(result["message"], replay.NO_MOMENT_REGISTERED)
        replay.replay_prepare(self.root, "s1", "dec-0002", run=run)
        self.assertEqual(len(run.calls), 1)

    def test_part_without_time_never_starts_the_extractor(self):
        run = fake_worker()
        result = replay.replay_prepare(self.root, "s1", "dec-0004", run=run)
        self.assertEqual((result["status"], run.calls), ("no_moment", []))

    def test_a_failing_extractor_is_reported_and_leaves_no_cache(self):
        with self.assertRaisesRegex(AdapterError, "cannot be read") as caught:
            replay.replay_prepare(self.root, "s1", "dec-0001", run=fake_worker(returncode=2))
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(self.moment("dec-0001")["status"], "not_prepared")

    def test_files_are_served_by_whitelist_only(self):
        key = replay.replay_prepare(self.root, "s1", "dec-0001", run=fake_worker())["key"]
        body, content_type = replay.replay_file(self.root, "s1", key, "seq-001.jpg")
        self.assertEqual((body, content_type), (b"\xff\xd8seq-001.jpg", "image/jpeg"))
        self.assertEqual(replay.replay_file(self.root, "s1", key, "before.jpg")[0], b"\xff\xd8before")
        for bad_name in ("manifest.json", "../manifest.json", "seq-1.jpg", "seq-001.png", "", "..\\x.jpg", "seq-009.jpg"):
            with self.subTest(name=bad_name), self.assertRaises(AdapterError) as caught:
                replay.replay_file(self.root, "s1", key, bad_name)
            self.assertEqual(caught.exception.status, 404)
        for bad_key in ("", "..", "../replay", key + "/..", "zz" * 6 + "-1-2-3-v2", VIDEO_SHA[:12] + "-1-v1"):
            with self.subTest(key=bad_key), self.assertRaises(AdapterError):
                replay.replay_file(self.root, "s1", bad_key, "seq-000.jpg")


class RouteTest(ReplayCase):
    def setUp(self):
        super().setUp()
        self.httpd = server.serve(self.root, "127.0.0.1", 0)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join, 30)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def get(self, path: str):
        try:
            with urllib.request.urlopen(self.base + path, timeout=30) as response:
                return response.status, response.headers.get_content_type(), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get_content_type(), exc.read()

    def test_moments_and_files_over_http(self):
        status, _type, body = self.get("/api/replay?session=s1&parts=dec-0001,dec-0004")
        moments = json.loads(body)["moments"]
        self.assertEqual((status, [m["status"] for m in moments]), (200, ["not_prepared", "no_moment"]))
        key = replay.replay_prepare(self.root, "s1", "dec-0001", run=fake_worker())["key"]
        status, content_type, body = self.get(f"/api/replay/file?session=s1&key={key}&name=after.jpg")
        self.assertEqual((status, content_type, body), (200, "image/jpeg", b"\xff\xd8after"))
        self.assertEqual(self.get(f"/api/replay/file?session=s1&key={key}&name=manifest.json")[0], 404)
        self.assertEqual(self.get("/replay.js")[0], 200)


class WorkerTest(unittest.TestCase):
    """The real worker on a tiny synthetic video, with sheet registration replaced by a stub."""

    @classmethod
    def setUpClass(cls):
        import cv2
        import numpy as np
        cls.cv2, cls.np = cv2, np
        sys.path.insert(0, str(UI_ROOT))
        import replay_worker
        cls.worker = replay_worker

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.video = self.tmp / "clip.avi"
        writer = self.cv2.VideoWriter(str(self.video), self.cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
        for i in range(150):  # 15 s at 10 fps; the grey level encodes the frame index
            writer.write(self.np.full((48, 64, 3), i, self.np.uint8))
        writer.release()
        self.sha = hashlib.sha256(self.video.read_bytes()).hexdigest()
        patcher = mock.patch.dict(os.environ, {"ANASTYLOS_DRAWING_CACHE": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def registered(self, failing: set[int]):
        def fake(video, frame_index, ppm):
            if frame_index in failing:
                return None, "sheet registration failed: rms 1.3 mm > 0.5 mm"
            return SimpleNamespace(image=self.np.full((9, 12, 3), frame_index % 256, self.np.uint8)), None
        return mock.patch.object(self.worker, "_registered", side_effect=fake)

    def test_failed_registration_frames_are_skipped_and_recorded(self):
        failing = {70, 75}
        with self.registered(failing):
            manifest = self.worker.extract(self.video, self.sha, 6.5, 8.5, 7.5, self.tmp / "out")
        out = self.tmp / "out"
        self.assertEqual(manifest["status"], "ready")
        self.assertEqual([s["frame_index"] for s in manifest["skipped"]], [70, 75])
        self.assertEqual(len(manifest["frames"]), 21 - 2)
        self.assertTrue(all(f["frame_index"] not in failing for f in manifest["frames"]))
        times = [f["t_s"] for f in manifest["frames"]]
        self.assertEqual((times[0], times[-1], times == sorted(times)), (6.5, 8.5, True))
        for name in ("seq-000.jpg", "before.jpg", "after.jpg", "manifest.json"):
            self.assertTrue((out / name).is_file(), name)
        self.assertFalse((self.tmp / "out.partial").exists())
        self.assertEqual(manifest["video"]["sha256"], self.sha)
        self.assertEqual(manifest["files_sha256"]["before.jpg"], hashlib.sha256((out / "before.jpg").read_bytes()).hexdigest())
        self.assertEqual((manifest["before"]["t_s"], manifest["after"]["t_s"]), (7.4, 7.6))
        self.assertTrue(manifest["before"]["low_skin"] and manifest["after"]["low_skin"])
        self.assertEqual((manifest["memory_written"], manifest["model_called"]), (False, False))

    def test_too_few_registered_frames_means_unregistered_and_no_picks(self):
        with self.registered(set(range(150))):
            manifest = self.worker.extract(self.video, self.sha, 6.5, 8.5, 7.5, self.tmp / "out")
        self.assertEqual((manifest["status"], manifest["frames"], manifest["before"], manifest["after"]), ("unregistered", [], None, None))
        self.assertEqual(len(manifest["skipped"]), 21)

    def test_a_long_clip_keeps_about_thirty_frames(self):
        with self.registered(set()):
            manifest = self.worker.extract(self.video, self.sha, 1.0, 13.0, 12.0, self.tmp / "out")
        self.assertTrue(20 <= len(manifest["frames"]) <= 40, len(manifest["frames"]))
        self.assertLessEqual(manifest["rate_fps"], 10.0)
        self.assertEqual((manifest["frames"][0]["t_s"], manifest["end_s"]), (1.0, 13.0))

    def test_the_pair_is_chosen_around_the_anchor_and_hands_are_flagged(self):
        kept = [{"file": f"seq-{i:03d}.jpg", "t_s": float(i), "frame_index": i, "skin": skin}
                for i, skin in enumerate([0.01, 0.015, 0.2, 0.3, 0.25, 0.01, 0.4])]
        before, after = self.worker.choose_pair(kept, anchor_s=3.0)
        self.assertEqual((before["t_s"], before["low_skin"]), (1.0, True))  # the last low-skin frame up to the anchor
        self.assertEqual((after["t_s"], after["low_skin"]), (5.0, True))    # the first low-skin frame after it
        hands = [{**f, "skin": 0.3} for f in kept]
        before, after = self.worker.choose_pair(hands, anchor_s=3.0)
        self.assertEqual((before["t_s"], before["low_skin"], after["t_s"], after["low_skin"]), (3.0, False, 4.0, False))
        self.assertEqual(self.worker.choose_pair(kept[4:], anchor_s=0.0), (None, {**kept[5], "low_skin": True}))

    def test_a_different_video_is_refused_and_nothing_is_written(self):
        with self.registered(set()), self.assertRaisesRegex(ValueError, "differs from the SHA-256"):
            self.worker.extract(self.video, "0" * 64, 6.5, 8.5, 7.5, self.tmp / "out")
        self.assertFalse((self.tmp / "out").exists())
        self.assertFalse((self.tmp / "out.partial").exists())

    def test_command_line_failure_exit_code(self):
        done = subprocess.run([sys.executable, "-B", str(UI_ROOT / "replay_worker.py"), "--video", str(self.video),
                               "--video-sha256", "0" * 64, "--start-s", "6.5", "--end-s", "8.5", "--anchor-s", "7.5", "--out", str(self.tmp / "out")],
                              capture_output=True, text=True, env={**os.environ})
        self.assertEqual(done.returncode, 2)
        self.assertIn("differs from the SHA-256", done.stderr)


if __name__ == "__main__":
    unittest.main()
