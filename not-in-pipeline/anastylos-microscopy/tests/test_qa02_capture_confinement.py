"""QA-02 (v0.1.1): capture-root confinement for capture-manifest frames.

Linux/macOS emulate the Windows junction escape with a directory symlink.
The real NTFS junction test runs only on Windows (``mklink /J``) and is
reported as SKIPPED elsewhere - a POSIX symlink is NOT a Windows junction
verification.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from _support import CLOCK, cv2, resistor_image, tmpdir

from anastylos_microscopy import capture, cli
from anastylos_microscopy.runner import inspect_manifest
from anastylos_microscopy.util import sha256_bytes

PKG = Path(__file__).resolve().parents[1]
CAN_SYMLINK = hasattr(os, "symlink")


def _jpeg(seq=("brown", "black", "red", "gold")) -> bytes:
    ok, buffer = cv2.imencode(".jpg", resistor_image(seq))
    assert ok
    return buffer.tobytes()


def _symlink_ok(tmp: Path) -> bool:
    try:
        os.symlink(tmp / "nowhere", tmp / ".probe")
        os.unlink(tmp / ".probe")
        return True
    except (OSError, NotImplementedError):
        return False


class Capture:
    """A capture dir 'cap' (manifest root) next to an unauthorized dir 'outside'."""

    def __init__(self, testcase):
        self.base = tmpdir(testcase)
        self.root = self.base / "cap"
        self.outside = self.base / "outside"
        (self.root / "frames").mkdir(parents=True)
        self.outside.mkdir()
        self.inside_bytes = _jpeg()
        self.secret_bytes = _jpeg(("yellow", "violet", "red", "gold"))
        (self.outside / "escape.jpg").write_bytes(self.secret_bytes)
        self.secret_sha = sha256_bytes(self.secret_bytes)

    def manifest(self, frames) -> Path:
        doc = {"schema": "anastylos-microscopy-capture-manifest/1", "validation_class": "synthetic",
               "source": {"camera_id": "qa02-test"}, "frames": frames}
        path = self.root / "capture-manifest.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def frame(self, file, data=None):
        return {"file": file, "sha256": sha256_bytes(data if data is not None else self.inside_bytes)}

    def assert_secret_unprocessed(self, testcase, out: Path):
        """No unauthorized byte reached intake: no record, no evidence copy, no hash anywhere."""
        if out.exists():
            for item in out.rglob("*"):
                testcase.assertNotIn(self.secret_sha, item.name)
                if item.is_file():
                    testcase.assertNotIn(self.secret_sha.encode(), item.read_bytes())
                    testcase.assertNotEqual(item.read_bytes(), self.secret_bytes)


class LexicalTest(unittest.TestCase):
    def test_lexical_rejections(self):
        bad = ["../x.jpg", "frames/../../x.jpg", "frames/./x.jpg", "/etc/passwd", "C:\\x.jpg",
               "C:x.jpg", "\\\\server\\share\\x.jpg", "\\\\?\\C:\\x.jpg", "frames\\..\\..\\x.jpg",
               "frames/x.jpg:stream", "frames/CON", "frames/nul.jpg", "frames/x.jpg.", "frames /x.jpg",
               "frames/x\x00.jpg", "", "   "]
        for text in bad:
            with self.assertRaises(capture.ManifestError, msg=repr(text)):
                capture.safe_relative(text)
        self.assertEqual(str(capture.safe_relative("frames\\a\\b.jpg")), "frames/a/b.jpg")

    def test_is_within(self):
        self.assertTrue(capture.is_within("/a/b/c", "/a/b"))
        self.assertTrue(capture.is_within("/a/b", "/a/b"))
        self.assertFalse(capture.is_within("/a/bc/d", "/a/b"))
        self.assertFalse(capture.is_within("/a", "/a/b"))


class ConfinementTest(unittest.TestCase):
    def _run(self, cap: Capture, frames):
        mpath = cap.manifest(frames)
        out = cap.base / "out"
        batch, _ = inspect_manifest(mpath, out, mode="resistor", clock=CLOCK)
        validate = cli.main(["validate", "--kind", "capture_manifest", str(mpath)])
        return batch, validate, out

    # 1
    def test_normal_frame(self):
        cap = Capture(self)
        (cap.root / "frames" / "f1.jpg").write_bytes(cap.inside_bytes)
        batch, validate, _ = self._run(cap, [cap.frame("frames/f1.jpg")])
        self.assertEqual(validate, 0)
        self.assertEqual(batch["entries"][0]["processing"], "completed")
        self.assertEqual(batch["exit_code"], 0)

    # 2
    def test_nested_relative_frame(self):
        cap = Capture(self)
        nested = cap.root / "frames" / "session1" / "pass2"
        nested.mkdir(parents=True)
        (nested / "f.jpg").write_bytes(cap.inside_bytes)
        batch, validate, _ = self._run(cap, [cap.frame("frames/session1/pass2/f.jpg"),
                                             cap.frame("frames\\session1\\pass2\\f.jpg")])
        self.assertEqual(validate, 1, "duplicate frame is still a manifest error")
        self.assertEqual(batch["entries"][0]["processing"], "completed")
        batch, validate, _ = self._run(cap, [cap.frame("frames/session1/pass2/f.jpg")])
        self.assertEqual(validate, 0)

    # 3
    def test_dotdot_traversal(self):
        cap = Capture(self)
        batch, validate, out = self._run(cap, [cap.frame("../outside/escape.jpg", cap.secret_bytes)])
        self.assertEqual(validate, 1)
        self.assertEqual(batch["entries"][0]["processing"], "manifest_rejected")
        self.assertEqual(batch["exit_code"], 1)
        cap.assert_secret_unprocessed(self, out)

    # 4
    def test_absolute_outside_path(self):
        cap = Capture(self)
        target = str(cap.outside / "escape.jpg")
        batch, validate, out = self._run(cap, [cap.frame(target, cap.secret_bytes)])
        self.assertEqual(validate, 1)
        self.assertIn("relative", batch["entries"][0]["error"])
        cap.assert_secret_unprocessed(self, out)

    # 5
    def test_missing_frame(self):
        cap = Capture(self)
        batch, validate, _ = self._run(cap, [cap.frame("frames/missing.jpg")])
        self.assertEqual(validate, 1)
        self.assertEqual(batch["entries"][0]["rejection"], "frame_missing")
        self.assertEqual(batch["exit_code"], 1)

    # 6
    @unittest.skipUnless(CAN_SYMLINK, "no symlink support")
    def test_symlink_inside_root_allowed(self):
        cap = Capture(self)
        if not _symlink_ok(cap.base):
            self.skipTest("symlink creation not permitted on this host")
        (cap.root / "frames" / "real.jpg").write_bytes(cap.inside_bytes)
        os.symlink(cap.root / "frames" / "real.jpg", cap.root / "frames" / "alias.jpg")
        (cap.root / "store").mkdir()
        (cap.root / "store" / "f.jpg").write_bytes(cap.inside_bytes)
        os.symlink(cap.root / "store", cap.root / "frames" / "linkdir", target_is_directory=True)
        batch, validate, out = self._run(cap, [cap.frame("frames/alias.jpg"),
                                               cap.frame("frames/linkdir/f.jpg")])
        self.assertEqual(validate, 0)
        self.assertEqual([e["processing"] for e in batch["entries"]], ["completed", "completed"])
        record = json.loads((out / batch["entries"][1]["inspection_json"]).read_text("utf-8"))
        links = record["source"]["capture_manifest"]["confinement"]["links_followed"]
        self.assertEqual(links, [{"component": "frames/linkdir", "kind": "symlink"}])

    # 7
    @unittest.skipUnless(CAN_SYMLINK, "no symlink support")
    def test_file_symlink_outside_root_rejected(self):
        cap = Capture(self)
        if not _symlink_ok(cap.base):
            self.skipTest("symlink creation not permitted on this host")
        os.symlink(cap.outside / "escape.jpg", cap.root / "frames" / "escape.jpg")
        batch, validate, out = self._run(cap, [cap.frame("frames/escape.jpg", cap.secret_bytes)])
        self.assertEqual(validate, 1)
        self.assertEqual(batch["entries"][0]["rejection"], "outside_capture_root")
        cap.assert_secret_unprocessed(self, out)

    # 8 (POSIX emulation of the QA-reproduced junction escape: frames/ -> outside dir)
    @unittest.skipUnless(CAN_SYMLINK, "no symlink support")
    def test_directory_link_outside_root_rejected(self):
        cap = Capture(self)
        if not _symlink_ok(cap.base):
            self.skipTest("symlink creation not permitted on this host")
        os.rmdir(cap.root / "frames")
        os.symlink(cap.outside, cap.root / "frames", target_is_directory=True)
        batch, validate, out = self._run(cap, [cap.frame("frames/escape.jpg", cap.secret_bytes)])
        self.assertEqual(validate, 1)
        self.assertEqual(batch["entries"][0]["rejection"], "outside_capture_root")
        self.assertIsNone(batch["entries"][0]["inspection_json"])
        cap.assert_secret_unprocessed(self, out)

    # 8 (real Windows junction)
    @unittest.skipUnless(os.name == "nt", "Windows directory junctions need NTFS on Windows "
                                          "(PENDING on non-Windows hosts)")
    def test_windows_junction_outside_root_rejected(self):
        cap = Capture(self)
        os.rmdir(cap.root / "frames")
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(cap.root / "frames"),
                               str(cap.outside)], capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest(f"mklink /J failed: {proc.stdout} {proc.stderr}")
        self.assertTrue(os.path.isjunction(cap.root / "frames"))
        batch, validate, out = self._run(cap, [cap.frame("frames/escape.jpg", cap.secret_bytes)])
        self.assertEqual(validate, 1)
        self.assertEqual(batch["entries"][0]["rejection"], "outside_capture_root")
        cap.assert_secret_unprocessed(self, out)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_windows_junction_inside_root_allowed(self):
        cap = Capture(self)
        (cap.root / "store").mkdir()
        (cap.root / "store" / "f.jpg").write_bytes(cap.inside_bytes)
        os.rmdir(cap.root / "frames")
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(cap.root / "frames"),
                               str(cap.root / "store")], capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest(f"mklink /J failed: {proc.stdout} {proc.stderr}")
        batch, validate, _ = self._run(cap, [cap.frame("frames/f.jpg")])
        self.assertEqual(validate, 0)
        self.assertEqual(batch["entries"][0]["processing"], "completed")

    # 9
    @unittest.skipUnless(CAN_SYMLINK, "no symlink support")
    def test_broken_link_rejected(self):
        cap = Capture(self)
        if not _symlink_ok(cap.base):
            self.skipTest("symlink creation not permitted on this host")
        os.symlink(cap.root / "frames" / "gone.jpg", cap.root / "frames" / "dangling.jpg")
        os.symlink(cap.root / "loop2", cap.root / "loop1")
        os.symlink(cap.root / "loop1", cap.root / "loop2")
        batch, validate, _ = self._run(cap, [cap.frame("frames/dangling.jpg"),
                                             cap.frame("loop1/f.jpg")])
        self.assertEqual(validate, 1)
        self.assertEqual([e["rejection"] for e in batch["entries"]],
                         ["unresolvable_link", "unresolvable_link"])

    def test_hard_link_rejected_fail_closed(self):
        cap = Capture(self)
        try:
            os.link(cap.outside / "escape.jpg", cap.root / "frames" / "hard.jpg")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hard links unavailable: {exc}")
        batch, validate, out = self._run(cap, [cap.frame("frames/hard.jpg", cap.secret_bytes)])
        self.assertEqual(validate, 1)
        self.assertEqual(batch["entries"][0]["rejection"], "multiple_hard_links")
        cap.assert_secret_unprocessed(self, out)

    def test_inspected_bytes_are_the_validated_bytes(self):
        cap = Capture(self)
        (cap.root / "frames" / "f1.jpg").write_bytes(cap.inside_bytes)
        mpath = cap.manifest([cap.frame("frames/f1.jpg")])
        reads = []
        real_read = capture.read_frame

        def spy(root, rel, *a, **k):
            data, report = real_read(root, rel, *a, **k)
            reads.append(sha256_bytes(data))
            # Replace the file AFTER the confined read: the record must still describe the
            # bytes that were read, hashed and preserved (no second open of the path).
            (cap.root / "frames" / "f1.jpg").write_bytes(cap.secret_bytes)
            return data, report

        with mock.patch.object(capture, "read_frame", spy):
            batch, path = inspect_manifest(mpath, cap.base / "out", mode="resistor", clock=CLOCK)
        record = json.loads((path.parent / batch["entries"][0]["inspection_json"]).read_text("utf-8"))
        self.assertEqual(record["image"]["sha256"], reads[0])
        evidence = [a for a in record["artifacts"] if a["role"] == "original_copy"][0]
        self.assertEqual(sha256_bytes((path.parent / record["inspection_id"] / evidence["path"])
                                      .read_bytes()), reads[0])
        self.assertTrue(record["source"]["capture_manifest"]["confinement"]["single_read"])

    # 10 race-sensitive: a directory is swapped for a link to the outside between check and open
    @unittest.skipUnless(CAN_SYMLINK and os.name != "nt", "POSIX rename/symlink race emulation")
    def test_swap_between_check_and_open_rejected(self):
        cap = Capture(self)
        if not _symlink_ok(cap.base):
            self.skipTest("symlink creation not permitted on this host")
        (cap.root / "frames" / "escape.jpg").write_bytes(cap.inside_bytes)
        real_open = os.open
        state = {"swapped": False}

        def racing_open(path, flags, *args, **kwargs):
            if not state["swapped"] and str(path).endswith("escape.jpg"):
                os.rename(cap.root / "frames", cap.root / "frames.orig")
                os.symlink(cap.outside, cap.root / "frames", target_is_directory=True)
                state["swapped"] = True
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(capture.os, "open", racing_open):
            with self.assertRaises(capture.ConfinementError) as ctx:
                capture.read_frame(capture.capture_root(cap.root / "m.json"),
                                   PurePosixPath("frames/escape.jpg"))
        self.assertTrue(state["swapped"])
        self.assertEqual(ctx.exception.code, "changed_during_read")

    @unittest.skipUnless(CAN_SYMLINK and os.name != "nt", "POSIX rename/symlink race emulation")
    def test_swap_and_restore_around_open_rejected(self):
        cap = Capture(self)
        if not _symlink_ok(cap.base):
            self.skipTest("symlink creation not permitted on this host")
        (cap.root / "frames" / "escape.jpg").write_bytes(cap.inside_bytes)
        real_open = os.open

        def racing_open(path, flags, *args, **kwargs):
            if str(path).endswith("escape.jpg"):
                os.rename(cap.root / "frames", cap.root / "frames.orig")
                os.symlink(cap.outside, cap.root / "frames", target_is_directory=True)
                try:
                    return real_open(path, flags, *args, **kwargs)
                finally:  # attacker restores the original before the post-open check
                    os.unlink(cap.root / "frames")
                    os.rename(cap.root / "frames.orig", cap.root / "frames")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(capture.os, "open", racing_open):
            with self.assertRaises(capture.ConfinementError) as ctx:
                capture.read_frame(capture.capture_root(cap.root / "m.json"),
                                   PurePosixPath("frames/escape.jpg"))
        self.assertEqual(ctx.exception.code, "changed_during_read")

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW not available")
    def test_final_component_swapped_to_symlink_rejected(self):
        cap = Capture(self)
        if not _symlink_ok(cap.base):
            self.skipTest("symlink creation not permitted on this host")
        target = cap.root / "frames" / "f.jpg"
        target.write_bytes(cap.inside_bytes)
        real_open = os.open

        def racing_open(path, flags, *args, **kwargs):
            if str(path).endswith("f.jpg") and not os.path.islink(target):
                os.unlink(target)
                os.symlink(cap.outside / "escape.jpg", target)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(capture.os, "open", racing_open):
            with self.assertRaises(capture.ConfinementError) as ctx:
                capture.read_frame(capture.capture_root(cap.root / "m.json"),
                                   PurePosixPath("frames/f.jpg"))
        self.assertIn(ctx.exception.code, ("open_failed", "changed_during_read"))

    def test_validate_and_inspect_agree(self):
        cap = Capture(self)
        (cap.root / "frames" / "ok.jpg").write_bytes(cap.inside_bytes)
        frames = [cap.frame("frames/ok.jpg"), cap.frame("../outside/escape.jpg"),
                  cap.frame("frames/missing.jpg")]
        if CAN_SYMLINK and _symlink_ok(cap.base):
            os.symlink(cap.outside, cap.root / "frames" / "out", target_is_directory=True)
            frames.append(cap.frame("frames/out/escape.jpg", cap.secret_bytes))
        mpath = cap.manifest(frames)
        errors = capture.verify(mpath)
        batch, _ = inspect_manifest(mpath, cap.base / "out", mode="resistor", clock=CLOCK)
        rejected = [e["file"] for e in batch["entries"] if e["processing"] == "manifest_rejected"]
        self.assertEqual(len(errors), len(frames) - 1)
        self.assertEqual(len(rejected), len(frames) - 1)
        self.assertEqual(batch["exit_code"], 4)
        cap.assert_secret_unprocessed(self, cap.base / "out")

    def test_replay_of_manifest_record_is_confined(self):
        cap = Capture(self)
        (cap.root / "frames" / "f.jpg").write_bytes(cap.inside_bytes)
        mpath = cap.manifest([cap.frame("frames/f.jpg")])
        batch, path = inspect_manifest(mpath, cap.base / "a", mode="resistor", clock=CLOCK)
        record_path = path.parent / batch["entries"][0]["inspection_json"]
        from anastylos_microscopy.runner import replay
        self.assertTrue(replay(record_path, cap.base / "b")["identical"])
        if CAN_SYMLINK and _symlink_ok(cap.base):
            os.rename(cap.root / "frames", cap.root / "frames.orig")
            os.symlink(cap.outside, cap.root / "frames", target_is_directory=True)
            (cap.outside / "f.jpg").write_bytes(cap.inside_bytes)
            with self.assertRaises(capture.ConfinementError):
                replay(record_path, cap.base / "c")


if __name__ == "__main__":
    unittest.main()
