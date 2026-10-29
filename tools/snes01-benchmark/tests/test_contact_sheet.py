"""Tests for make_contact_sheet.py (graph building only, no ffmpeg run)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_contact_sheet


def tile(frame, crop, region):
    return {"frame": frame, "crop": crop, "region": region}


class LoadTilesCase(unittest.TestCase):
    def test_loads_before_after_in_order(self):
        doc = {
            "events": [
                {
                    "id": "E1",
                    "contact_tile": {
                        "before": tile("frame_0001", [0, 0, 100, 100], "tray"),
                        "after": tile("frame_0002", [0, 0, 100, 100], "tray"),
                    },
                }
            ]
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(doc, tmp)
            path = tmp.name
        self.addCleanup(os.remove, path)
        tiles = make_contact_sheet.load_tiles(path)
        self.assertEqual(
            [(t["event"], t["side"], t["frame"]) for t in tiles],
            [("E1", "BEFORE", "frame_0001"), ("E1", "AFTER", "frame_0002")],
        )

    def test_missing_tile_key_raises(self):
        doc = {
            "events": [
                {
                    "id": "E1",
                    "contact_tile": {
                        "before": {"frame": "frame_0001"},
                        "after": tile("frame_0002", [0, 0, 100, 100], "tray"),
                    },
                }
            ]
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(doc, tmp)
            path = tmp.name
        self.addCleanup(os.remove, path)
        with self.assertRaises(ValueError):
            make_contact_sheet.load_tiles(path)


class FilterGraphCase(unittest.TestCase):
    def test_graph_contains_expected_ops(self):
        tiles = [
            {
                "event": "E1",
                "side": "BEFORE",
                "frame": "frame_0001",
                "crop": [10, 20, 300, 400],
                "region": "tray",
            },
            {
                "event": "E1",
                "side": "AFTER",
                "frame": "frame_0002",
                "crop": [10, 20, 300, 400],
                "region": "tray",
            },
        ]
        graph = make_contact_sheet.build_filter(tiles, 480, 520, 2, "font.ttf")
        self.assertIn("crop=300:400:10:20", graph)
        self.assertIn("scale=480:-1", graph)
        self.assertIn("pad=480:520", graph)
        self.assertIn("E1 BEFORE FRAME_0001 TRAY", graph)
        self.assertIn("xstack=inputs=2:layout=0_0|480_0", graph)

    def test_escape_drawtext(self):
        self.assertEqual(
            make_contact_sheet.escape_drawtext("a:b%"),
            "a\\:b\\%",
        )


if __name__ == "__main__":
    unittest.main()
