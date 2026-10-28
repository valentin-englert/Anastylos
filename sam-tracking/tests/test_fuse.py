"""Fusion phase-machine tests over synthetic tracks (no network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anastylos_sam import fuse as FU  # noqa: E402


def _box(cx: float, cy: float, half: float = 10.0) -> dict:
    return {"left": cx - half, "top": cy - half,
            "right": cx + half, "bottom": cy + half}


def _evidence(request_id: str, frames_boxes: list[tuple[int, dict]],
              object_id: str = "0") -> dict:
    return {"request_id": request_id,
            "records": [{"frame": frame, "object_id": object_id, "box": box}
                        for frame, box in frames_boxes]}


SRC = [100.0, 100.0, 200.0, 200.0]
DST = [500.0, 500.0, 600.0, 600.0]


def _src_track() -> list[tuple[int, dict]]:
    # Frame-to-frame steps stay under the 150 px jump gate.
    return ([(frame, _box(150, 150)) for frame in range(5)] +
            [(5, _box(210, 210)), (6, _box(290, 290)), (7, _box(380, 380)),
             (8, _box(480, 480)), (9, _box(555, 555))])


class DescribeTrack(unittest.TestCase):
    def test_gaps_and_jumps(self):
        items = [{"frame": frame, "box": _box(150 + 200 * (frame > 2), 150)}
                 for frame in (0, 1, 2, 4, 5)]
        desc = FU.describe_track(items)
        self.assertEqual(desc["gaps"], [[3, 3]])
        self.assertEqual(desc["segments"], 2)
        self.assertEqual(len(desc["jumps"]), 0)  # gap frames are not jumps

    def test_jump_flagged(self):
        items = [{"frame": 0, "box": _box(150, 150)},
                 {"frame": 1, "box": _box(500, 500)}]
        desc = FU.describe_track(items)
        self.assertEqual(len(desc["jumps"]), 1)


class PhaseMachine(unittest.TestCase):
    def test_supported_hypothesis(self):
        src = _evidence("src-1", _src_track())
        hands = [_evidence("hand-1", [(frame, _box(160, 160, 30))
                                      for frame in range(4, 9)], "h0")]
        dst = _evidence("dst-1", [(frame, _box(555, 555))
                                  for frame in range(8, 12)], "d0")
        result = FU.run_hypothesis(src_evidence=src, src_id="0",
                                   src_region=SRC, dst_region=DST,
                                   hands=hands, dst_evidence=dst, dst_id="d0")
        self.assertEqual(result["verdict"], "HYPOTHESIS_SUPPORTED")
        self.assertEqual(result["reasons"], [])

    def test_no_hand_contact(self):
        src = _evidence("src-1", _src_track())
        result = FU.run_hypothesis(src_evidence=src, src_id="0",
                                   src_region=SRC, dst_region=DST, hands=[])
        self.assertEqual(result["verdict"], "UNKNOWN")
        self.assertIn("NO_HAND_CONTACT", result["reasons"])

    def test_hand_ambiguity(self):
        src = _evidence("src-1", _src_track())
        hands = [_evidence("hand-1", [(4, _box(160, 160, 30))], "h0"),
                 _evidence("hand-2", [(4, _box(165, 165, 30))], "h1")]
        result = FU.run_hypothesis(src_evidence=src, src_id="0",
                                   src_region=SRC, dst_region=DST, hands=hands)
        self.assertEqual(result["verdict"], "UNKNOWN")
        self.assertIn("HAND_AMBIGUITY", result["reasons"])

    def test_jump_blocks_support(self):
        track = [(frame, _box(150, 150)) for frame in range(5)]
        track.append((5, _box(550, 550)))
        track += [(frame, _box(555, 555)) for frame in range(6, 10)]
        src = _evidence("src-1", track)
        hands = [_evidence("hand-1", [(frame, _box(160, 160, 30))
                                      for frame in range(4, 7)], "h0")]
        dst = _evidence("dst-1", [(frame, _box(555, 555))
                                  for frame in range(6, 10)], "d0")
        result = FU.run_hypothesis(src_evidence=src, src_id="0",
                                   src_region=SRC, dst_region=DST,
                                   hands=hands, dst_evidence=dst, dst_id="d0")
        self.assertIn("ID_JUMP_SUSPECT", result["reasons"])
        self.assertEqual(result["verdict"], "UNKNOWN")

    def test_missing_destination_track(self):
        src = _evidence("src-1", _src_track())
        hands = [_evidence("hand-1", [(4, _box(160, 160, 30))], "h0")]
        result = FU.run_hypothesis(src_evidence=src, src_id="0",
                                   src_region=SRC, dst_region=DST, hands=hands)
        self.assertIn("NO_DESTINATION_TRACK", result["reasons"])
        self.assertEqual(result["verdict"], "UNKNOWN")

    def test_empty_track(self):
        result = FU.run_hypothesis(src_evidence=_evidence("e", []),
                                   src_id="0", src_region=SRC,
                                   dst_region=DST, hands=[])
        self.assertEqual(result["verdict"], "UNKNOWN")
        self.assertIn("INSUFFICIENT_EVIDENCE", result["reasons"])


if __name__ == "__main__":
    unittest.main()
