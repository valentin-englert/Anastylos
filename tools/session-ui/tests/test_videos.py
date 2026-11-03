"""Phone videos on the session page: listing, the process job with a fake runner, failures and the HTTP routes."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))

import server  # noqa: E402
import videos  # noqa: E402
from adapter import AdapterError  # noqa: E402

PAYLOAD = b"\x00\x00\x00\x18ftypmp42" + b"frames" * 100
SHA = hashlib.sha256(PAYLOAD).hexdigest()
VIDEO_ID = SHA[:32]


class FakeRunner:
    """Stands in for session.py: records the command and writes the parts.json the real runner would."""

    def __init__(self, returncode=0, stderr=""):
        self.returncode, self.stderr, self.commands = returncode, stderr, []

    def __call__(self, command):
        self.commands.append(command)
        if self.returncode == 0:
            run_dir = Path(command[command.index("--out") + 1])
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "parts.json").write_text(json.dumps(
                {"video_sha256": SHA, "counts": {"parts": 3, "linked_automatic": 2, "written_to_memory": 2, "questions": 1}}))
        return videos.phases.Completed(self.returncode, "", self.stderr)


class VideoCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "workspace"
        self.root.mkdir()
        self.receiver = Path(tmp.name) / "receiver-data"
        (self.root / videos.SETTINGS).write_text(json.dumps({"receiver_data_dir": str(self.receiver)}))
        self.python = Path(sys.executable)

    def store(self, payload=PAYLOAD, status="complete", sha=SHA):
        directory = self.receiver / "videos" / "guided-1" / sha[:32]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "meta.json").write_text(json.dumps({
            "video_id": sha[:32], "session_id": "guided-1", "sha256": sha, "size": len(payload), "status": status,
            "declared_at": "2026-11-02T12:00:00Z", "completed_at": "2026-11-02T12:01:00Z"}))
        if status == "complete":
            (directory / "video.mp4").write_bytes(payload)
        return directory

    def process(self, runner, background=False):
        return videos.start_processing(self.root, VIDEO_ID, runner, self.python, background=background)


class TestListing(VideoCase):
    def test_lists_received_videos_with_their_state(self):
        self.store()
        view = videos.list_videos(self.root)
        self.assertTrue(view["configured"])
        self.assertEqual([(v["video"], v["status"], v["job"]) for v in view["videos"]], [(VIDEO_ID, "complete", None)])

    def test_without_receiver_setup_the_list_is_empty_and_says_so(self):
        (self.root / videos.SETTINGS).unlink()
        self.assertEqual(videos.list_videos(self.root), {"configured": False, "videos": []})


class TestProcess(VideoCase):
    def test_process_creates_a_new_session_folder_and_runs_the_pipeline_on_the_stored_file(self):
        directory = self.store()
        runner = FakeRunner()
        job = self.process(runner)
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["counts"]["parts"], 3)
        self.assertTrue((self.root / job["session"] / "agent-session.json").is_file())
        command = runner.commands[0]
        self.assertEqual(command[command.index("--video") + 1], str((directory / "video.mp4").resolve()))
        self.assertEqual(videos.list_videos(self.root)["videos"][0]["job"]["state"], "done")

    def test_a_second_click_returns_the_same_job_without_a_second_run(self):
        self.store()
        runner = FakeRunner()
        first = self.process(runner)
        again = self.process(runner)
        self.assertTrue(again["already_started"])
        self.assertEqual(again["session"], first["session"])
        self.assertEqual(len(runner.commands), 1)

    def test_the_pipelines_own_rejection_is_reported_verbatim_and_can_be_retried(self):
        self.store()
        reason = "rejected: the sheet is not stable in the video (handheld footage)"
        job = self.process(FakeRunner(returncode=2, stderr=reason))
        self.assertEqual(job["state"], "failed")
        self.assertIn(reason, job["error"])
        time.sleep(1.1)  # the folder name carries a timestamp
        retry = self.process(FakeRunner())
        self.assertEqual(retry["state"], "done")
        self.assertNotEqual(retry["session"], job["session"])

    def test_a_missing_vision_python_is_a_visible_failure(self):
        self.store()
        job = videos.start_processing(self.root, VIDEO_ID, FakeRunner(), self.root / "no-python.exe", background=False)
        self.assertEqual(job["state"], "failed")
        self.assertIn("no-python.exe", job["error"])

    def test_a_changed_file_is_refused_before_any_session_is_created(self):
        directory = self.store()
        (directory / "video.mp4").write_bytes(PAYLOAD + b"x")
        with self.assertRaises(AdapterError) as caught:
            self.process(FakeRunner())
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith("video-")], [])

    def test_unfinished_and_unknown_videos_are_refused(self):
        self.store(status="receiving")
        with self.assertRaises(AdapterError) as unfinished:
            self.process(FakeRunner())
        self.assertEqual(unfinished.exception.status, 409)
        with self.assertRaises(AdapterError) as unknown:
            videos.start_processing(self.root, "f" * 32, FakeRunner(), self.python, background=False)
        self.assertEqual(unknown.exception.status, 404)

    def test_parts_are_found_after_processing_and_a_failure_stays_visible(self):
        self.store()
        calls = []
        videos.start_processing(self.root, VIDEO_ID, FakeRunner(), self.python, background=False,
                                find_parts=lambda root, folder: calls.append(folder) or {"not_drawn": [1]})
        job = videos.list_videos(self.root)["videos"][0]["job"]
        self.assertEqual((job["state"], job["parts"], job["parts_not_drawn"], len(calls)), ("done", "found", 1, 1))

    def test_a_failed_part_search_leaves_the_job_done(self):
        self.store()

        def broken(root, folder):
            raise AdapterError("no key", 502)

        videos.start_processing(self.root, VIDEO_ID, FakeRunner(), self.python, background=False, find_parts=broken)
        job = videos.list_videos(self.root)["videos"][0]["job"]
        self.assertEqual((job["state"], job["parts"], job["parts_error"]), ("done", "failed", "no key"))

    def test_storage_cells_are_measured_after_the_part_search_and_a_failure_stays_visible(self):
        self.store()
        calls = []
        videos.start_processing(self.root, VIDEO_ID, FakeRunner(), self.python, background=False,
                                find_parts=lambda root, folder: {"not_drawn": []},
                                measure_cells=lambda root, folder: calls.append(folder) or {"measured": ["P1"]})
        job = videos.list_videos(self.root)["videos"][0]["job"]
        self.assertEqual((job["state"], job["parts"], job["cells"], len(calls)), ("done", "found", "measured", 1))

    def test_a_failed_cell_measurement_leaves_the_job_done_and_the_parts_found(self):
        self.store()

        def broken(root, folder):
            raise AdapterError("the video cannot be read", 502)

        videos.start_processing(self.root, VIDEO_ID, FakeRunner(), self.python, background=False,
                                find_parts=lambda root, folder: {"not_drawn": []}, measure_cells=broken)
        job = videos.list_videos(self.root)["videos"][0]["job"]
        self.assertEqual((job["state"], job["parts"], job["cells"], job["cells_error"]), ("done", "found", "failed", "the video cannot be read"))

    def test_background_job_ends_in_a_visible_state(self):
        self.store()
        self.process(FakeRunner(), background=True)
        state = None
        for _ in range(200):
            job = videos.list_videos(self.root)["videos"][0]["job"]
            state = job and job["state"]
            if state in ("done", "failed"):
                break
            time.sleep(0.05)
        for worker in threading.enumerate():
            if worker.name.startswith("video-"):
                worker.join(10)  # the temp folder must not disappear under a running job
        self.assertEqual(state, "done")


class TestRoutes(VideoCase):
    def setUp(self):
        super().setUp()
        self.httpd = server.serve(self.root, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join, 30)
        self.addCleanup(self.httpd.shutdown)

    def call(self, path, body=None):
        request = urllib.request.Request(self.base + path, method="POST" if body is not None else "GET")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        try:
            with urllib.request.urlopen(request, data=data, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_list_route_and_unknown_video(self):
        self.store()
        status, view = self.call("/api/videos")
        self.assertEqual((status, view["videos"][0]["video"]), (200, VIDEO_ID))
        status, body = self.call("/api/videos/process", {"video": "f" * 32})
        self.assertEqual(status, 404)
        self.assertIn("Unknown video", body["error"])


if __name__ == "__main__":
    unittest.main()
