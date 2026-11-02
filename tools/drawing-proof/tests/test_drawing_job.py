"""Drawing job: decision rules, plausibility checks (J1/J2 regressions) and visible ends of dead or slow workers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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


class FakeWorld:
    """Clock, process table and spawner for job_control: no real processes, sleeping advances the clock."""

    def __init__(self):
        self.t, self.alive, self.killed, self.spawned, self.next_pid = 1000.0, set(), [], [], 5000
        self.spawn_snapshots = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def kill(self, pid):
        self.killed.append(pid)
        self.alive.discard(pid)

    def spawn(self, spec, job, token):
        self.spawn_snapshots.append(json.loads((job / "progress.json").read_text(encoding="utf-8")))  # state on disk before the worker exists
        self.next_pid += 1
        self.spawned.append((self.next_pid, token))
        self.alive.add(self.next_pid)
        return self.next_pid


class JobRaceTest(unittest.TestCase):
    def setUp(self):
        self.world = FakeWorld()
        for name, fake in (("_now", self.world.now), ("_sleep", self.world.sleep), ("process_alive", lambda pid: pid in self.world.alive),
                           ("kill_process", self.world.kill), ("_spawn", self.world.spawn)):
            patcher = mock.patch.object(job_control, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.job = Path(self._tmp.name) / "job"
        self.spec = Path(self._tmp.name) / "spec.json"
        self.spec.write_text("{}", encoding="utf-8")

    def disk(self) -> dict:
        return json.loads((self.job / "progress.json").read_text(encoding="utf-8"))

    def start_and_adopt(self) -> dict:
        """start() plus the worker's claim, as the real worker does under its own pid."""
        job_control.start(self.spec, self.job)
        token = self.disk()["token"]
        self.world.alive.add(os.getpid())
        return job_control.claim_run(self.job, token)

    def test_poll_at_worker_exit_does_not_overwrite_done(self):
        run = self.start_and_adopt()
        first_check = []

        def exits_during_poll(pid):  # the worker writes its final state right after the poller read "running"
            if not first_check:
                first_check.append(pid)
                job_control.write_if_owner(self.job, {**run, "state": "done", "result": {"status": "drawn_unconfirmed"}})
            return False

        with mock.patch.object(job_control, "process_alive", exits_during_poll):
            seen = job_control.status(self.job)
        self.assertEqual((seen["state"], self.disk()["state"]), ("done", "done"))
        self.assertEqual(self.disk()["result"], {"status": "drawn_unconfirmed"})

    def test_terminal_states_are_returned_and_never_rewritten(self):
        for state in ("done", "failed", "paused"):
            with self.subTest(state=state):
                run = self.start_and_adopt()
                job_control.write_if_owner(self.job, {**run, "state": state})
                before = (self.job / "progress.json").read_bytes()
                self.world.alive.clear()
                self.world.t += 3600
                self.assertEqual(job_control.status(self.job)["state"], state)
                self.assertEqual((self.job / "progress.json").read_bytes(), before)
                job_control.start(self.spec, self.job)  # leave the next subtest a fresh generation
                self.world.alive.clear()

    def test_poller_fails_a_dead_worker_once_and_keeps_its_generation(self):
        run = self.start_and_adopt()
        self.world.alive.clear()
        doc = job_control.status(self.job)
        self.assertEqual((doc["state"], doc["generation"], doc["token"]), ("failed", run["generation"], run["token"]))
        self.assertEqual(doc["detected_by"], "status_poll")

    def test_poller_does_not_fail_a_worker_that_beat_between_read_and_write(self):
        run = self.start_and_adopt()
        self.world.t += job_control.HEARTBEAT_STALE_S + 5  # stale on the first read
        calls = []

        def beats_during_poll(pid):
            if not calls:
                calls.append(pid)
                job_control.write_if_owner(self.job, {**run, "heartbeat_at": self.world.t})
            return True

        with mock.patch.object(job_control, "process_alive", beats_during_poll):
            self.assertEqual(job_control.status(self.job)["state"], "running")
        self.assertEqual(self.disk()["state"], "running")

    def test_stage_timeout_fails_then_kills_the_worker(self):
        run = self.start_and_adopt()
        worker_pid = 7100
        self.world.alive.add(worker_pid)
        job_control.write_if_owner(self.job, {**run, "pid": worker_pid, "stage": "sam", "stage_started_at": self.world.t})
        self.world.t += job_control.STAGE_TIMEOUT_S["sam"] + 1
        job_control.write_if_owner(self.job, {**self.disk(), "heartbeat_at": self.world.t})  # alive and beating, only slow
        self.assertIn("exceeded", job_control.status(self.job)["error"])
        self.assertEqual(self.disk()["state"], "failed")
        self.assertEqual(self.world.killed, [worker_pid])

    def test_start_writes_state_with_generation_before_the_worker_is_spawned(self):
        result = job_control.start(self.spec, self.job)
        snapshot = self.world.spawn_snapshots[0]
        self.assertEqual((snapshot["state"], snapshot["generation"], snapshot["pid"]), ("running", 1, None))
        self.assertEqual(snapshot["token"], self.world.spawned[0][1])
        self.assertEqual((result["state"], result["generation"]), ("started", 1))
        adopted = job_control.claim_run(self.job, snapshot["token"])
        self.assertEqual((adopted["generation"], adopted["token"], adopted["started_at"]),
                         (1, snapshot["token"], snapshot["started_at"]))
        self.assertEqual(adopted["pid"], os.getpid())

    def test_stale_writer_is_refused_after_a_new_generation(self):
        old = self.start_and_adopt()
        job_control.write_if_owner(self.job, {**old, "state": "paused"})
        job_control.start(self.spec, self.job)
        current = self.disk()
        self.assertEqual(current["generation"], old["generation"] + 1)
        with self.assertRaises(job_control.Superseded):
            job_control.write_if_owner(self.job, {**old, "state": "done"})
        self.assertEqual(self.disk(), current)

    def test_worker_cannot_adopt_a_run_that_is_not_its_own(self):
        job_control.start(self.spec, self.job)
        with self.assertRaises(job_control.Superseded):
            job_control.claim_run(self.job, "token-of-an-older-run")

    def test_stale_progress_object_stops_and_writes_nothing(self):
        job_control.start(self.spec, self.job)
        stale = drawing_job.Progress(self.job, token=self.disk()["token"])
        stale._stop.set()
        job_control.write_if_owner(self.job, {**self.disk(), "state": "failed", "error": "ended by poller"})
        job_control.start(self.spec, self.job)
        before = self.disk()
        with self.assertRaises(job_control.Superseded):
            stale.update(stage="sam")
        stale.finish("done", result={"status": "drawn_unconfirmed"})  # silently refused
        self.assertEqual(self.disk(), before)

    def test_progress_after_poller_failure_cannot_resurrect_the_job(self):
        job_control.start(self.spec, self.job)
        worker = drawing_job.Progress(self.job, token=self.disk()["token"])
        worker._stop.set()
        self.world.alive.clear()
        self.assertEqual(job_control.status(self.job)["state"], "failed")
        worker.finish("done", result={})
        self.assertEqual(self.disk()["state"], "failed")

    def test_resume_after_stale_heartbeat_kills_old_worker_before_spawning_and_bumps_generation(self):
        run = self.start_and_adopt()
        old_pid = 7000
        self.world.alive.add(old_pid)
        job_control.write_if_owner(self.job, {**run, "pid": old_pid, "spawn_pid": old_pid})
        self.world.t += job_control.HEARTBEAT_STALE_S + 1
        self.assertIn("heartbeat", job_control.status(self.job)["error"])  # failed, but the old worker still lives
        self.assertIn(old_pid, self.world.alive)
        order = []
        real_kill, real_spawn = self.world.kill, self.world.spawn
        with mock.patch.object(job_control, "kill_process", lambda pid: (order.append(("kill", pid)), real_kill(pid))), \
                mock.patch.object(job_control, "_spawn", lambda *a: (order.append(("spawn", old_pid in self.world.alive)), real_spawn(*a))[1]):
            result = job_control.start(self.spec, self.job)
        self.assertEqual(order[0], ("kill", old_pid))
        self.assertEqual(order[-1], ("spawn", False))  # old worker verified dead at spawn time
        self.assertEqual((result["state"], result["generation"]), ("started", run["generation"] + 1))
        self.assertNotEqual(self.disk()["token"], run["token"])
        with self.assertRaises(job_control.Superseded):  # the old worker, had it survived, can no longer write
            job_control.write_if_owner(self.job, {**run, "state": "done"})

    def test_resume_refuses_to_spawn_when_the_old_worker_will_not_die(self):
        run = self.start_and_adopt()
        job_control.write_if_owner(self.job, {**run, "state": "paused"})
        spawned_before = len(self.world.spawned)
        with mock.patch.object(job_control, "kill_process", lambda pid: None):
            with self.assertRaises(job_control.JobError):
                job_control.start(self.spec, self.job)
        self.assertEqual(len(self.world.spawned), spawned_before)
        self.assertEqual(self.disk()["state"], "paused")

    def test_start_on_a_live_running_job_does_not_spawn_a_second_worker(self):
        self.start_and_adopt()
        spawned_before = len(self.world.spawned)
        self.assertEqual(job_control.start(self.spec, self.job)["state"], "already_running")
        self.assertEqual(len(self.world.spawned), spawned_before)

    def test_failed_spawn_ends_the_job_visibly(self):
        with mock.patch.object(job_control, "_spawn", side_effect=OSError("no interpreter")):
            with self.assertRaises(OSError):
                job_control.start(self.spec, self.job)
        self.assertEqual(self.disk()["state"], "failed")

    def test_write_is_atomic_and_survives_a_reader_holding_the_file(self):
        real_replace, calls = os.replace, []

        def busy_once(src, dst):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError("file in use")
            return real_replace(src, dst)

        with mock.patch.object(job_control.os, "replace", busy_once):
            job_control.atomic_write_json(self.job.parent / "p.json", {"a": 1})
        self.assertEqual(json.loads((self.job.parent / "p.json").read_text()), {"a": 1})
        self.assertEqual([p.name for p in self.job.parent.glob("*.tmp")], [])

    def test_lock_is_exclusive_and_times_out(self):
        self.job.mkdir(parents=True)
        with job_control.locked(self.job):
            with self.assertRaises(job_control.LockTimeout):
                with job_control.locked(self.job, timeout_s=0.1):
                    pass
        with job_control.locked(self.job, timeout_s=0.1):  # released again
            pass


