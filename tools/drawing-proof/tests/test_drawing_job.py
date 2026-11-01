"""Drawing job: decision rules, plausibility checks (J1/J2 regressions) and visible ends of dead or slow workers."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drawing_job  # noqa: E402
import job_control  # noqa: E402
import quality  # noqa: E402

SPEC_PART = {"scope": "part"}


class DecisionTest(unittest.TestCase):
    def test_j1_frame_before_first_removal_needs_confirmation_even_if_muse_says_single_part(self):
        choice = {"state": "single_part"}
        decision, reasons = drawing_job.frame_decision(SPEC_PART, choice, t_s=0.5, removal_s=16.0)
        self.assertEqual(decision, "NEEDS_CONFIRMATION")
        self.assertIn("before the first observed removal", reasons[0])

    def test_unclear_or_other_state_needs_confirmation(self):
        for state in ("unclear", "assembled_object"):
            self.assertEqual(drawing_job.frame_decision(SPEC_PART, {"state": state}, 30.0, 16.0)[0], "NEEDS_CONFIRMATION")

    def test_matching_state_after_removal_is_ok(self):
        self.assertEqual(drawing_job.frame_decision(SPEC_PART, {"state": "single_part"}, 30.0, 16.0), ("ok", []))

    def test_final_state_never_releases(self):
        self.assertEqual(drawing_job.final_state(False, [], "ok"), "NEEDS_CAPTURE")
        self.assertEqual(drawing_job.final_state(True, ["BOX_AT_IMAGE_EDGE"], "ok"), "NEEDS_CAPTURE")
        self.assertEqual(drawing_job.final_state(True, ["MASK_SPARSE_IN_BOX"], "ok"), "NEEDS_REVIEW")
        self.assertEqual(drawing_job.final_state(True, [], "NEEDS_CONFIRMATION"), "NEEDS_CONFIRMATION")
        self.assertEqual(drawing_job.final_state(True, [], "ok"), "drawn_unconfirmed")


class PlausibilityTest(unittest.TestCase):
    def test_j2_half_board_in_a_box_reaching_the_image_edge_is_flagged(self):
        # J2 shape: the box runs to the bottom image edge (cable), the mask covers only the left half of the board.
        mask = np.zeros((1062, 1524), bool)
        mask[250:450, 300:600] = True
        flags = quality.plausibility(mask, [300, 250, 1200, 1062], None)["flags"]
        self.assertIn("BOX_AT_IMAGE_EDGE", flags)
        self.assertIn("MASK_SPARSE_IN_BOX", flags)
        self.assertIn("MASK_BOX_MISMATCH", flags)
        self.assertEqual(drawing_job.final_state(True, flags, "ok"), "NEEDS_CAPTURE")

    def test_mask_clipped_by_its_sam_box(self):
        mask = np.zeros((400, 400), bool)
        mask[100:300, 100:300] = True
        flags = quality.plausibility(mask, [100, 100, 300, 300], {"left": 100, "top": 100, "right": 300, "bottom": 300})["flags"]
        self.assertIn("MASK_CLIPPED_BY_BOX", flags)

    def test_consistent_box_and_mask_pass(self):
        mask = np.zeros((400, 400), bool)
        mask[110:290, 120:280] = True
        self.assertEqual(quality.plausibility(mask, [105, 105, 295, 295], {"left": 105, "top": 105, "right": 295, "bottom": 295})["flags"], [])


class WorkerEndTest(unittest.TestCase):
    def _progress(self, job: Path, **fields) -> None:
        doc = {"state": "running", "stage": "muse_box", "pid": 0, "heartbeat_at": time.time(), "stage_started_at": time.time(),
               "started_at": time.time(), "stage_s": {}, "done": 4, "total": 7}
        doc.update(fields)
        (job / "progress.json").write_text(json.dumps(doc), encoding="utf-8")

    def test_dead_worker_becomes_failed_and_stays_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            dead = subprocess.Popen([sys.executable, "-c", "pass"])
            dead.wait()
            self._progress(job, pid=dead.pid)
            self.assertEqual(job_control.status(job)["state"], "failed")
            self.assertEqual(json.loads((job / "progress.json").read_text())["state"], "failed")

    def test_stage_timeout_kills_the_worker_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                self._progress(job, pid=worker.pid, stage_started_at=time.time() - job_control.STAGE_TIMEOUT_S["muse_box"] - 5)
                doc = job_control.status(job)
                self.assertEqual(doc["state"], "failed")
                self.assertIn("exceeded", doc["error"])
                self.assertIsNotNone(worker.wait(timeout=10))
            finally:
                if worker.poll() is None:
                    worker.kill()

    def test_stale_heartbeat_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                self._progress(job, pid=worker.pid, heartbeat_at=time.time() - 120)
                self.assertIn("heartbeat", job_control.status(job)["error"])
            finally:
                worker.kill()
                worker.wait()


if __name__ == "__main__":
    unittest.main()
