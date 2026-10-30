"""Agent M0: session file and phase processing, with a fake session runner (no video pipeline, no network)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anastylos  # noqa: E402
from phases import SESSION_RUNNER, Completed, run_phase, session_command  # noqa: E402
from state import STATE_NAME, SessionError, SessionState  # noqa: E402

PYTHON = Path("python.exe")


class FakeRunner:
    """Writes what session.py would write for a phase: parts.json with counts, questions.json."""

    def __init__(self, returncode=0, video_sha256=None, write=True):
        self.returncode, self.video_sha256, self.write = returncode, video_sha256, write
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        out = Path(command[command.index("--out") + 1])
        out.mkdir(parents=True)
        if self.write:
            sha = self.video_sha256 or _sha(Path(command[command.index("--video") + 1]))
            counts = {"parts": 3, "linked_automatic": 1, "written_to_memory": 1, "questions": 2}
            (out / "parts.json").write_text(json.dumps({"schema": "anastylos-session-run@1", "video_sha256": sha,
                                                        "counts": counts, "parts": []}), encoding="utf-8")
        return Completed(self.returncode, "", "custody step failed" if self.returncode else "")


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Workspace(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "session"
        self.video = self.tmp / "P1.mp4"
        self.video.write_bytes(b"not really a video")
        self.state = SessionState.create(self.root, "SNES-Controller", "operator (test)")


class SessionFileTest(Workspace):
    def test_a_session_is_created_once_and_reloads(self):
        with self.assertRaises(SessionError):
            SessionState.create(self.root, "x", "y")
        loaded = SessionState.load(self.root)
        self.assertEqual(loaded.doc["object"], "SNES-Controller")
        self.assertTrue(loaded.memory_dir.is_dir())

    def test_a_non_empty_folder_is_not_taken_over(self):
        other = self.tmp / "busy"
        other.mkdir()
        (other / "notes.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(SessionError):
            SessionState.create(other, "x", "y")

    def test_loading_a_folder_without_session_file_names_the_fix(self):
        with self.assertRaisesRegex(SessionError, "run `new` first"):
            SessionState.load(self.tmp)


class PhaseTest(Workspace):
    def test_a_phase_runs_the_frozen_runner_and_records_counts(self):
        runner = FakeRunner()
        counts = run_phase(self.state, "P1", "beim Abschrauben der Rückseite", self.video, runner, PYTHON)
        self.assertEqual(counts["questions"], 2)
        command = runner.commands[0]
        self.assertEqual(command[1], str(SESSION_RUNNER))
        self.assertEqual(command[command.index("--memory") + 1], str(self.state.memory_dir))
        phase = SessionState.load(self.root).phase("P1")
        self.assertEqual(phase["status"], "done")
        self.assertEqual(phase["video_sha256"], _sha(self.video))
        words = json.loads(self.state.phase_words_path.read_text(encoding="utf-8"))
        self.assertEqual(words["phases"], {"P1": "beim Abschrauben der Rückseite"})

    def test_a_phase_tag_runs_once(self):
        run_phase(self.state, "P1", "a", self.video, FakeRunner(), PYTHON)
        with self.assertRaisesRegex(SessionError, "already recorded"):
            run_phase(self.state, "P1", "a", self.video, FakeRunner(), PYTHON)

    def test_a_failed_phase_keeps_its_folder_and_blocks_nothing_but_its_tag(self):
        with self.assertRaisesRegex(SessionError, "failed"):
            run_phase(self.state, "P1", "a", self.video, FakeRunner(returncode=1, write=False), PYTHON)
        phase = SessionState.load(self.root).phase("P1")
        self.assertEqual(phase["status"], "failed")
        self.assertIn("custody step failed", phase["error"])
        self.assertTrue(self.state.run_dir("P1").is_dir())
        run_phase(self.state, "P2", "a", self.video, FakeRunner(), PYTHON)
        self.assertEqual(self.state.done_runs(), [self.state.run_dir("P2")])

    def test_an_unfinished_phase_stops_the_next_one(self):
        self.state.begin_phase("P1", "a", self.video, "0" * 64)  # as after a crash during processing
        with self.assertRaisesRegex(SessionError, "never finished"):
            run_phase(SessionState.load(self.root), "P2", "a", self.video, FakeRunner(), PYTHON)

    def test_a_run_of_another_video_is_rejected(self):
        with self.assertRaisesRegex(SessionError, "different video"):
            run_phase(self.state, "P1", "a", self.video, FakeRunner(video_sha256="f" * 64), PYTHON)
        self.assertEqual(SessionState.load(self.root).phase("P1")["status"], "failed")

    def test_bad_tags_labels_and_videos_are_refused_before_anything_runs(self):
        runner = FakeRunner()
        for tag, label, video in (("phase1", "a", self.video), ("P1", "  ", self.video),
                                  ("P1", "a", self.tmp / "missing.mp4")):
            with self.subTest(tag=tag, label=label):
                with self.assertRaises(SessionError):
                    run_phase(self.state, tag, label, video, runner, PYTHON)
        self.assertEqual(runner.commands, [])

    def test_the_command_names_custody_config_and_label(self):
        command = session_command(PYTHON, self.video, "P2", "beim Öffnen", self.state.memory_dir,
                                  self.state.run_dir("P2"))
        self.assertEqual(command[command.index("--config") + 1], "custody-config.v15.json")
        self.assertEqual(command[command.index("--phase-label") + 1], "beim Öffnen")


class CliTest(unittest.TestCase):
    def test_sessions_inside_the_repository_are_refused(self):
        inside = Path(__file__).resolve().parent / "tmp-session"
        self.assertEqual(anastylos.main(["new", "--session", str(inside), "--object", "x", "--operator", "y"]), 1)
        self.assertFalse(inside.exists())

    def test_new_and_status_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "s"
            self.assertEqual(anastylos.main(["new", "--session", str(root), "--object", "x", "--operator", "y"]), 0)
            self.assertTrue((root / STATE_NAME).is_file())
            self.assertEqual(anastylos.main(["status", "--session", str(root)]), 0)


if __name__ == "__main__":
    unittest.main()
