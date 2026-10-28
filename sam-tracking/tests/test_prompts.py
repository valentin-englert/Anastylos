"""Prompt construction tests (no network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anastylos_sam import prompts as PR  # noqa: E402


class ConceptValidation(unittest.TestCase):
    def test_plain_noun_phrase_ok(self):
        self.assertEqual(PR.validate_concept("screw"), [])

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            PR.validate_concept("   ")

    def test_multi_concept_rejected(self):
        for text in ("screw, hand", "screw;hand", "screw+hand",
                     "screw and hand", "screw\nhand"):
            with self.assertRaises(ValueError, msg=text):
                PR.validate_concept(text)

    def test_command_and_spatial_warn(self):
        self.assertTrue(PR.validate_concept("segment the screw"))
        self.assertTrue(PR.validate_concept("screw on the left"))

    def test_normalize_collapses_whitespace(self):
        self.assertEqual(PR.normalize_concept("  circuit   board "), "circuit board")


class BoxPromptBuilder(unittest.TestCase):
    def test_exact_token_text(self):
        text, echo = PR.build_box_prompt(
            size=(640, 480),
            objects=[{"id": 1, "frame": 0, "box": [10, 20, 40, 60],
                      "points": [{"x": 25, "y": 30},
                                 {"x": 5, "y": 5, "label": "negative"}]}])
        self.assertEqual(
            text, "<0f>1<|box;x1=10;y1=20;x2=39;y2=59;w=640;h=480|>"
                  "<|point;x=25;y=30;w=640;h=480|>"
                  "-<|point;x=5;y=5;w=640;h=480|>")
        self.assertEqual(echo[0]["id"], "1")

    def test_box_outside_media_rejected(self):
        with self.assertRaises(ValueError):
            PR.build_box_prompt(size=(640, 480), objects=[
                {"id": 1, "frame": 0, "box": [600, 20, 700, 60]}])

    def test_empty_object_rejected(self):
        with self.assertRaises(ValueError):
            PR.build_box_prompt(size=(640, 480), objects=[
                {"id": 1, "frame": 0}])

    def test_video_prompt_frames_sorted(self):
        text, _ = PR.build_box_prompt(
            size=(100, 100),
            objects=[{"id": 2, "frame": 9, "points": [{"x": 1, "y": 1}]},
                     {"id": 1, "frame": 3, "points": [{"x": 2, "y": 2}]}])
        self.assertTrue(text.startswith("<3f>1"))
        self.assertIn("<9f>2", text)


if __name__ == "__main__":
    unittest.main()
