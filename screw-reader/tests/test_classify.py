"""Classifier rule checks. Standard library only.

Run from the screw-reader directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_screw.classify import classify


def features(**overrides):
    base = {"sym2": 0.0, "sym4": 0.0, "sym6": 0.0, "eccentricity": 0.0,
            "fill": 0.2, "solidity": 1.0, "vertices": 4, "defects": 0,
            "lobes": 0, "ticks": 0.0, "contrast": 0.5}
    base.update(overrides)
    quality = {"contrast": base.pop("contrast")}
    return base, quality


class ClassifyTest(unittest.TestCase):
    def test_slotted(self):
        feats, quality = features(sym2=0.9, sym4=0.2, eccentricity=0.95, lobes=2)
        result = classify(feats, quality)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["class"], "slotted")

    def test_phillips(self):
        feats, quality = features(sym4=0.9, lobes=4, ticks=0.05, eccentricity=0.2)
        result = classify(feats, quality)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["class"], "phillips")

    def test_pozidriv(self):
        feats, quality = features(sym4=0.9, lobes=4, ticks=0.8, eccentricity=0.2)
        result = classify(feats, quality)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["class"], "pozidriv")

    def test_cross_lookalike_marginal_ticks(self):
        feats, quality = features(sym4=0.9, lobes=4, ticks=0.09, eccentricity=0.2)
        result = classify(feats, quality)
        self.assertEqual(result["status"], "ambiguous")
        names = [c["class"] for c in result["candidates"][:2]]
        self.assertEqual(names, ["phillips", "pozidriv"])

    def test_torx(self):
        feats, quality = features(sym6=0.9, lobes=6, solidity=0.86, vertices=12, defects=6)
        result = classify(feats, quality)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["class"], "torx")

    def test_hex(self):
        feats, quality = features(sym6=0.9, lobes=6, solidity=0.99, vertices=6, defects=0)
        result = classify(feats, quality)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["class"], "hex")

    def test_star_lookalike_marginal_shape(self):
        feats, quality = features(sym6=0.9, lobes=6, solidity=0.92, vertices=8, defects=3)
        result = classify(feats, quality)
        self.assertEqual(result["status"], "ambiguous")
        names = sorted(c["class"] for c in result["candidates"][:2])
        self.assertEqual(names, ["hex", "torx"])

    def test_rival_families_ambiguous(self):
        feats, quality = features(sym2=0.7, sym4=0.7, eccentricity=0.8, lobes=2,
                                  ticks=0.0, solidity=0.9, vertices=4)
        result = classify(feats, quality)
        self.assertEqual(result["status"], "ambiguous")
        names = sorted(c["class"] for c in result["candidates"][:2])
        self.assertEqual(names, ["phillips", "slotted"])

    def test_weak_evidence_unknown(self):
        feats, quality = features(sym2=0.2, sym4=0.2, sym6=0.2, lobes=3)
        result = classify(feats, quality)
        self.assertEqual(result["status"], "unknown")

    def test_impossible_lobe_count_caps_at_ambiguous(self):
        feats, quality = features(sym4=0.9, lobes=5, ticks=0.05, eccentricity=0.2)
        result = classify(feats, quality)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("lobe", result["reason"])

    def test_no_head_unknown(self):
        self.assertEqual(classify(None, {})["status"], "unknown")

    def test_low_contrast_unknown(self):
        feats, _ = features(sym4=0.9, lobes=4, ticks=0.05)
        self.assertEqual(classify(feats, {"contrast": 0.05})["status"], "unknown")

    def test_no_popularity_default(self):
        feats, quality = features(sym2=0.5, sym4=0.5, sym6=0.5, eccentricity=0.5,
                                  lobes=0, ticks=0.5, solidity=0.9, vertices=8)
        result = classify(feats, quality)
        self.assertNotEqual(result["status"], "ok")

    def test_ranked_candidates_carry_evidence(self):
        feats, quality = features(sym4=0.9, lobes=4, ticks=0.05, eccentricity=0.2)
        (top, *_) = classify(feats, quality)["candidates"]
        self.assertIn("fourfold", top["evidence"])
        self.assertGreaterEqual(top["score"], 0.55)


if __name__ == "__main__":
    unittest.main()
