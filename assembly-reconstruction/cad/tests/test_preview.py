"""Preview tests: projection honesty, determinism, hash citation.

Run from the assembly-reconstruction/cad directory:

    .venv/Scripts/python -m unittest discover -s tests -v

This file is standard library only.
"""

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anastylos_cad.preview import (  # noqa: E402
    project_xy, render_wireframe_svg,
)

SQUARE_VERTS = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                (10.0, 5.0, 0.0), (0.0, 5.0, 0.0)]
SQUARE_TRIS = [(0, 1, 2), (0, 2, 3)]
SHA = "ab" * 32


class Wireframe(unittest.TestCase):
    def test_valid_xml_citing_model_hash(self):
        svg = render_wireframe_svg(project_xy(SQUARE_VERTS), SQUARE_TRIS, SHA)
        ET.fromstring(svg)  # raises if malformed
        self.assertIn(SHA, svg)
        self.assertIn("projection", svg)

    def test_deterministic(self):
        first = render_wireframe_svg(project_xy(SQUARE_VERTS), SQUARE_TRIS, SHA)
        second = render_wireframe_svg(project_xy(SQUARE_VERTS), SQUARE_TRIS, SHA)
        self.assertEqual(first, second)

    def test_edge_count_is_unique_triangle_edges(self):
        svg = render_wireframe_svg(project_xy(SQUARE_VERTS), SQUARE_TRIS, SHA)
        # Two triangles sharing one diagonal: 5 unique edges.
        self.assertEqual(svg.count("<line"), 5)

    def test_empty_vertices_rejected(self):
        with self.assertRaises(ValueError):
            render_wireframe_svg([], [], SHA)


if __name__ == "__main__":
    unittest.main()
