"""Export: PLY+STL reopen in a fresh subprocess with finite verts, faces>0."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anastylos_mesh.carve import VoxelGrid, carve
from anastylos_mesh.export import save_mesh
from anastylos_mesh.surface import extract_surface
from anastylos_mesh.synthetic import make_scene, render_fixtures

_PROBE = (
    "import json,sys;import trimesh;import numpy as _np;"
    "m=trimesh.load(sys.argv[1],force='mesh');"
    "v=_np.asarray(m.vertices);"
    "print(json.dumps({'vertices':int(v.shape[0]),"
    "'faces':int(_np.asarray(m.faces).shape[0]),"
    "'finite':bool(_np.isfinite(v).all())}))"
)


def _reopen_counts(path):
    proc = subprocess.run([sys.executable, "-c", _PROBE, str(path)],
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"REOPEN_FAILED:{path}:{proc.stderr.strip()[-500:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestExport(unittest.TestCase):
    def test_ply_stl_reopen_finite_and_nonempty(self):
        scene = make_scene("box")
        fixtures = render_fixtures(scene, n_views=8, width=96, height=72)
        extents = [b[1] - b[0] for b in scene.bounds]
        pad = max(extents) * 0.05
        bounds = tuple((b[0] - pad, b[1] + pad) for b in scene.bounds)
        voxel = max(b[1] - b[0] for b in bounds) / 32
        grid = VoxelGrid(bounds_mm=bounds, voxel_size=voxel)
        result = carve(fixtures.cameras, fixtures.masks, grid, min_observed=2)
        surface = extract_surface(result)
        with tempfile.TemporaryDirectory() as tmp:
            for suffix in (".ply", ".stl"):
                path = save_mesh(surface.mesh, Path(tmp) / f"mesh{suffix}")
                counts = _reopen_counts(path)
                self.assertEqual(counts["vertices"], surface.vertex_count)
                self.assertEqual(counts["faces"], surface.face_count)
                self.assertTrue(counts["finite"])
                self.assertGreater(counts["vertices"], 0)
                self.assertGreater(counts["faces"], 0)


if __name__ == "__main__":
    unittest.main()
