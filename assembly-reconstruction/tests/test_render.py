"""SVG renderer tests: separation, honesty, determinism.

Run from the assembly-reconstruction directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_reconstruction.projector import build_state  # noqa: E402
from anastylos_reconstruction.render import render_svg  # noqa: E402

FIX = ROOT / "fixtures" / "synthetic"
NS = {"svg": "http://www.w3.org/2000/svg"}


def _fixture_state():
    return build_state(str(FIX / "memory.jsonl"),
                       results_dir=str(FIX / "results"),
                       graph_path=str(FIX / "assembly_graph.json"))


def _all_hrefs(text):
    root = ET.fromstring(text)
    hrefs = []
    for elem in root.iter():
        for key in ("href", "{http://www.w3.org/1999/xlink}href"):
            if elem.get(key):
                hrefs.append(elem.get(key))
    return hrefs


class RenderStructure(unittest.TestCase):
    def test_svg_parses_as_xml(self):
        ET.fromstring(render_svg(_fixture_state()))

    def test_deterministic_across_runs(self):
        self.assertEqual(render_svg(_fixture_state()),
                         render_svg(_fixture_state()))

    def test_physical_and_observation_sections_present(self):
        text = render_svg(_fixture_state())
        self.assertIn("Physical map", text)
        self.assertIn("Component stack", text)
        self.assertIn("Technical explosion sketch", text)
        self.assertNotIn("Exploded stack", text)
        self.assertIn("layout-only", text)
        self.assertIn("verbatim", text)

    def test_no_separate_evidence_strip(self):
        self.assertNotIn("Evidence crops", render_svg(_fixture_state()))

    def test_header_counts_observations_not_parts(self):
        text = render_svg(_fixture_state())
        self.assertIn("components: 5 (5 observations, 0 declared)", text)
        self.assertIn("5 components (5 observations, 0 declared) — not 5 "
                      "verified parts", text)
        self.assertIn("Identity stays unknown", text)

    def test_every_card_labeled_identity_unknown_with_badge(self):
        text = render_svg(_fixture_state())
        for index, component in enumerate(_fixture_state()["components"]):
            badge = f"[{index + 1}]"
            self.assertIn(
                f"{badge} {component['id']}  kind: observation  identity: unknown",
                text)

    def test_technical_sketch_has_stable_part_ids_and_balloons(self):
        text = render_svg(_fixture_state())
        root = ET.fromstring(text)
        for component in _fixture_state()["components"]:
            part = root.find(f".//*[@id='part-{component['id']}']")
            self.assertIsNotNone(part)
            self.assertEqual(part.get("data-event-id"), component["id"])
            self.assertIsNotNone(root.find(f".//*[@id='balloon-{component['id']}']"))
            self.assertIn(f'href="#part-{component["id"]}"', text)

    def test_badges_link_map_boxes_to_cards(self):
        text = render_svg(_fixture_state())
        # Each badge appears once beside its map box and once on its card.
        for index in range(5):
            self.assertGreaterEqual(text.count(f"[{index + 1}]"), 2)

    def test_evidence_pair_region_and_views_shown(self):
        text = render_svg(_fixture_state())
        self.assertIn("p_back/R1", text)
        self.assertIn("p_tray/R1", text)
        self.assertIn("fixture_back", text)
        self.assertIn("surface_a3", text)

    def test_tray_badges_distinguish_human_from_evidence(self):
        text = render_svg(_fixture_state())
        self.assertIn("operator-confirmed by fixture-operator", text)
        self.assertIn("evidence basis", text)
        self.assertIn("tray: unassigned", text)

    def test_confirmed_and_candidate_edges_drawn_differently(self):
        text = render_svg(_fixture_state())
        self.assertIn("confirmed", text)
        self.assertIn("candidate", text)
        self.assertIn("stroke-dasharray", text)

    def test_missing_evidence_has_explicit_placeholder(self):
        text = render_svg(_fixture_state())
        self.assertIn("obs-0004", text)
        self.assertIn("crop unavailable", text)
        self.assertIn("missing evidence", text)

    def test_crop_clips_use_verbatim_rectified_px(self):
        self.assertIn("256 216 32 32", render_svg(_fixture_state()))

    def test_provenance_footer_cites_hashes(self):
        state = _fixture_state()
        text = render_svg(state)
        self.assertIn(state["provenance"]["memory_sha256"], text)
        self.assertIn("assembly-state-v1", text)
        self.assertIn("identical inputs, identical bytes", text)

    def test_reassembly_carries_not_verified_warning(self):
        self.assertIn("NOT a verified procedure", render_svg(_fixture_state()))

    def test_unavailable_state_renders_reason_without_crash(self):
        state = build_state(str(FIX / "no-such-log.jsonl"))
        text = render_svg(state)
        ET.fromstring(text)
        self.assertIn("unavailable", text)


class LinkedCrops(unittest.TestCase):
    def test_hrefs_are_browser_safe_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            svg_path = str(Path(tmp) / "out" / "exploded.svg")
            text = render_svg(_fixture_state(), svg_path=svg_path)
            hrefs = [h for h in _all_hrefs(text) if not h.startswith("#")]
            self.assertTrue(hrefs)
            for href in hrefs:
                self.assertNotIn("\\", href, href)
                self.assertFalse(Path(href).is_absolute(), href)
                target = (Path(svg_path).parent / href).resolve()
                self.assertTrue(target.is_file(), href)

    def test_underlay_present_where_rectified_exists(self):
        text = render_svg(_fixture_state())
        self.assertIn("background: before_rectified (p_back, via obs-0001)", text)
        self.assertIn("no rectified underlay available for this view", text)


class EmbeddedCrops(unittest.TestCase):
    def test_embedded_svg_has_no_external_file_refs(self):
        text = render_svg(_fixture_state(), embed_crops=True)
        ET.fromstring(text)
        hrefs = _all_hrefs(text)
        self.assertTrue(hrefs)
        for href in hrefs:
            self.assertTrue(href.startswith(("data:image/", "#")), href)

    def test_each_unique_image_embedded_once(self):
        text = render_svg(_fixture_state(), embed_crops=True)
        # Two fixture PNGs shared by all card clips and the map underlay.
        self.assertEqual(text.count('<image id="cropimg-'), 2)
        self.assertIn("embedded images: 2", text)
        self.assertIn("all crops self-contained", text)

    def test_embedded_bytes_match_source_files(self):
        text = render_svg(_fixture_state(), embed_crops=True)
        root = ET.fromstring(text)
        blobs = [img.get("href") for img in root.findall(".//svg:image", NS)
                 if (img.get("href") or "").startswith("data:")]
        sources = {(FIX / "results" / name).read_bytes()
                   for name in ("back_before.png", "back_after.png")}
        decoded = {base64.b64decode(blob.split(",", 1)[1]) for blob in blobs}
        self.assertEqual(decoded, sources)

    def test_embedded_deterministic_across_runs(self):
        first = render_svg(_fixture_state(), embed_crops=True)
        second = render_svg(_fixture_state(), embed_crops=True)
        self.assertEqual(first, second)


class SameLocation(unittest.TestCase):
    def test_matching_coordinates_cross_referenced_without_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memory.jsonl"
            events = [
                {"type": "observation", "id": f"obs-000{i}", "session": "s",
                 "source_view": "v", "bbox_mm": [10, 10, 4, 4],
                 "centroid_mm": [12, 12],
                 "evidence": {"result_json": "x.json", "pair_id": "p",
                              "region": "R1", "result_sha256": None}}
                for i in (1, 2)
            ]
            log.write_text("\n".join(json.dumps(e) for e in events) + "\n",
                           encoding="utf-8")
            text = render_svg(build_state(str(log)))
            self.assertIn("same observed coordinates as obs-0002 "
                          "(location match only, not identity)", text)
            self.assertIn("same observed coordinates as obs-0001 "
                          "(location match only, not identity)", text)


if __name__ == "__main__":
    unittest.main()
