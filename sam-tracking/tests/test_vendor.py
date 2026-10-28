"""Vendored-parser provenance tests: constants match committed bytes."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_sam import PARSER_COMMIT, PARSER_VERSION  # noqa: E402


class VendorProvenance(unittest.TestCase):
    def test_version_matches_pyproject(self):
        text = (ROOT / "vendor" / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(PARSER_VERSION, match.group(1))  # type: ignore[union-attr]

    def test_commit_matches_vendor_record(self):
        text = (ROOT / "vendor" / "VENDOR.md").read_text(encoding="utf-8")
        self.assertIn(PARSER_COMMIT, text)

    def test_parser_imports_and_builds(self):
        sys.path.insert(0, str(ROOT / "vendor"))
        import meta_sam_parser as parser  # noqa: PLC0415
        self.assertIn("decode_mask_to_raster", parser.__all__)
        self.assertIn("build_object_prompt", parser.__all__)

    def test_compatibility_matrix_parses(self):
        with open(ROOT / "vendor" / "compatibility.json",
                   encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertIsInstance(data, dict)

    def test_license_shipped(self):
        text = (ROOT / "vendor" / "LICENSE.meta-sam").read_text(encoding="utf-8")
        self.assertIn("SAM License", text)


if __name__ == "__main__":
    unittest.main()