class ResumeTest(unittest.TestCase):
    def test_checkpoint_computes_once_and_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            first, reused_first = drawing_job._checkpoint(Path(tmp), "box", lambda: calls.append(1) or {"box": [1, 2, 3, 4]})
            second, reused_second = drawing_job._checkpoint(Path(tmp), "box", lambda: calls.append(1) or {"box": [9, 9, 9, 9]})
            self.assertEqual((first, second, len(calls)), ({"box": [1, 2, 3, 4]}, {"box": [1, 2, 3, 4]}, 1))
            self.assertEqual((reused_first, reused_second), (False, True))

    def test_sam_without_records_is_an_empty_match_not_a_failure(self):
        import muse_sam
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "img-1"
            evidence.mkdir()
            (evidence / "evidence.json").write_text(json.dumps({"records": []}), encoding="utf-8")
            mask, info = muse_sam.read_sam_result(Path(tmp), (10, 10))
            self.assertIsNone(mask)
            self.assertEqual(info["records"], 0)


class StillTest(unittest.TestCase):
    class _Progress:
        def stage(self, name):
            import contextlib
            return contextlib.nullcontext()

    def _still(self, tmp, content=b"jpeg-bytes"):
        import hashlib
        path = Path(tmp) / "still.jpg"
        path.write_bytes(content)
        return {"path": str(path), "sha256": hashlib.sha256(content).hexdigest(), "request_id": "r-1"}

    def test_requested_still_is_rectified_once_and_offered_as_one_tile(self):
        from unittest import mock
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            spec = {"still": self._still(tmp), "region_mm": [0, 0, 10, 10], "px_per_mm": 2}
            rf = mock.Mock(image=np.zeros((20, 20, 3), np.uint8))
            with mock.patch.object(drawing_job.source, "rectify", return_value=(None, rf)) as rectify:
                sheet, chosen, skipped, still_rf = drawing_job._acquire_still(spec, self._Progress())
            self.assertIs(still_rf, rf)
            self.assertEqual((len(chosen), skipped), (1, []))
            self.assertIsNone(rectify.call_args.args[1])  # None = still image, not a video frame
            self.assertEqual(sheet.ndim, 3)

    def test_still_with_a_changed_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            still = self._still(tmp)
            Path(still["path"]).write_bytes(b"other")
            with self.assertRaises(drawing_job.source.SourceError):
                drawing_job._acquire_still({"still": still, "region_mm": [0, 0, 1, 1], "px_per_mm": 1}, self._Progress())

    def test_phase_start_rule_does_not_apply_to_a_requested_still(self):
        spec = {"scope": "assembly"}
        choice = {"state": "assembly"}
        self.assertEqual(drawing_job.frame_decision(spec, choice, 0.0, None), ("ok", []))


if __name__ == "__main__":
    unittest.main()
