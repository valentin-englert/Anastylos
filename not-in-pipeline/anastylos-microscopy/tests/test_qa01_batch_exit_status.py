"""QA-01 (v0.1.1): batch outcome aggregation and REAL process exit status.

Every CLI case runs `python -m anastylos_microscopy ...` in a child process
and asserts its return code; nothing here inspects printed text only.
Fault injection (reader / orchestrator exceptions) uses a tiny bootstrap that
patches one function and then calls the unchanged cli.main via sys.exit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from _support import CLOCK, ROOT, resistor_image, text_panel, tmpdir, write_png

from anastylos_microscopy import schema
from anastylos_microscopy.runner import exit_code_for, inspect_batch

PKG = Path(__file__).resolve().parents[1]
GLARE = PKG / "examples" / "synthetic_mic" / "images" / "unknown_glare.png"

BOOT_READER_CRASH = """
import sys
from anastylos_microscopy import adapters
def boom(self, image, roi, ctx):
    raise RuntimeError("injected reader crash (QA-01 test)")
adapters.ResistorAdapter.run = boom
from anastylos_microscopy.cli import main
sys.exit(main(sys.argv[1:]))
"""

BOOT_ORCHESTRATOR_CRASH = """
import sys
from anastylos_microscopy import runner
real = runner.inspect_image
def flaky(path, *a, **k):
    if "crash" in str(path):
        raise MemoryError("injected orchestrator crash (QA-01 test)")
    return real(path, *a, **k)
