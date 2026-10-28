"""Parse bridge tests over official conformance vectors (no network)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anastylos_sam import parse as PA  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LANE = {"item_id": "test-0", "output_index": 0, "content_index": 0}


def lane_events(chunks: list[str]) -> list[dict]:
    full = "".join(chunks)
    events = [{**LANE, "type": "response.output_text.delta", "delta": chunk}
              for chunk in chunks]
    events.append({**LANE, "type": "response.output_text.done", "text": full})
    events.append({"type": "response.completed",
                   "response": {"id": "resp-test", "status": "completed"}})
    return events


def load_case(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as handle:
        return json.load(handle)


class ParseImageVector(unittest.TestCase):
    def test_conformance_image_lossless(self):
        case = load_case("compact-api-image-lossless.json")
        parsed = PA.parse_events(lane_events(case["chunks"]), "image")
        self.assertEqual(parsed["outcome"]["status"], "completed")
        self.assertEqual(parsed["diagnostics"], [])
        self.assertEqual(len(parsed["records"]), 1)
        item = parsed["records"][0]
        self.assertIsNone(item["frame"])
        self.assertEqual(item["object_id"], "7")
        self.assertEqual(item["box"], {"left": 0, "top": 0, "right": 3,
                                       "bottom": 2})
        self.assertEqual(item["mask"]["encoding"], "lossless")
        self.assertEqual(item["mask"]["raster"], bytes((0, 0, 1, 1, 0, 1)))
        self.assertEqual(item["mask"]["rle"]["size"], [2, 3])
        self.assertEqual(item["mask"]["rle"]["counts"], "1121")


class ParseVideoVector(unittest.TestCase):
    def test_conformance_video_sparse_ids(self):
        case = load_case("synthetic-video-basic.json")
        parsed = PA.parse_events(lane_events(case["chunks"]), "video")
        self.assertEqual(parsed["outcome"]["status"], "completed")
        frames = sorted({item["frame"] for item in parsed["records"]})
        self.assertEqual(frames, [7, 9])
        ids_f9 = sorted(item["object_id"] for item in parsed["records"]
                        if item["frame"] == 9)
        self.assertEqual(ids_f9, ["0", "2"])  # sparse ids preserved
        for item in parsed["records"]:
            self.assertEqual(item["mask"]["encoding"], "one_bit")
            self.assertEqual(len(item["mask"]["raster"]),
                             item["mask"]["height"] * item["mask"]["width"])


class ParseEdgeCases(unittest.TestCase):
    def test_empty_lane_is_valid_zero_match(self):
        parsed = PA.parse_events(lane_events([]), "image")
        self.assertEqual(parsed["outcome"]["status"], "completed")
        self.assertEqual(parsed["records"], [])

    def test_truncated_stream_is_incomplete_eof(self):
        events = [{**LANE, "type": "response.output_text.delta",
                   "delta": "<0f>"}]
        parsed = PA.parse_events(events, "image")
        self.assertEqual(parsed["outcome"]["status"], "incomplete")
        self.assertEqual(parsed["outcome"]["reason"], "eof")

    def test_bare_frame_header_is_malformed(self):
        parsed = PA.parse_events(lane_events(["<0f>\n"]), "image")
        self.assertEqual(parsed["records"], [])
        codes = [item["code"] for item in parsed["diagnostics"]]
        self.assertIn("malformed_record", codes)

    def test_failed_response_raises(self):
        events = lane_events([])[:-1] + [
            {"type": "response.failed",
             "response": {"status": "failed",
                          "error": {"message": "boom", "code": "x"}}}]
        with self.assertRaises(PA.ParseFailed):
            PA.parse_events(events, "image")


class NonStreamingSynthesis(unittest.TestCase):
    def test_completed_json_feeds_same_parser(self):
        case = load_case("compact-api-image-lossless.json")
        full = "".join(case["chunks"])
        data = {"id": "resp-1", "status": "completed",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": full}]}]}
        events = PA.events_from_completed(data)
        parsed = PA.parse_events(events, "image")
        self.assertEqual(len(parsed["records"]), 1)
        self.assertEqual(parsed["records"][0]["object_id"], "7")

    def test_failed_completed_json_raises(self):
        with self.assertRaises(PA.ParseFailed):
            PA.events_from_completed({"status": "failed", "error": {}})


if __name__ == "__main__":
    unittest.main()
