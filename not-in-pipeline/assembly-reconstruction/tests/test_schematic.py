"""Controller schematic sheet tests."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_reconstruction.cli import main  # noqa: E402
from anastylos_reconstruction.schematic import (  # noqa: E402
    load_package,
    render_schematic_svg,
    verify_evidence,
    write_schematic,
)

FIX = ROOT / "fixtures" / "schematic" / "controller_schematic_package.json"


class SchematicSheet(unittest.TestCase):
    def test_package_loads(self):
        package = load_package(FIX)
        self.assertEqual(len(package["parts"]), 3)

    def test_same_part_ids_in_assembled_and_exploded(self):
        package = load_package(FIX)
        zero = render_schematic_svg(package, explode_factor=0.0)
        exploded = render_schematic_svg(package, explode_factor=1.0)
        for part in package["parts"]:
            self.assertIn(f'id="{part["id"]}"', zero)
            self.assertIn(f'id="{part["id"]}"', exploded)
            self.assertIn(f'data-part-id="{part["id"]}"', zero)
            self.assertIn(f'data-part-id="{part["id"]}"', exploded)
        self.assertIn('data-view="assembled"', zero)
        self.assertIn('data-view="exploded"', exploded)
        self.assertIn('data-explode-factor="0"', zero)

    def test_zero_explosion_keeps_offsets_inactive(self):
        package = load_package(FIX)
        # With factor 0, exploded offsets must not move anchors relative to
        # assembled placement of the same render path inside view-exploded
        # when factor is 0 on a dedicated assembled file.
        zero = render_schematic_svg(package, explode_factor=0.0)
        root = ET.fromstring(zero)
        exploded_view = None
        for elem in root.iter():
            if elem.get("id") == "view-exploded":
                exploded_view = elem
                break
        self.assertIsNotNone(exploded_view)
        self.assertEqual(exploded_view.get("data-explode-factor"), "0.000")

    def test_partial_geometry_marked(self):
        text = render_schematic_svg(load_package(FIX), explode_factor=1.0)
        self.assertIn("schematic_from_partial_observed", text)
        self.assertIn("#8a5a00", text)
        self.assertIn("draft_unconfirmed", text)

    def test_deterministic_and_evidence_untouched(self):
        package = load_package(FIX)
        first = render_schematic_svg(package, explode_factor=1.0)
        second = render_schematic_svg(package, explode_factor=1.0)
        self.assertEqual(first, second)
        before = hashlib.sha256(FIX.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            write_schematic(str(FIX), tmp, explode_factor=1.0)
        self.assertEqual(hashlib.sha256(FIX.read_bytes()).hexdigest(), before)

    def test_cli_schematic(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["schematic", str(FIX), "--out", tmp])
            self.assertEqual(code, 0)
            svg = Path(tmp) / "controller_schematic.svg"
            assembled = Path(tmp) / "controller_schematic_assembled.svg"
            prov = Path(tmp) / "controller_schematic_provenance.json"
            self.assertTrue(svg.is_file())
            self.assertTrue(assembled.is_file())
            self.assertTrue(prov.is_file())
            ET.parse(str(svg))
            data = json.loads(prov.read_text(encoding="utf-8"))
            self.assertEqual(len(data["parts"]), 3)

    def test_verify_evidence_missing_is_reported(self):
        package = load_package(FIX)
        package = dict(package)
        package["evidence"] = {
            "front_shell_button_group": {
                "extract": str(Path("no-such-file.jpg")),
                "extract_sha256": "0" * 64,
            }
        }
        reports = verify_evidence(package)
        self.assertEqual(reports[0]["status"], "unreadable")


if __name__ == "__main__":
    unittest.main()
