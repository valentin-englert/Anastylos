"""Evidence schema round-trip + tamper detection (no network)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anastylos_sam import EVIDENCE_SCHEMA  # noqa: E402
from anastylos_sam import evidence as EV  # noqa: E402
from anastylos_sam import png as PNG  # noqa: E402


def _record() -> dict:
    return EV.build_record(
        request_id="samq-test01", created_utc="2026-10-28T00:00:00+00:00",
        prompt={"kind": "concept", "concept": "screw"},
        media={"kind": "image", "path": "x.jpg", "sha256": "ab" * 32,
               "bytes": 10, "width": 3, "height": 2},
        params={"stream": True}, response={"outcome": {"status": "completed"}},
        usage=EV.estimate_cost(media_kind="image", frames=1),
        records=[{"frame": None, "object_id": "7", "box": {}}],
        diagnostics=[], tool_versions={"python": "3.12"})


class EvidenceRoundTrip(unittest.TestCase):
    def test_write_verify_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            png = PNG.encode_gray8(3, 2, bytes((0, 0, 1, 1, 0, 1)))
            request_dir = EV.write_request(
                out, _record(), transcript_events=[{"type": "response.completed"}],
                mask_pngs={"mask_fNone_o7.png": png})
            self.assertTrue((request_dir / "evidence.json").is_file())
            check = EV.verify_request(request_dir)
            self.assertEqual(check, {"ok": True, "problems": []})

    def test_tampered_mask_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            png = PNG.encode_gray8(2, 2, bytes((0, 1, 1, 0)))
            request_dir = EV.write_request(
                out, _record(), transcript_events=[], mask_pngs={"m.png": png})
            (request_dir / "masks" / "m.png").write_bytes(b"forged")
            check = EV.verify_request(request_dir)
            self.assertFalse(check["ok"])
            self.assertTrue(any("m.png" in problem for problem in check["problems"]))

    def test_tampered_transcript_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            request_dir = EV.write_request(
                out, _record(), transcript_events=[{"type": "a"}],
                mask_pngs={})
            with open(request_dir / "transcript.jsonl", "a",
                       encoding="utf-8") as handle:
                handle.write("{}\n")
            check = EV.verify_request(request_dir)
            self.assertFalse(check["ok"])

    def test_unknown_schema_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = _record()
            record["schema"] = "something-else@9"
            with self.assertRaises(ValueError):
                EV.write_request(Path(tmp), record, transcript_events=[],
                                 mask_pngs={})


class CostModel(unittest.TestCase):
    def test_image_cost(self):
        cost = EV.estimate_cost(media_kind="image", frames=1)
        self.assertEqual(cost["usd"], 0.0025)
        self.assertEqual(cost["units_billed"], 1)

    def test_video_minimum_50_frames(self):
        cost = EV.estimate_cost(media_kind="video", frames=12)
        self.assertEqual(cost["units_billed"], 50)
        self.assertAlmostEqual(cost["usd"], 0.01)

    def test_video_scales(self):
        cost = EV.estimate_cost(media_kind="video", frames=1000)
        self.assertAlmostEqual(cost["usd"], 0.20)

    def test_schema_constant(self):
        self.assertEqual(EVIDENCE_SCHEMA, "sam-evidence-v1/request@1")


if __name__ == "__main__":
    unittest.main()