runner.inspect_image = flaky
from anastylos_microscopy.cli import main
sys.exit(main(sys.argv[1:]))
"""


def run_cli(args, *, root=True, boot=None):
    env = {k: v for k, v in os.environ.items() if k != "ANASTYLOS_ROOT"}
    env["PYTHONPATH"] = str(PKG / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if root:
        env["ANASTYLOS_ROOT"] = str(ROOT)
    cmd = [sys.executable, "-c", boot] if boot else [sys.executable, "-m", "anastylos_microscopy"]
    proc = subprocess.run(cmd + [str(a) for a in args], cwd=PKG, env=env, capture_output=True,
                          text=True, timeout=600)
    return proc


def batch_doc(out: Path) -> dict:
    doc = json.loads((out / "batch.json").read_text(encoding="utf-8"))
    errors = schema.validate_named(doc, "batch")
    assert not errors, errors
    return doc


class BatchExitStatusTest(unittest.TestCase):
    def _folder(self, files: dict) -> Path:
        base = tmpdir(self)
        folder = base / "in"
        folder.mkdir()
        for name, content in files.items():
            if isinstance(content, bytes):
                (folder / name).write_bytes(content)
            else:
                write_png(folder / name, content)
        return folder

    def _inspect(self, folder, *extra, root=True, boot=None):
        out = folder.parent / "out"
        proc = run_cli(["inspect", "--batch-dir", folder, "--clock", CLOCK, "--out", out, *extra],
                       root=root, boot=boot)
        return proc, out

    # 1
    def test_all_inputs_succeed_exit_0(self):
        folder = self._folder({"a.png": resistor_image(), "b.png": resistor_image(
            ("yellow", "violet", "red", "gold"))})
        proc, out = self._inspect(folder, "--mode", "resistor")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = batch_doc(out)
        self.assertEqual(doc["outcome"], "success")
        self.assertEqual(doc["exit_code"], 0)
        self.assertEqual(doc["outcome_counts"]["completed"], 2)
        self.assertEqual(doc["outcome_counts"]["candidates"], 2)

    # 2
    def test_one_valid_one_corrupt_is_partial_nonzero(self):
        folder = self._folder({"a_good.png": resistor_image(),
                               "b_corrupt.png": b"\x89PNG\r\n\x1a\ntruncated"})
        proc, out = self._inspect(folder, "--mode", "resistor")
        self.assertEqual(proc.returncode, 4, proc.stderr)
        doc = batch_doc(out)
        self.assertEqual(doc["outcome"], "partial_failure")
        self.assertEqual(doc["outcome_counts"]["completed"], 1)
        self.assertEqual(doc["outcome_counts"]["input_error"], 1)
        self.assertEqual([e["processing"] for e in doc["entries"]], ["completed", "input_error"])
        self.assertIsNotNone(doc["entries"][1]["inspection_json"], "per-image record kept")

    # 3
    def test_all_inputs_invalid_exit_1(self):
        folder = self._folder({"a.png": b"\x89PNG\r\n\x1a\nbroken", "b.jpg": b"\xff\xd8\xffnope",
                               "c.png": b"not an image at all"})
        proc, out = self._inspect(folder, "--mode", "resistor")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        doc = batch_doc(out)
        self.assertEqual(doc["outcome"], "failure")
        self.assertEqual(doc["outcome_counts"]["input_error"], 3)
        self.assertEqual(doc["count"], 3, "every input still gets an entry")

    # 4
    def test_missing_anastylos_root_is_fatal_exit_3_before_processing(self):
        folder = self._folder({"a.png": resistor_image(), "b.png": resistor_image(),
                               "c.png": resistor_image()})
        proc, out = self._inspect(folder, "--mode", "resistor", root=False)
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertIn("ANASTYLOS_ROOT", proc.stderr)
        self.assertFalse((out / "batch.json").exists(), "fatal setup failure writes nothing")
        proc, _ = self._inspect(folder, "--mode", "resistor", "--anastylos-root",
                                folder.parent / "does-not-exist", root=False)
        self.assertEqual(proc.returncode, 3)

    def test_missing_root_manifest_and_single_image_exit_3(self):
        manifest = PKG / "examples/synthetic_mic/capture_node_example/capture-manifest.json"
        out = tmpdir(self) / "o"
        proc = run_cli(["inspect", "--manifest", manifest, "--mode", "resistor", "--out", out],
                       root=False)
        self.assertEqual(proc.returncode, 3)
        proc = run_cli(["inspect", "--image", GLARE, "--mode", "resistor", "--out", out], root=False)
        self.assertEqual(proc.returncode, 3)

    # 5
    def test_reader_exception_in_every_image_exit_1(self):
        folder = self._folder({"a.png": resistor_image(), "b.png": resistor_image()})
        proc, out = self._inspect(folder, "--mode", "resistor", boot=BOOT_READER_CRASH)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        doc = batch_doc(out)
        self.assertEqual(doc["outcome_counts"]["reader_error"], 2)
        self.assertEqual(doc["reader_status_counts"]["resistor"], {"reader_error": 2})

    def test_reader_exception_beside_working_reader_is_partial(self):
        folder = self._folder({"a.png": resistor_image()})
        proc, out = self._inspect(folder, "--mode", "multi", "--readers", "resistor,screw",
                                  boot=BOOT_READER_CRASH)
        self.assertEqual(proc.returncode, 4, proc.stderr)
        doc = batch_doc(out)
        self.assertEqual(doc["entries"][0]["processing"], "completed_with_reader_errors")
        self.assertEqual(doc["entries"][0]["reader_statuses"]["resistor"], "reader_error")

    def test_unexpected_orchestrator_exception_is_counted_crashed(self):
        folder = self._folder({"a_ok.png": resistor_image(), "b_crash.png": resistor_image()})
        proc, out = self._inspect(folder, "--mode", "resistor", boot=BOOT_ORCHESTRATOR_CRASH)
        self.assertEqual(proc.returncode, 4, proc.stderr)
        doc = batch_doc(out)
        self.assertEqual(doc["outcome_counts"]["crashed"], 1)
        self.assertEqual(doc["status_counts"].get("crashed"), 1)
        self.assertIn("MemoryError", doc["entries"][1]["error"])
        folder2 = self._folder({"x_crash.png": resistor_image(), "y_crash.png": resistor_image()})
        proc, out = self._inspect(folder2, "--mode", "resistor", boot=BOOT_ORCHESTRATOR_CRASH)
        self.assertEqual(proc.returncode, 1, "every inspection crashed -> nonzero")

    # 6
    def test_legitimate_abstention_is_not_a_crash_exit_0(self):
        import numpy as np
        uniform = np.full((200, 400, 3), 128, np.uint8)
        folder = self._folder({"a_uniform.png": uniform, "b_glare.png": __import__("cv2").imread(
            str(GLARE))})
        proc, out = self._inspect(folder, "--mode", "resistor")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = batch_doc(out)
        self.assertEqual(doc["outcome_counts"]["completed"], 2)
        self.assertEqual(doc["outcome_counts"]["abstentions"], 2)
        self.assertEqual(doc["outcome_counts"]["crashed"], 0)
        self.assertEqual(sorted(e["overall_status"] for e in doc["entries"]),
                         ["ambiguous", "unknown"])

    # 7
    def test_ocr_unavailable_only_reader_exit_3_not_counted_as_ocr_success(self):
        folder = self._folder({"a.png": text_panel("472"), "b.png": text_panel("103")})
        proc, out = self._inspect(folder, "--mode", "smd", "--tesseract-cmd",
                                  "tesseract-not-installed-anastylos-test")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        doc = batch_doc(out)
        self.assertEqual(doc["outcome"], "environment_unavailable")
        self.assertEqual(doc["outcome_counts"]["unavailable"], 2)
        self.assertEqual(doc["outcome_counts"]["completed"], 0)
        self.assertEqual(doc["ocr"]["readers"]["smd"], {"unavailable": 2})
        self.assertEqual(doc["ocr"]["unavailable_entries"], 2)

    def test_ocr_unavailable_beside_non_ocr_reader_is_surfaced(self):
        folder = self._folder({"a.png": text_panel("LM358N", size=(480, 140))})
        proc, out = self._inspect(folder, "--mode", "auto", "--hypothesis", "ic", "--tesseract-cmd",
                                  "tesseract-not-installed-anastylos-test")
        doc = batch_doc(out)
        entry = doc["entries"][0]
        self.assertEqual(entry["reader_statuses"]["markings"], "unavailable")
        self.assertEqual(doc["ocr"]["unavailable_entries"], 1)
        self.assertNotIn("ok", doc["ocr"]["readers"]["markings"])
        # polarity still produced an interpretable record -> processing completed, exit 0;
        # the unavailable OCR reader is reported, never counted as an OCR result.
        self.assertEqual(entry["processing"], "completed")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    # 8
    def test_empty_batch_exit_1(self):
        folder = self._folder({"notes.txt": b"no images here"})
        proc, out = self._inspect(folder, "--mode", "resistor")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("empty batch", proc.stderr)
        self.assertFalse((out / "batch.json").exists())
        batch, _ = inspect_batch([], tmpdir(self), mode="resistor", clock=CLOCK)
        self.assertEqual((batch["outcome"], batch["exit_code"], batch["count"]), ("empty", 1, 0))

    def test_single_image_exit_codes(self):
        base = tmpdir(self)
        good = write_png(base / "g.png", resistor_image())
        bad = base / "bad.png"
        bad.write_bytes(b"\x89PNG\r\n\x1a\nxx")
        self.assertEqual(run_cli(["inspect", "--image", good, "--mode", "resistor", "--out",
                                  base / "o"]).returncode, 0)
        self.assertEqual(run_cli(["inspect", "--image", bad, "--mode", "resistor", "--out",
                                  base / "o"]).returncode, 1)
        self.assertEqual(run_cli(["inspect", "--image", good, "--mode", "smd", "--tesseract-cmd",
                                  "tesseract-not-installed-anastylos-test", "--out",
                                  base / "o"]).returncode, 3)
        self.assertEqual(run_cli(["inspect", "--image", good, "--mode", "resistor", "--out", base / "o"],
                                 boot=BOOT_READER_CRASH).returncode, 1)

    def test_exit_code_table(self):
        self.assertEqual(exit_code_for([]), 1)
        self.assertEqual(exit_code_for(["completed", "completed"]), 0)
        self.assertEqual(exit_code_for(["completed", "input_error"]), 4)
        self.assertEqual(exit_code_for(["completed", "unavailable"]), 4)
        self.assertEqual(exit_code_for(["completed_with_reader_errors"]), 4)
        self.assertEqual(exit_code_for(["crashed", "crashed", "crashed"]), 1)
        self.assertEqual(exit_code_for(["manifest_rejected", "input_error"]), 1)
        self.assertEqual(exit_code_for(["unavailable", "unavailable"]), 3)
        self.assertEqual(exit_code_for(["unavailable", "input_error"]), 1)


if __name__ == "__main__":
    unittest.main()
