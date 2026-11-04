"""MIC-01..05 harness on the labelled SYNTHETIC example set, plus isolation checks.

These results prove harness wiring only. They are not MUSTOOL / MSI GTX 970
hardware validation (there are no real microscope photos in this package).
"""

from __future__ import annotations

import re
import shutil
import unittest
from pathlib import Path

from _support import CLOCK, MissingTesseract, SizeKeyedOCR, tmpdir

from anastylos_microscopy import cli, schema
from anastylos_microscopy.evaluation import evaluate, harness_fingerprint

PKG = Path(__file__).resolve().parents[1]
DATASET = PKG / "examples" / "synthetic_mic" / "dataset.json"
OCR_TABLE = {360: ["472"], 520: ["R12", "C5"], 480: ["LM358N"]}


class EvaluationTest(unittest.TestCase):
    def _outcomes(self, report):
        return {row["id"]: row["outcome"] for row in report["items"]}

    def test_dev_split_with_deterministic_stub_engine(self):
        report, path = evaluate(DATASET, tmpdir(self), split="dev", clock=CLOCK,
                                ocr_backend=SizeKeyedOCR(OCR_TABLE))
        outcomes = self._outcomes(report)
        self.assertEqual(outcomes["mic01-472"], "correct")
        self.assertEqual(outcomes["mic01-unmarked"], "correct_abstention")
        self.assertEqual(outcomes["mic02-silk"], "correct")
        self.assertEqual(outcomes["mic03-lm358"], "correct")
        self.assertEqual(outcomes["mic04-ic-query"], "correct")
        self.assertEqual(outcomes["mic04-negative"], "correct_abstention")
        self.assertEqual(outcomes["mic05-uniform"], "correct_abstention")
        self.assertEqual(outcomes["mic05-blur"], "correct_abstention")
        # v0.1.0 finding QA-03: the unchanged resistor reader still reads the synthetic glare
        # image as 'ok' 22.9 kOhm (asserted on the native result in test_qa03_*). Since v0.1.1
        # the Core evidence assessment holds that verdict as ambiguous, so the harness scores
        # an abstention. (v0.1.0 asserted "false_confident" here.)
        self.assertEqual(outcomes["mic05-glare"], "correct_abstention")
        self.assertNotIn("mic01-103", outcomes, "held-out item must not run on the dev split")
        self.assertEqual(report["hardware_validation"], "not_claimed")
        self.assertEqual(report["gtx970_validation"], "not_claimed")
        self.assertEqual(report["validation_class"], "synthetic")
        self.assertTrue(path.is_file())

    def test_dev_split_without_ocr_reports_unavailable(self):
        report, _ = evaluate(DATASET, tmpdir(self), split="dev", clock=CLOCK,
                             ocr_backend=MissingTesseract())
        outcomes = self._outcomes(report)
        for key in ("mic01-472", "mic01-unmarked", "mic02-silk", "mic03-lm358"):
            self.assertEqual(outcomes[key], "unavailable", key)

    def test_heldout_requires_frozen_fingerprint(self):
        with self.assertRaises(ValueError):
            evaluate(DATASET, tmpdir(self), split="heldout", clock=CLOCK)
        with self.assertRaises(ValueError):
            evaluate(DATASET, tmpdir(self), split="heldout", frozen_config="0" * 64, clock=CLOCK)
        report, _ = evaluate(DATASET, tmpdir(self), split="heldout",
                             frozen_config=harness_fingerprint(), clock=CLOCK,
                             ocr_backend=SizeKeyedOCR(OCR_TABLE))
        self.assertTrue(report["heldout_access"])
        self.assertEqual([row["id"] for row in report["items"]], ["mic01-103"])

    def test_example_documents_validate(self):
        self.assertEqual(schema.validate_named(__import__("json").loads(
            DATASET.read_text(encoding="utf-8")), "dataset"), [])
        manifest = PKG / "examples/synthetic_mic/capture_node_example/capture-manifest.json"
        self.assertEqual(cli.main(["validate", "--kind", "capture_manifest", str(manifest)]), 0)
        calib = PKG / "examples/calibration.SYNTHETIC-EXAMPLE.json"
        self.assertEqual(cli.main(["validate", "--kind", "calibration", str(calib)]), 0)

    def test_cli_inspect_on_capture_example(self):
        out = tmpdir(self)
        manifest = PKG / "examples/synthetic_mic/capture_node_example/capture-manifest.json"
        code = cli.main(["inspect", "--manifest", str(manifest), "--mode", "resistor",
                         "--clock", CLOCK, "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue((out / "batch.json").is_file())


class IsolationTest(unittest.TestCase):
    SRC = PKG / "src"

    def _sources(self):
        return [p for p in self.SRC.rglob("*.py")]

    def test_no_blobcam_runtime_dependency(self):
        for path in self._sources():
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"^\s*(import|from)\s+blobcam", text, re.M), path)

    def test_no_assembly_memory_or_network_or_subprocess_use(self):
        banned = [r"^\s*(import|from)\s+assembly", r"assembly-memory-calibration", r"\bsubprocess\b", r"\burllib\b", r"\brequests\b",
                  r"\bsocket\b", r"os\.system"]
        for path in self._sources():
            text = path.read_text(encoding="utf-8").lower()
            for pattern in banned:
                self.assertIsNone(re.search(pattern, text), f"{pattern} in {path}")

    def test_no_tray_counter_or_missing_parts_in_registry(self):
        from anastylos_microscopy.adapters import REGISTRY
        joined = " ".join(REGISTRY).lower()
        self.assertNotIn("tray", joined)
        self.assertNotIn("missing", joined)

    def test_inspection_never_writes_into_anastylos_root(self):
        from anastylos_microscopy import env
        root = env.resolve_root(None)
        before = sorted(str(p) for p in root.rglob("*") if "__pycache__" not in p.parts)
        out = tmpdir(self)
        evaluate(DATASET, out, split="dev", clock=CLOCK, ocr_backend=SizeKeyedOCR(OCR_TABLE))
        after = sorted(str(p) for p in root.rglob("*") if "__pycache__" not in p.parts)
        self.assertEqual(before, after)
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
