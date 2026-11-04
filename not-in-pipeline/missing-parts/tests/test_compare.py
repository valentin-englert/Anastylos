"""Visual comparison checks on deterministic synthetic pairs.

Fixtures exercise the pipeline only; they prove nothing about
real-hardware accuracy.

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
import numpy as np

from anastylos_missing import synthetic as S
from anastylos_missing.compare import run_comparison
from anastylos_missing.context import load_context


def write_context(directory: Path, frames=(None, None), surface="light",
                  transform=None) -> dict:
    payload = {"original": {"role": "original_assembly", "frame": frames[0]},
               "current": {"role": "reassembly_state", "frame": frames[1]},
               "surface": surface}
    if transform is not None:
        payload["transform"] = transform
    path = directory / "ctx.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_context(path)


def write_pair(directory: Path, name: str, images) -> tuple[str, str]:
    original, current = images[0], images[1]
    opath, cpath = directory / f"{name}_o.png", directory / f"{name}_c.png"
    cv2.imwrite(str(opath), original)
    cv2.imwrite(str(cpath), current)
    return str(opath), str(cpath)


def classes(result):
    return {r["label"]: r["classification"] for r in result["regions"]}


class DirectPairTest(unittest.TestCase):
    def test_unchanged_is_ok_without_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "same", S.unchanged())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(result["regions"], [])
        self.assertFalse(result["requires_confirmation"])
        self.assertIn("successfully evaluated", result["verdict_meaning"])

    def test_removed_is_unconfirmed_absent_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "rm", S.removed())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(classes(result), {"R1": "candidate_absent_region"})
        region = result["regions"][0]
        self.assertEqual(region["strength"], "supported")
        self.assertTrue(region["requires_confirmation"])
        self.assertTrue(result["requires_confirmation"])
        self.assertIn("R1", result["confirmation_required_for"])
        self.assertNotIn("confirmed_missing", [r["classification"] for r in result["regions"]])

    def test_added_is_newly_occupied(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "add", S.added())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        self.assertEqual(classes(result), {"R1": "candidate_newly_occupied"})
        self.assertTrue(result["regions"][0]["requires_confirmation"])

    def test_shifted_part_stays_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "sh", S.shifted())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        kinds = set(classes(result).values())
        self.assertIn("shifted_present", kinds)
        self.assertNotIn("candidate_absent_region", kinds)
        self.assertNotIn("candidate_newly_occupied", kinds)

    def test_displaced_links_absence_and_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "dp", S.displaced())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        self.assertEqual(set(classes(result).values()), {"candidate_displaced"})
        self.assertEqual(len(result["displacement_links"]), 1)
        link = result["displacement_links"][0]
        self.assertTrue(link["requires_confirmation"])
        self.assertNotEqual(link["absence_region"], link["presence_region"])

    def test_repeated_parts_flag_one_without_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "rp", S.repeated())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        self.assertEqual(classes(result), {"R1": "candidate_absent_region"})
        region = result["regions"][0]
        self.assertIn("identity", " ".join(region["reasons"]).lower()
                      + region["evidence"].get("association_identity_note", "identity"))

    def test_preexisting_holes_claim_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "hh", S.preexisting_hole())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(result["regions"], [])
        self.assertGreaterEqual(result["holes"]["matched_unchanged"], 1)

    def test_revealed_mounting_hole_supports_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "rv", S.hole_revealed())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        region = result["regions"][0]
        self.assertEqual(region["classification"], "candidate_absent_region")
        self.assertTrue(region["evidence"].get("mounting_hole_visible_in_current"))
        self.assertTrue(region["requires_confirmation"])

    def test_lighting_change_claims_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "li", S.lighting())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        self.assertEqual(result["regions"], [])
        self.assertEqual(result["verdict"], "ok")

    def test_occlusion_withdraws_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "oc", S.occluded())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        kinds = set(classes(result).values())
        self.assertNotIn("candidate_absent_region", kinds)
        self.assertNotIn("candidate_newly_occupied", kinds)
        self.assertIn(result["verdict"], ("ambiguous", "unknown"))
        self.assertTrue(any("occlusion" in r for region in result["regions"]
                            for r in region["reasons"]))

    def test_contradictory_evidence_stays_unclaimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "cx", S.contradictory())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        region = result["regions"][0]
        self.assertEqual(region["classification"], "observed_difference")
        self.assertEqual(region["strength"], "contradictory")
        self.assertEqual(result["verdict"], "ambiguous")

    def test_result_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "js", S.removed())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        json.dumps(result)


class RegistrationTest(unittest.TestCase):
    def test_excessive_shift_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "sv", S.shifted_view())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir))
        self.assertEqual(result["verdict"], "unavailable")
        self.assertEqual(result["regions"], [])
        self.assertTrue(any("shift" in r for r in result["reasons"]))

    def test_verified_transform_accepts_cross_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            original, current, _, homography = S.translated_pair()
            opath, cpath = write_pair(tmpdir, "tf", (original, current))
            context = write_context(
                tmpdir, frames=("custom:cam_a", "custom:cam_b"),
                transform={"homography": homography, "from_frame": "custom:cam_b",
                           "to_frame": "custom:cam_a",
                           "provenance": "synthetic checkerboard",
                           "reprojection_rms_mm": 0.2})
            result = run_comparison(original=opath, current=cpath, context=context)
        self.assertEqual(result["registration"]["method"], "verified_transform")
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(result["regions"], [])

    def test_dimension_mismatch_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, _ = write_pair(tmpdir, "dm", S.unchanged())
            small = cv2.resize(cv2.imread(opath), (100, 100))
            cpath = tmpdir / "dm_small.png"
            cv2.imwrite(str(cpath), small)
            result = run_comparison(original=opath, current=str(cpath),
                                    context=write_context(tmpdir))
        self.assertEqual(result["verdict"], "unavailable")

    def test_uniform_images_are_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            flat = np.full((200, 200, 3), 200, np.uint8)
            opath, cpath = tmpdir / "f_o.png", tmpdir / "f_c.png"
            cv2.imwrite(str(opath), flat)
            cv2.imwrite(str(cpath), flat)
            result = run_comparison(original=str(opath), current=str(cpath),
                                    context=write_context(tmpdir))
        self.assertEqual(result["verdict"], "unknown")

    def test_missing_image_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_comparison(original=str(Path(tmp) / "gone.png"),
                               current=str(Path(tmp) / "gone2.png"),
                               context=write_context(Path(tmp)))

    def test_custom_aligner_is_used(self):
        seen = {}

        def aligner(original, current, max_shift):
            seen["called"] = True
            return {"ok": True, "reason": "", "dx": 0.0, "dy": 0.0, "cc": 1.0,
                    "warped": current, "valid": np.full(current.shape, 255, np.uint8)}

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "al", S.unchanged())
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir), aligner=aligner)
        self.assertTrue(seen.get("called"))
        self.assertEqual(result["verdict"], "ok")

    def test_pipeline_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "dt", S.removed())
            context = write_context(tmpdir)
            first = run_comparison(original=opath, current=cpath, context=context)
            second = run_comparison(original=opath, current=cpath, context=context)
        self.assertEqual(first, second)


class NeutralModeTest(unittest.TestCase):
    def test_neutral_reports_differences_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "nt", S.removed())
            result = run_comparison(original=opath, current=cpath, context=None)
        self.assertEqual(result["mode"], "neutral")
        self.assertEqual(set(classes(result).values()), {"observed_difference"})

    def test_neutral_unchanged_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "nu", S.unchanged())
            result = run_comparison(original=opath, current=cpath, context=None)
        self.assertEqual(result["mode"], "neutral")
        self.assertEqual(result["verdict"], "ok")


class DetectionInputTest(unittest.TestCase):
    def test_trusted_detection_yields_absent_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            info = S.detection_fixture(tmpdir / "pair_01")
            context = write_context(tmpdir, frames=("fixture_back", "fixture_back"))
            result = run_comparison(detection=info["result_json"], context=context)
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(classes(result), {"R1": "candidate_absent_region"})
        self.assertTrue(result["frame"]["mm_validated"])
        self.assertIsNotNone(result["detection"]["result_sha256"])

    def test_detection_without_images_uses_sign(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            info = S.detection_fixture(tmpdir / "pair_02", with_images=False)
            context = write_context(tmpdir, frames=("fixture_back", "fixture_back"))
            result = run_comparison(detection=info["result_json"], context=context)
        self.assertEqual(classes(result), {"R1": "candidate_absent_region"})
        self.assertTrue(any("rectified images unavailable" in i for i in result["issues"]))

    def test_untrusted_detection_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            info = S.detection_fixture(tmpdir / "pair_03", status="registration_invalid")
            context = write_context(tmpdir, frames=("fixture_back", "fixture_back"))
            result = run_comparison(detection=info["result_json"], context=context)
        self.assertEqual(result["verdict"], "unavailable")
        self.assertEqual(result["regions"], [])

    def test_frame_mismatch_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            info = S.detection_fixture(tmpdir / "pair_04", view="surface_a3")
            context = write_context(tmpdir, frames=("fixture_back", "fixture_back"))
            result = run_comparison(detection=info["result_json"], context=context)
        self.assertEqual(result["verdict"], "unavailable")
        self.assertTrue(any("differs from evidence view" in r for r in result["reasons"]))

    def test_detection_and_images_are_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            info = S.detection_fixture(tmpdir / "pair_05")
            opath, cpath = write_pair(tmpdir, "ex", S.unchanged())
            with self.assertRaises(ValueError):
                run_comparison(original=opath, current=cpath,
                               detection=info["result_json"],
                               context=write_context(tmpdir))


class AssociationTest(unittest.TestCase):
    def test_similar_candidates_listed_without_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            opath, cpath = write_pair(tmpdir, "as", S.repeated())
            associations = [{"region": "R1", "candidate_id": "screw-A",
                             "score": 0.91, "source": "fingerprints"},
                            {"region": "R1", "candidate_id": "screw-B",
                             "score": 0.90, "source": "fingerprints"}]
            result = run_comparison(original=opath, current=cpath,
                                    context=write_context(tmpdir),
                                    associations=associations)
        region = result["regions"][0]
        self.assertEqual(len(region["associations"]), 2)
        self.assertTrue(all(a["requires_confirmation"] for a in region["associations"]))
        self.assertTrue(any("no identity asserted" in r for r in region["reasons"]))


if __name__ == "__main__":
    unittest.main()
