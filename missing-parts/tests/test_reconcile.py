"""Inventory reconciliation checks on synthetic comparisons.

Proves obligation logic (confirmed only by confirmation, visual
candidates linked or left unassigned), not real-world accuracy.

Run from the missing-parts directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2

from anastylos_missing import synthetic as S
from anastylos_missing.compare import run_comparison
from anastylos_missing.context import load_context
from anastylos_missing.reconcile import run_reconciliation

PPM = 4.0


def setup_pair(directory: Path, name: str, images, frame="fixture_back"):
    opath, cpath = directory / f"{name}_o.png", directory / f"{name}_c.png"
    cv2.imwrite(str(opath), images[0])
    cv2.imwrite(str(cpath), images[1])
    ctx = directory / f"{name}_ctx.json"
    ctx.write_text(json.dumps({"original": {"role": "original_assembly", "frame": frame},
                               "current": {"role": "reassembly_state", "frame": frame},
                               "surface": "light"}), encoding="utf-8")
    meta = {"px_per_mm": PPM, "view": frame, "origin": None, "provenance": "test"}
    return run_comparison(original=str(opath), current=str(cpath),
                          context=load_context(ctx), frame_meta=meta,
                          comparison_label=name)


def px_box_to_mm(bbox):
    return [round(bbox[0] / PPM, 2), round(bbox[1] / PPM, 2),
            round(bbox[2] / PPM, 2), round(bbox[3] / PPM, 2)]


def expected_at(ident, bbox_px, frame="fixture_back", source="test list"):
    return {"id": ident, "bbox_mm": px_box_to_mm(bbox_px), "frame": frame,
            "source": source, "note": None}


class ObligationTest(unittest.TestCase):
    def test_absent_visual_evidence_opens_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _, _, truth = S.removed()
            comparison = setup_pair(tmpdir, "rm", S.removed())
            report = run_reconciliation(
                comparison, [expected_at("e1", truth["absent"][0])],
                {"source": "test", "entries": 1})
        self.assertEqual(report["obligations"][0]["status"], "candidate_absent")
        self.assertTrue(report["requires_confirmation"])
        self.assertIn("e1", report["confirmation_required_for"])
        self.assertEqual(report["unassigned_visual_candidates"], [])

    def test_unmatched_expectation_stays_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            comparison = setup_pair(tmpdir, "uc", S.unchanged())
            report = run_reconciliation(
                comparison, [expected_at("e1", [106, 126, 28, 28])],
                {"source": "test", "entries": 1})
        self.assertEqual(report["obligations"][0]["status"], "expected_unconfirmed")

    def test_shifted_evidence_reads_likely_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _, _, truth = S.shifted()
            comparison = setup_pair(tmpdir, "sh", S.shifted())
            report = run_reconciliation(
                comparison, [expected_at("e1", truth["present"][0])],
                {"source": "test", "entries": 1})
        self.assertEqual(report["obligations"][0]["status"], "likely_present")

    def test_unassigned_candidates_listed_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            comparison = setup_pair(tmpdir, "ua", S.removed())
            report = run_reconciliation(
                comparison, [expected_at("e1", [300, 300, 20, 20])],
                {"source": "test", "entries": 1})
        self.assertEqual(report["obligations"][0]["status"], "expected_unconfirmed")
        self.assertEqual(report["unassigned_visual_candidates"], ["R1"])

    def test_frame_mismatch_keeps_obligation_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _, _, truth = S.removed()
            comparison = setup_pair(tmpdir, "fm", S.removed())
            report = run_reconciliation(
                comparison, [expected_at("e1", truth["absent"][0], frame="surface_a3")],
                {"source": "test", "entries": 1})
        item = report["obligations"][0]
        self.assertEqual(item["status"], "expected_unconfirmed")
        self.assertTrue(any("no common validated frame" in r for r in item["reasons"]))

    def test_unavailable_comparison_keeps_obligations_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            comparison = setup_pair(tmpdir, "uv", S.shifted_view())
            self.assertEqual(comparison["verdict"], "unavailable")
            report = run_reconciliation(
                comparison, [expected_at("e1", [106, 126, 28, 28])],
                {"source": "test", "entries": 1})
        self.assertEqual(report["obligations"][0]["status"], "expected_unconfirmed")
        self.assertEqual(report["verdict"], "unavailable")

    def test_no_expectations_reconciles_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            comparison = setup_pair(tmpdir, "ne", S.removed())
            report = run_reconciliation(comparison, [], {"source": None, "entries": 0})
        self.assertEqual(report["verdict"], "ok")
        self.assertEqual(report["unassigned_visual_candidates"], ["R1"])

    def test_overlapping_evidence_is_contradictory(self):
        comparison = {"comparison_id": "cmp-test", "verdict": "ok",
                      "frame": {"name": "fixture_back", "mm_validated": True},
                      "regions": [
                          {"label": "R1", "classification": "candidate_absent_region",
                           "bbox_mm": [26.0, 31.0, 7, 7], "centroid_mm": [29.5, 34.5]},
                          {"label": "R2", "classification": "shifted_present",
                           "bbox_mm": [26.0, 31.0, 7, 7], "centroid_mm": [29.5, 34.5]}]}
        report = run_reconciliation(
            comparison, [expected_at("e1", [104, 124, 28, 28])],
            {"source": "test", "entries": 1})
        item = report["obligations"][0]
        self.assertIsNotNone(item["contradiction"])
        self.assertEqual(report["verdict"], "ambiguous")


class ConfirmationTest(unittest.TestCase):
    def test_missing_confirmation_confirms(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _, _, truth = S.removed()
            comparison = setup_pair(tmpdir, "cm", S.removed())
            report = run_reconciliation(
                comparison, [expected_at("e1", truth["absent"][0])],
                {"source": "test", "entries": 1},
                [{"expected_id": "e1", "confirmed_by": "operator",
                  "statement": "missing"}])
        item = report["obligations"][0]
        self.assertEqual(item["status"], "confirmed_missing")
        self.assertFalse(item["requires_confirmation"])

    def test_present_confirmation_confirms(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _, _, truth = S.unchanged()
            comparison = setup_pair(tmpdir, "cp", S.unchanged())
            report = run_reconciliation(
                comparison, [expected_at("e1", truth["present"][0])],
                {"source": "test", "entries": 1},
                [{"expected_id": "e1", "confirmed_by": "operator",
                  "statement": "present"}])
        self.assertEqual(report["obligations"][0]["status"], "confirmed_present")

    def test_vision_alone_never_confirms(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _, _, truth = S.removed()
            comparison = setup_pair(tmpdir, "nc", S.removed())
            report = run_reconciliation(
                comparison, [expected_at("e1", truth["absent"][0])],
                {"source": "test", "entries": 1})
        statuses = {item["status"] for item in report["obligations"]}
        self.assertNotIn("confirmed_missing", statuses)
        self.assertNotIn("confirmed_present", statuses)

    def test_unknown_confirmation_target_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            comparison = setup_pair(tmpdir, "ut", S.unchanged())
            with self.assertRaises(ValueError):
                run_reconciliation(comparison, [], {"source": "test", "entries": 0},
                                   [{"expected_id": "ghost", "confirmed_by": "op",
                                     "statement": "missing"}])


class MemorySourceTest(unittest.TestCase):
    def test_memory_log_expectations_reconcile(self):
        from anastylos_missing.inputs import load_expected
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _, _, truth = S.removed()
            comparison = setup_pair(tmpdir, "mm", S.removed())
            log = tmpdir / "memory.jsonl"
            log.write_text(json.dumps(
                {"id": "obs-0007", "type": "observation", "session": "day1",
                 "source_view": "fixture_back",
                 "bbox_mm": px_box_to_mm(truth["absent"][0]),
                 "centroid_mm": [29.5, 34.5],
                 "evidence": {"result_json": "r.json", "pair_id": "p", "region": "R1"}}) + "\n",
                encoding="utf-8")
            before = log.read_text(encoding="utf-8")
            items, provenance = load_expected(log)
            report = run_reconciliation(comparison, items, provenance)
            self.assertEqual(report["obligations"][0]["status"], "candidate_absent")
            self.assertEqual(log.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
