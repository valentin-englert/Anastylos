import copy
import json
from pathlib import Path
import unittest
from capture_gaps.planner import evaluate, fingerprint, CATALOG


class CaptureGapTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((Path(__file__).resolve().parents[1] / "capture_gaps/example.json").read_text())

    def candidate(self):
        self.doc["candidate"] = {"evidence_id": "image-2", "image_sha256": "b" * 64,
                                 "binding": copy.deepcopy(self.doc["gap"]["binding"])}
        return self.doc["candidate"]

    def confirm(self, c):
        review = {"accepted": True, "reviewer": "operator-test", "recorded_at": "2026-10-31T12:00:00Z",
                  "evidence_id": c["evidence_id"], "image_sha256": c["image_sha256"],
                  "gap_sha256": fingerprint(self.doc["gap"])}
        c["technical_review"] = dict(review)
        c["operator_confirmation"] = dict(review, criterion=CATALOG[self.doc["gap"]["kind"]][2])

    def test_instruction(self):
        r = evaluate(self.doc)
        self.assertEqual(r["status"], "open")
        self.assertIn("entire outer rim", r["instruction"])
        self.assertFalse(r["dispatch_allowed"])

    def test_no_operator_review(self):
        c = self.candidate(); self.confirm(c); del c["operator_confirmation"]
        self.assertEqual(evaluate(self.doc)["status"], "needs_review")

    def test_stale_binding(self):
        c = self.candidate(); self.confirm(c)
        self.doc["current_binding"]["revision"] = "r2"
        self.assertIn("Stale", evaluate(self.doc)["reason"])

    def test_duplicate_image(self):
        c = self.candidate(); c["image_sha256"] = "a" * 64; self.confirm(c)
        self.assertIn("Identical", evaluate(self.doc)["reason"])

    def test_closed_operator_basis_and_no_mutation(self):
        c = self.candidate(); self.confirm(c); before = copy.deepcopy(self.doc)
        r = evaluate(self.doc)
        self.assertEqual(r["status"], "closed")
        self.assertEqual(r["basis"], "operator_declared")
        self.assertEqual(self.doc, before)

    def test_candidate_binding(self):
        c = self.candidate(); self.confirm(c); c["binding"]["target"]["id"] = "other"
        self.assertEqual(evaluate(self.doc)["status"], "needs_review")

    def test_review_hash_and_content(self):
        c = self.candidate(); self.confirm(c)
        for key in ("image_sha256", "gap_sha256", "criterion", "evidence_id"):
            with self.subTest(key=key):
                d = copy.deepcopy(self.doc); d["candidate"]["operator_confirmation"][key] = "wrong"
                self.assertEqual(evaluate(d)["status"], "needs_review")

    def test_catalog(self):
        for kind in CATALOG:
            with self.subTest(kind=kind):
                d = copy.deepcopy(self.doc); d["gap"]["kind"] = kind
                self.assertEqual(evaluate(d)["status"], "open")

    def test_unsupported_and_bad_hash(self):
        self.doc["gap"]["kind"] = "automatic_detection"
        with self.assertRaises(ValueError): evaluate(self.doc)
        self.doc["gap"]["kind"] = "outer_boundary"
        self.doc["gap"]["binding"]["hashes"] = {"memory": "bad"}
        with self.assertRaises(ValueError): evaluate(self.doc)

    def test_frame_confirmation_can_review_existing_frame(self):
        self.doc["gap"]["kind"] = "frame_identity"
        c = self.candidate(); c["image_sha256"] = "a" * 64; self.confirm(c)
        self.assertEqual(evaluate(self.doc)["status"], "closed")
