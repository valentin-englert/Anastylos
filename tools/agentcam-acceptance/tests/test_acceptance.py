"""Maintained suite for the AgentCam Wave-1 acceptance harness.

Run from tools/agentcam-acceptance:
    python -m unittest discover -s tests
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import accept  # noqa: E402
import checks  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
CONTRACT = ROOT / "capture-companion" / "control" / "contracts"
CONTRACT_SHA = "ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663"


def load(name):
    return checks.load_json_strict(CONTRACT / name)


class ContractTest(unittest.TestCase):
    def test_manifest_all_match(self):
        loaded = accept.load_contract(CONTRACT)
        self.assertEqual(loaded["manifest_failures"], [])
        self.assertEqual(len(loaded["files"]), 13)

    def test_contract_sha_recomputes(self):
        manifest = load("CONTRACT.json")
        self.assertEqual(checks.recompute_contract_sha(manifest["files"]),
                         CONTRACT_SHA)
        self.assertEqual(manifest["contract_sha256"], CONTRACT_SHA)

    def test_manifest_detects_tampered_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "contracts"
            shutil.copytree(CONTRACT, clone)
            vec = clone / "vectors" / "guidance-signs.json"
            raw = vec.read_bytes().replace(b"120,", b"121,", 1)
            vec.write_bytes(raw)
            loaded = accept.load_contract(clone)
            self.assertTrue(loaded["manifest_failures"])
            gate = accept.g_contract_manifest(loaded)
            self.assertEqual(gate["verdict"], "FAIL")

    def test_uuid_regex_matches_schema_pattern(self):
        ctl = load("schemas/control.schema.json")
        pattern = ctl["$defs"]["ViewRequest"]["properties"][
            "request_id"]["pattern"]
        self.assertEqual(checks.UUID_RE.pattern, pattern)


class ControlVectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctl = load("schemas/control.schema.json")
        cls.base = load("vectors/control-request-valid.json")

    def test_valid_request_clean(self):
        self.assertEqual(checks.check_view_request(self.base, self.ctl), [])

    def test_tampered_revision_rejected(self):
        bad = json.loads(json.dumps(self.base))
        bad["revision"] = 0
        self.assertTrue(checks.check_view_request(bad, self.ctl))

    def test_set_pointer_both_forms(self):
        doc = {"a": [{"b": 1}]}
        checks.set_pointer(doc, "/a/0/b", 2)
        self.assertEqual(doc["a"][0]["b"], 2)
        checks.set_pointer(doc, "#/a/0/b", 3)
        self.assertEqual(doc["a"][0]["b"], 3)

    def test_negative_cases_classify(self):
        results = checks.check_control_negative(
            self.base, load("vectors/control-negative.json"), self.ctl)
        self.assertEqual(len(results), 8)
        for res in results:
            self.assertTrue(res["ok"], res)

    def test_transitions_mapped(self):
        res = checks.check_control_transitions(
            load("vectors/control-transitions.json"), self.ctl)
        self.assertEqual(res["n_cases"], 17)
        self.assertTrue(res["ids_unique"])
        for case in res["per_case"]:
            self.assertTrue(case["ok"], case)


class GuidanceTest(unittest.TestCase):
    def test_recompute_two_of_two(self):
        res = checks.guidance_recompute(load("vectors/guidance-signs.json"))
        self.assertEqual(res["notes"], [])
        self.assertEqual(len(res["per_case"]), 2)
        for case in res["per_case"]:
            self.assertTrue(case["ok"], case)
        self.assertEqual(res["per_case"][0]["cues"],
                         ["right 20", "up 10", "back 50"])
        self.assertEqual(res["per_case"][1]["cues"], ["closer 50"])
        self.assertEqual(len(res["required_extensions"]), 7)


class SpatialCadTest(unittest.TestCase):
    def test_spatial_valid_clean(self):
        violations = checks.check_spatial(
            load("vectors/spatial-valid.json"),
            load("schemas/spatial-evidence.schema.json"))
        self.assertEqual(violations, [])

    def test_cad_valid_clean(self):
        base = load("vectors/cad-feature-valid.json")
        schema = load("schemas/cad-feature-model.schema.json")
        self.assertEqual(checks.check_cad(base, schema), [])
        self.assertEqual(checks.cad_semantics(base), [])

    def test_cad_invalid_classify(self):
        results = checks.check_cad_invalid(
            load("vectors/cad-feature-valid.json"),
            load("vectors/cad-feature-invalid.json"),
            load("schemas/cad-feature-model.schema.json"))
        self.assertEqual(len(results), 6)
        for res in results:
            self.assertTrue(res["ok"], res)

    def test_det3_identity_and_reflection(self):
        ident = [1, 0, 0, 0, 0, 1, 0, 0,
                 0, 0, 1, 0, 0, 0, 0, 1]
        mirror = [1, 0, 0, 0, 0, 1, 0, 0,
                  0, 0, -1, 0, 0, 0, 0, 1]
        self.assertAlmostEqual(checks.det3(ident), 1.0)
        self.assertAlmostEqual(checks.det3(mirror), -1.0)

    def test_strict_json_rejects_nan(self):
        with self.assertRaises(ValueError):
            checks.loads_strict('{"probe": NaN}')


class McpBundleTest(unittest.TestCase):
    def test_bundle_18_no_dangling(self):
        control_sha = checks.sha256_file(
            CONTRACT / "schemas" / "control.schema.json")[1]
        res = checks.check_mcp_bundle(
            load("schemas/mcp-tools.expanded.json"),
            load("schemas/mcp-tools.json"), control_sha)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["schemas_present"], 18)
        self.assertEqual(res["dangling"], [])
        self.assertEqual(res["external"], [])


class RunIntegrationTest(unittest.TestCase):
    @staticmethod
    def git_head():
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
        return proc.stdout.strip()

    def test_run_emits_sha_bound_scorecard(self):
        head = self.git_head()
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-B", str(HERE / "accept.py"), "run",
                 "--candidate", head, "--expect-contract", CONTRACT_SHA,
                 "--out", tmp, "--skip-gates", "regression-selftests"],
                cwd=ROOT, capture_output=True, text=True, timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            card = json.loads((Path(tmp) / "acceptance-run.json")
                              .read_text(encoding="utf-8"))
            self.assertTrue(card["contract_match"])
            self.assertEqual(card["contract_sha256"], CONTRACT_SHA)
            verdicts = {g["id"]: g["verdict"] for g in card["gates"]}
            self.assertNotIn("FAIL", verdicts.values())
            self.assertEqual(verdicts["vector-control-valid"], "PASS")
            self.assertEqual(verdicts["E1"], "PENDING_EVIDENCE")
            self.assertEqual(verdicts["H0"], "PENDING_EVIDENCE")
            for entry in card["evidence"]:
                raw = (ROOT / entry["path"]).read_bytes()
                self.assertEqual(checks.sha256_file(ROOT / entry["path"])[1],
                                 entry["sha256"])
                self.assertEqual(len(raw), entry["bytes"])

    def test_run_fails_on_tampered_vector(self):
        head = self.git_head()
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "contracts"
            shutil.copytree(CONTRACT, clone)
            vec = clone / "vectors" / "control-request-valid.json"
            doc = json.loads(vec.read_text(encoding="utf-8"))
            doc["revision"] = 0
            vec.write_text(json.dumps(doc), encoding="utf-8")
            out = Path(tmp) / "out"
            proc = subprocess.run(
                [sys.executable, "-B", str(HERE / "accept.py"), "run",
                 "--candidate", head, "--contract", str(clone),
                 "--out", str(out),
                 "--gates", "contract-manifest,vector-control-valid"],
                cwd=ROOT, capture_output=True, text=True, timeout=600)
            self.assertEqual(proc.returncode, 1, proc.stdout[-2000:])
            card = json.loads((out / "acceptance-run.json")
                              .read_text(encoding="utf-8"))
            verdicts = {g["id"]: g["verdict"] for g in card["gates"]}
            self.assertEqual(verdicts["contract-manifest"], "FAIL")
            self.assertEqual(verdicts["vector-control-valid"], "FAIL")

    def test_run_rejects_stale_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-B", str(HERE / "accept.py"), "run",
                 "--candidate", "0" * 40, "--out", tmp,
                 "--gates", "run-integrity"],
                cwd=ROOT, capture_output=True, text=True, timeout=600)
            self.assertEqual(proc.returncode, 1, proc.stdout[-2000:])

    def test_replay_wired(self):
        proc = subprocess.run(
            [sys.executable, "-B", str(HERE / "accept.py"), "replay",
             "--vectors", "guidance-signs"],
            cwd=ROOT, capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn('"cues_ok": true', proc.stdout)


if __name__ == "__main__":
    unittest.main()
