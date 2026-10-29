"""Parametric editability: stable params, operator-wins merge, FCStd export.

Covers the follow-up acceptance gate: a user changes dimensions/hole
parameters, regenerates deterministically, and gets a native parametric
FreeCAD document (Part feature tree, not an imported STEP blob).
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from anastylos_cad import params as P

HERE = os.path.dirname(os.path.abspath(__file__))
CAD_ROOT = os.path.dirname(HERE)
PLATE = os.path.join(CAD_ROOT, "fixtures", "synthetic", "plate_hole.json")
CADWORK = os.path.join(CAD_ROOT, "cadwork.py")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_plate():
    with open(PLATE, encoding="utf-8") as handle:
        return json.load(handle)


class ExtractParamsTest(unittest.TestCase):
    def test_stable_ids_values_units(self):
        table = P.extract_params(load_plate())
        self.assertEqual(table["base_plate.size_x_mm"]["value"], 80)
        self.assertEqual(table["base_plate.size_y_mm"]["value"], 50)
        self.assertEqual(table["base_plate.size_z_mm"]["value"], 6)
        self.assertEqual(table["hole_1.radius_mm"]["value"], 3)
        self.assertEqual(table["hole_1.center_x_mm"]["value"], 40)
        self.assertEqual(table["hole_1.center_y_mm"]["value"], 25)
        self.assertEqual(table["hole_1.center_z_mm"]["value"], 0)
        for pid, spec in table.items():
            self.assertEqual(spec["units"], "mm", pid)
            self.assertIn("minimum", spec)
            self.assertIn("maximum", spec)

    def test_depth_is_derived_not_editable(self):
        table = P.extract_params(load_plate())
        self.assertIn("hole_1.depth_mm", table)
        self.assertFalse(table["hole_1.depth_mm"]["editable"])


class ApplyEditsTest(unittest.TestCase):
    def test_edit_dimensions_and_hole(self):
        model = load_plate()
        base = P.model_sha256(model)
        edits = {"schema": "anastylos-cad-param-edits@1",
                 "base_model_sha256": base, "author": "operator-test",
                 "edits": [{"param_id": "base_plate.size_x_mm", "value": 90,
                            "reason": "caliper says 90"},
                           {"param_id": "hole_1.radius_mm", "value": 4,
                            "reason": "reamed hole"}]}
        new_model, prov = P.apply_edits(model, edits)
        feats = {f["feature_id"]: f for f in new_model["features"]}
        self.assertEqual(feats["base_plate"]["parameters"]["size_mm"],
                         [90, 50, 6])
        self.assertEqual(feats["hole_1"]["parameters"]["radius_mm"], 4)
        self.assertEqual(new_model["revision"], model["revision"] + 1)
        self.assertEqual(new_model["parent_model_sha256"], base)
        # input model untouched
        self.assertEqual(model["features"][0]["parameters"]["size_mm"],
                         [80, 50, 6])
        # provenance: revised params operator-specified, estimate preserved
        entry = prov["params"]["base_plate.size_x_mm"]
        self.assertEqual(entry["origin"], "operator-specified")
        self.assertEqual(entry["history"][0]["value"], 80)
        self.assertEqual(entry["history"][0]["origin"], "estimated")
        self.assertEqual(entry["history"][-1]["value"], 90)
        self.assertEqual(prov["params"]["base_plate.size_y_mm"]["origin"],
                         "estimated")

    def test_rejects_unknown_param_out_of_range_and_stale_base(self):
        model = load_plate()
        base = P.model_sha256(model)
        with self.assertRaises(P.ParamError):
            P.apply_edits(model, {"schema": "anastylos-cad-param-edits@1",
                                  "base_model_sha256": base,
                                  "edits": [{"param_id": "nope.x", "value": 1}]})
        with self.assertRaises(P.ParamError):
            P.apply_edits(model, {"schema": "anastylos-cad-param-edits@1",
                                  "base_model_sha256": base,
                                  "edits": [{"param_id": "hole_1.radius_mm",
                                             "value": -2}]})
        with self.assertRaises(P.ParamError):
            P.apply_edits(model, {"schema": "anastylos-cad-param-edits@1",
                                  "base_model_sha256": base,
                                  "edits": [{"param_id": "hole_1.depth_mm",
                                             "value": 9}]})
        with self.assertRaises(P.ParamError):
            P.apply_edits(model, {"schema": "anastylos-cad-param-edits@1",
                                  "base_model_sha256": "0" * 64,
                                  "edits": [{"param_id": "hole_1.radius_mm",
                                             "value": 4}]})

    def test_deterministic(self):
        model = load_plate()
        edits = {"schema": "anastylos-cad-param-edits@1",
                 "base_model_sha256": P.model_sha256(model),
                 "edits": [{"param_id": "base_plate.size_x_mm", "value": 90}]}
        first, _ = P.apply_edits(model, edits)
        second, _ = P.apply_edits(model, edits)
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))


class MergeEstimatesTest(unittest.TestCase):
    def test_operator_params_win_new_estimates_recorded(self):
        model = load_plate()
        base = P.model_sha256(model)
        edited, prov = P.apply_edits(
            model, {"schema": "anastylos-cad-param-edits@1",
                    "base_model_sha256": base,
                    "edits": [{"param_id": "base_plate.size_x_mm",
                               "value": 90}]})
        # automated reconstruction re-estimates everything incl. size_x=80
        merged, report, _prov2 = P.merge_estimates(edited, prov, load_plate())
        feats = {f["feature_id"]: f for f in merged["features"]}
        self.assertEqual(feats["base_plate"]["parameters"]["size_mm"][0], 90)
        kept = [r for r in report if r["param_id"] == "base_plate.size_x_mm"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["action"], "kept_operator_value")
        self.assertEqual(kept[0]["superseded_estimate"], 80)


class FcstdExportTest(unittest.TestCase):
    def test_tree_properties_match_params(self):
        from anastylos_cad import fcstd
        doc = fcstd.build_document(load_plate())
        self.assertEqual(doc["tree"]["BasePlate"]["Length"], 80)
        self.assertEqual(doc["tree"]["BasePlate"]["Width"], 50)
        self.assertEqual(doc["tree"]["Tool_Hole1"]["Radius"], 3)
        blob = fcstd.write_fcstd_bytes(load_plate())
        names = set()
        with zipfile.ZipFile(__import__("io").BytesIO(blob)) as archive:
            names = set(archive.namelist())
            xml_text = archive.read("Document.xml").decode("utf-8")
        self.assertIn("Document.xml", names)
        self.assertIn("GuiDocument.xml", names)
        self.assertNotIn("PartShape.brp", names)  # no imported-blob solid
        root = ET.fromstring(xml_text)  # must be well-formed
        types = {o.get("type") for o in root.iter("Object")}
        self.assertIn("Part::Box", types)
        self.assertIn("Part::Cylinder", types)
        self.assertIn("Part::Cut", types)

    def test_tree_rebuild_matches_kernel_geometry(self):
        from anastylos_cad import builder, fcstd
        model = load_plate()
        solids = builder.build_solids(model["features"])
        want = builder.solid_report(solids["base_plate"])
        got = fcstd.rebuild_tree_report(model)  # independent OCP rebuild
        for key in ("bbox_size",):
            for a, b in zip(want[key], got[key]):
                self.assertAlmostEqual(a, b, places=6)
        self.assertAlmostEqual(want["volume"], got["volume"], delta=1e-6)

    def test_macro_is_parametric_script(self):
        from anastylos_cad import fcstd
        macro = fcstd.generate_macro(load_plate())
        self.assertIn("base_plate_size_x_mm = 80", macro)
        self.assertIn("hole_1_radius_mm = 3", macro)
        self.assertIn('Doc.addObject("Part::Box"', macro)
        self.assertIn('Doc.addObject("Part::Cut"', macro)
        self.assertIn("Doc.recompute()", macro)
        self.assertNotIn("importStep", macro)


class EditChainCliTest(unittest.TestCase):
    def test_file_hash_pins_next_edit_hole_move(self):
        tmp = tempfile.mkdtemp(prefix="cadchain")
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        model = load_plate()
        edits1 = {"schema": "anastylos-cad-param-edits@1",
                  "base_model_sha256": P.model_sha256(model),
                  "edits": [{"param_id": "base_plate.size_x_mm", "value": 90}]}
        e1 = os.path.join(tmp, "e1.json")
        with open(e1, "w", encoding="utf-8") as handle:
            json.dump(edits1, handle)
        r1 = os.path.join(tmp, "r1")
        proc = subprocess.run([sys.executable, CADWORK, "apply-edits",
                               "--spec", PLATE, "--edits", e1, "--out", r1],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        spec1 = os.path.join(r1, "feature-model.json")
        with open(spec1, encoding="utf-8") as handle:
            parsed = json.load(handle)
        # file bytes are the canonical bytes: pin by file hash works
        self.assertEqual(sha256_file(spec1), P.model_sha256(parsed))
        edits2 = {"schema": "anastylos-cad-param-edits@1",
                  "base_model_sha256": sha256_file(spec1),
                  "edits": [{"param_id": "hole_1.center_x_mm", "value": 45,
                             "reason": "shift hole +5x"}]}
        e2 = os.path.join(tmp, "e2.json")
        with open(e2, "w", encoding="utf-8") as handle:
            json.dump(edits2, handle)
        r2 = os.path.join(tmp, "r2")
        proc = subprocess.run([sys.executable, CADWORK, "apply-edits",
                               "--spec", spec1, "--edits", e2, "--out", r2],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(os.path.join(r2, "feature-model.json"),
                  encoding="utf-8") as handle:
            moved = json.load(handle)
        hole = [f for f in moved["features"]
                if f["feature_id"] == "hole_1"][0]
        self.assertEqual(hole["T_CAD_feature"]["matrix"][3], 45)
        self.assertEqual(moved["revision"], model["revision"] + 2)


if __name__ == "__main__":
    unittest.main()
