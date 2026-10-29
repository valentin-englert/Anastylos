"""Observer v1.4: colour and structure cues in the paper model (white and yellow parts on white paper).

Run from the custody-ledger directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from anastylos_custody.observer import ObserverParams, ZoneObserver  # noqa: E402
from anastylos_custody.signals import FrameSignals  # noqa: E402
from test_observer import CELL, PARAMS, SIZE  # noqa: E402

CUES = ObserverParams(**{**PARAMS.__dict__, "cell_occupancy": "paper_model", "print_stroke_px": 3,
                         "min_part_area_px": 20, "min_saturation": 60.0, "min_edge_px": 60.0})


def cell_signals(t, *, part=None, hand=False):
    """part: None, 'white' (grey like paper, only edges), 'yellow' (low grey contrast, saturated)."""
    gray = np.full(SIZE, 128.0, np.float32)
    saturation = np.zeros(SIZE, np.float32)
    edges = np.zeros(SIZE, bool)
    edges[30, 22:38] = True                      # printed digit stroke: 16 px of edges
    hand_mask = np.zeros(SIZE, bool)
    motion = np.zeros(SIZE, bool)
    if part == "white":
        edges[24:36, 24] = edges[24:36, 35] = True    # outline and internal ridges of a white rubber part
        edges[24, 24:36] = edges[35, 24:36] = True
        edges[28, 24:36] = edges[32, 24:36] = True
    if part == "yellow":
        gray[24:36, 24:36] = 150.0               # below the grey threshold (|150-128| < 30)
        saturation[24:36, 24:36] = 180.0
    if hand:
        gray[22:30, 0:35] = 200.0
        hand_mask[22:30, 0:35] = True
        motion[10:50, 10:50] = True
    return FrameSignals(t, gray, motion, hand_mask, saturation, edges)


def run(observer, spec):
    t, index = 0.0, 0
    for seconds, kwargs in spec:
        for _ in range(int(round(seconds * 5))):
            observer.observe(cell_signals(round(t, 3), **kwargs), index)
            t, index = t + 0.2, index + 1
    return [e["kind"] for e in observer.events if e["kind"] != "reference_set"]


class PaperCueTests(unittest.TestCase):
    def test_white_part_is_found_by_edges(self):
        kinds = run(ZoneObserver("cell-2", "tray_cell", CELL, CUES), [(2, {}), (1, {"hand": True}), (3, {"part": "white"})])
        self.assertEqual(kinds, ["appeared"])

    def test_yellow_part_is_found_by_saturation(self):
        kinds = run(ZoneObserver("cell-6", "tray_cell", CELL, CUES), [(2, {}), (1, {"hand": True}), (3, {"part": "yellow"})])
        self.assertEqual(kinds, ["appeared"])

    def test_printed_digit_edges_alone_stay_empty(self):
        observer = ZoneObserver("cell-12", "tray_cell", CELL, CUES)
        run(observer, [(3, {})])
        self.assertEqual(observer.summary()["initially_measured"], "empty")

    def test_white_part_removed_is_vacated(self):
        kinds = run(ZoneObserver("cell-10", "tray_cell", CELL, CUES), [(2, {"part": "white"}), (1, {"hand": True}), (3, {})])
        self.assertEqual(kinds, ["vacated"])

    def test_without_cues_v1_3_behaviour_is_kept(self):
        grey_only = ObserverParams(**{**CUES.__dict__, "min_saturation": 0.0, "min_edge_px": 0.0})
        kinds = run(ZoneObserver("cell-2", "tray_cell", CELL, grey_only), [(2, {}), (1, {"hand": True}), (3, {"part": "white"})])
        self.assertNotIn("appeared", kinds)


if __name__ == "__main__":
    unittest.main()
