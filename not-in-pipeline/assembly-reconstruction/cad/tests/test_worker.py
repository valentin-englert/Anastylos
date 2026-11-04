"""Worker tests: kernel build, STEP round-trip, regen, CLI guards.

Run from the assembly-reconstruction/cad directory under the worker venv:

    .venv/Scripts/python -m unittest discover -s tests -v

These tests require the gated CadQuery runtime; they fail closed with
ModuleNotFoundError outside the worker venv (no skips).
"""

import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anastylos_cad.evidence import (  # noqa: E402
    BUNDLE_FILES, missing_feature_envelope,
)

FIX = ROOT / "fixtures" / "synthetic"
CADWORK = str(ROOT / "cadwork.py")

PLATE_VOLUME = 80.0 * 50.0 * 6.0 - math.pi * 3.0**2 * 6.0


def run_cli(*args):
    return subprocess.run([sys.executable, CADWORK] + list(args),
                          capture_output=True, text=True, timeout=180)


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FullRevision(unittest.TestCase):
    def test_run_plate_produces_honest_bundle(self):
        tmp = Path(tempfile.mkdtemp(prefix="cadrev"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "rev1"
        proc = run_cli("run", "--spec", str(FIX / "plate_hole.json"),
                       "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"], payload)
        for name in list(BUNDLE_FILES) + ["manifest.json"]:
            self.assertTrue((out / name).is_file(), name)
        validation = load_json(out / "validation.json")
        compare = validation["roundtrip_compare"]
        self.assertTrue(compare["native_valid"])
        self.assertTrue(compare["reimport_valid"])
        self.assertTrue(compare["dims_within_0v01mm"])
        self.assertTrue(compare["volume_rel_err_le_1e-5"])
        native = validation["native"]
        self.assertAlmostEqual(native["volume"], PLATE_VOLUME, places=6)
        self.assertEqual(native["bbox_size"], [80.0, 50.0, 6.0])
        # Manifest hashes verify against the bundle bytes.
        manifest = load_json(out / "manifest.json")
        for entry in manifest["artifacts"]:
            path = out / entry["path"]
            self.assertEqual(sha256_file(path), entry["sha256"], entry)
            self.assertEqual(path.stat().st_size, entry["bytes"], entry)

    def test_regen_plus_1mm_moves_only_x(self):
        tmp = Path(tempfile.mkdtemp(prefix="cadregen"))
        self.addCleanup(shutil.rmtree, tmp, True)
        spec = load_json(FIX / "plate_hole.json")
        spec["features"][0]["parameters"]["size_mm"][0] += 1.0
        grown = tmp / "grown.json"
        grown.write_text(json.dumps(spec), encoding="utf-8")
        first, second = tmp / "r1", tmp / "r2"
        self.assertEqual(run_cli(
            "run", "--spec", str(FIX / "plate_hole.json"),
            "--out", str(first)).returncode, 0)
        proc = run_cli("run", "--spec", str(grown), "--out", str(second))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        v1 = load_json(first / "validation.json")["native"]
        v2 = load_json(second / "validation.json")["native"]
        self.assertAlmostEqual(v2["bbox_size"][0] - v1["bbox_size"][0], 1.0,
                               places=6)
        self.assertEqual(v2["bbox_size"][1:], v1["bbox_size"][1:])
        self.assertNotEqual(sha256_file(first / "model.step"),
                            sha256_file(second / "model.step"))

    def test_placed_box_honors_feature_frame(self):
        tmp = Path(tempfile.mkdtemp(prefix="cadplaced"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "rev1"
        proc = run_cli("run", "--spec", str(FIX / "placed_box.json"),
                       "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        native = load_json(out / "validation.json")["native"]
        # R90z + (100,0,0): x in [80,100], y in [0,30], z in [0,10].
        self.assertEqual(native["bbox_mm"], [80.0, 0.0, 0.0, 100.0, 30.0, 10.0])

    def test_generated_script_reproduces_geometry_offline(self):
        import runpy
        tmp = Path(tempfile.mkdtemp(prefix="cadregen2"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "rev1"
        self.assertEqual(run_cli(
            "run", "--spec", str(FIX / "plate_hole.json"),
            "--out", str(out)).returncode, 0)
        namespace = runpy.run_path(str(out / "model.py"))
        solids = namespace["main"]()
        self.assertEqual(set(solids), {"base_plate"})
        from anastylos_cad.builder import solid_report
        report = solid_report(solids["base_plate"])
        native = load_json(out / "validation.json")["native"]
        self.assertEqual(report["bbox_size"], native["bbox_size"])
        self.assertAlmostEqual(report["volume"], native["volume"], places=9)


class PreKernelRejection(unittest.TestCase):
    def test_invalid_spec_rejected_before_outdir_exists(self):
        tmp = Path(tempfile.mkdtemp(prefix="cadbad"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "rev_bad"
        proc = run_cli("run", "--spec",
                       str(FIX / "invalid_negative_size.json"),
                       "--out", str(out))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reject"], "schema-reject")
        # The kernel runs only after the outdir is prepared; no outdir
        # means validation rejected before any kernel call.
        self.assertFalse(out.exists())


class WorkerGuards(unittest.TestCase):
    def test_refuses_nonempty_outdir(self):
        tmp = Path(tempfile.mkdtemp(prefix="cadfull"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "rev1"
        out.mkdir()
        (out / "prior.txt").write_text("prior revision", encoding="utf-8")
        proc = run_cli("run", "--spec", str(FIX / "plate_hole.json"),
                       "--out", str(out))
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        self.assertEqual((out / "prior.txt").read_text(encoding="utf-8"),
                         "prior revision")

    def test_refuses_oversize_spec(self):
        tmp = Path(tempfile.mkdtemp(prefix="cadbig"))
        self.addCleanup(shutil.rmtree, tmp, True)
        big = tmp / "big.json"
        big.write_text('{"pad": "' + "x" * (1024 * 1024 + 1) + '"}',
                       encoding="utf-8")
        proc = run_cli("validate", "--spec", str(big))
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)


class Subcommands(unittest.TestCase):
    def test_validate_exit_codes(self):
        ok_proc = run_cli("validate", "--spec", str(FIX / "plate_hole.json"))
        self.assertEqual(ok_proc.returncode, 0)
        self.assertTrue(json.loads(ok_proc.stdout)["ok"])
        bad_proc = run_cli("validate", "--spec",
                           str(FIX / "invalid_dangling_parent.json"))
        self.assertEqual(bad_proc.returncode, 2)
        self.assertEqual(json.loads(bad_proc.stdout)["reject"],
                         "semantic-reject")

    def test_reopen_reports_one_solid(self):
        tmp = Path(tempfile.mkdtemp(prefix="cadreopen"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "rev1"
        self.assertEqual(run_cli(
            "build", "--spec", str(FIX / "cylinder.json"),
            "--out", str(out)).returncode, 0)
        proc = run_cli("reopen", "--step", str(out / "model.step"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["solids"], 1)
        self.assertTrue(payload["reports"][0]["is_valid"])


class UnresolvedHandoff(unittest.TestCase):
    def test_null_uncertainty_surfaces_in_envelope(self):
        tmp = Path(tempfile.mkdtemp(prefix="cadunc"))
        self.addCleanup(shutil.rmtree, tmp, True)
        spec = load_json(FIX / "plate_hole.json")
        spec["features"][1]["uncertainty_mm"] = None
        loose = tmp / "loose.json"
        loose.write_text(json.dumps(spec), encoding="utf-8")
        out = tmp / "rev1"
        self.assertEqual(run_cli(
            "run", "--spec", str(loose), "--out", str(out)).returncode, 0)
        validation = load_json(out / "validation.json")
        self.assertEqual(
            [u["feature_id"] for u in validation["unresolved_features"]],
            ["hole_1"])
        envelope = missing_feature_envelope(spec, validation)
        self.assertEqual(
            [f["feature_id"] for f in envelope["underconstrained_features"]],
            ["hole_1"])


if __name__ == "__main__":
    unittest.main()
