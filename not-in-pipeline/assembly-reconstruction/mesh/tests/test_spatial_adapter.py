"""Spatial adapter: sealed-evidence ingest, PENDING statuses, typed rejects.

Numbered task requirements (all with correctly-labelled synthetic fixtures):

(1) Happy path: 3+ synthetic views (one epoch) shaped like the real
    contracts that PASS the production validator as accept+metric_ready,
    plus linked masks -> adapter returns MeshRequest, reconstructs
    end-to-end to a mesh, manifest references sidecar/profile/image/mask
    hashes.
(2) Invalid sidecar (bad hash binding) rejected via production validator.
(3) needs_review sidecar excluded from the request.
(4) Mixed pose epochs rejected.
(5) Missing mask file -> PENDING_MASKS with image ids.
(6) No metric-qualified images -> PENDING_EVIDENCE with per-image gates.
(7) Tampered image bytes (sha mismatch) rejected.
(8) Stale pose sample rejected per the production staleness rule.

Every fixture file is labelled synthetic: committed fixtures under
``fixtures_spatial/`` carry ``fixture_provenance: synthetic``; runtime
sidecars carry synthetic producer/limitations/method; staged image bytes
carry a ``synthetic-mesh-fixture`` marker; package manifests, capture sets
and mask sidecars carry ``fixture_provenance: synthetic``. Mask
``provenance`` stays ``manual`` (editor ``synthetic-test-operator``)
because the adapter rejects ``synthetic`` mask provenance
(``MASK_PROVENANCE_NOT_OPERATOR``): production-API gap documented in
``fixtures_spatial/README.md``.
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image

from anastylos_mesh.geometry import (
    MeshCoverageError,
    MeshProvenanceError,
)
from anastylos_mesh.spatial_adapter import (
    MeshRequest,
    PendingEvidence,
    PendingMasks,
    build_mesh_request,
)

ROOT = Path(__file__).resolve().parents[3]
FROZEN_SIDECAR = (
    ROOT / "capture-companion" / "control" / "contracts"
    / "vectors" / "spatial-valid.json"
)
FIXTURES = Path(__file__).resolve().parent / "fixtures_spatial"

SESSION_ID = "10000000-0000-4000-8000-000000000001"
SOURCE_ID = "10000000-0000-4000-8000-000000000002"
BOARD_ID = "00000000-0000-4000-8000-000000000011"
PROFILE_ID = "00000000-0000-4000-8000-000000000010"
EPOCH = "epoch-static-01"
IMAGE_MARKER = b"synthetic-mesh-fixture:"


def _uuid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mesh-adapter-test:{name}"))


def _prod():
    from anastylos_mesh.spatial_adapter import _load_production
    return _load_production()


def _fixture_profile_bytes() -> bytes:
    return (FIXTURES / "profile.synthetic-v1.json").read_bytes()


def _profile_doc():
    return json.loads(_fixture_profile_bytes().decode("utf-8"))


def _sidecar_for_view(tag: str, center, epoch: str = EPOCH):
    doc = json.loads(FROZEN_SIDECAR.read_text(encoding="utf-8"))
    doc["evidence_id"] = _uuid(f"{tag}-evidence")
    doc["session_id"] = SESSION_ID
    doc["source_id"] = SOURCE_ID
    doc["request_id"] = _uuid(f"{tag}-request")
    doc["request_revision"] = 1
    doc["attempt_id"] = _uuid(f"{tag}-attempt")
    package_id = _uuid(f"{tag}-package")
    image_id = _uuid(f"{tag}-image")
    image_bytes = b"\xff\xd8" + IMAGE_MARKER + tag.encode() + b"\x00" * 2000
    image_sha = hashlib.sha256(image_bytes).hexdigest()
    doc["asset"] = {
        "package_id": package_id,
        "observation_id": _uuid(f"{tag}-obs"),
        "image_id": image_id,
        "asset_id": _uuid(f"{tag}-asset"),
        "sha256": image_sha,
        "byte_length": len(image_bytes),
        "mime": "image/jpeg",
    }
    doc["pose"]["image_sha256"] = image_sha
    doc["pose"]["T_board_camera"]["matrix"] = [
        1, 0, 0, center[0],
        0, 1, 0, center[1],
        0, 0, 1, center[2],
        0, 0, 0, 1,
    ]
    doc["target"]["object_pose_epoch_ref"] = epoch
    doc["provenance"]["producer"] = "synthetic-mesh-adapter-fixture"
    doc["provenance"]["app_version"] = "synthetic-test-run"
    doc["provenance"]["build_sha"] = "synthetic-test-build"
    doc["pose"]["method"] = "synthetic-saved-image-fixture"
    doc["limitations"] = [
        "SYNTHETIC_TEST_FIXTURE_ONLY: mesh adapter test sidecar; "
        "no real image or physical evidence exists."
    ]
    return doc, image_bytes


def _write_dataset(tmp: Path, views, profile=True):
    """Stage a dataset_dir; views = [(tag, center, epoch, mutate|None)]."""
    ds = tmp / "dataset"
    (ds / "sidecars").mkdir(parents=True)
    (ds / "spatial" / "profiles").mkdir(parents=True)
    (ds / "packages").mkdir(parents=True)
    prod = _prod()
    if profile:
        raw = _fixture_profile_bytes()
        profile_sha = hashlib.sha256(raw).hexdigest()
        (ds / "spatial" / "profiles" / f"{profile_sha}.json").write_bytes(raw)
    else:
        profile_sha = "d" * 64
    receipts = []
    for tag, center, epoch, mutate in views:
        sidecar, image_bytes = _sidecar_for_view(tag, center, epoch)
        sidecar["calibration"]["profile_sha256"] = profile_sha
        if mutate is not None:
            mutate(sidecar)
        sidecar_sha = prod.body_sha256(sidecar)
        (ds / "sidecars" / f"{sidecar_sha}.json").write_text(
            json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
        )
        package_id = sidecar["asset"]["package_id"]
        pkg = ds / "packages" / package_id
        pkg.mkdir(parents=True)
        (pkg / "img.jpg").write_bytes(image_bytes)
        (pkg / "manifest.json").write_text(
            json.dumps({"fixture_provenance": "synthetic",
                        "fixture_note": "SYNTHETIC_TEST_FIXTURE_ONLY",
                        "files": [{"image_id": sidecar["asset"]["image_id"],
                                   "path": "img.jpg"}]},
                       indent=2) + "\n",
            encoding="utf-8",
        )
        receipts.append({
            "receipt_id": _uuid(f"{tag}-receipt"),
            "session_id": SESSION_ID,
            "source_id": SOURCE_ID,
            "request_id": sidecar["request_id"],
            "request_revision": 1,
            "attempt_id": sidecar["attempt_id"],
            "package_id": package_id,
            "observation_id": sidecar["asset"]["observation_id"],
            "image_id": sidecar["asset"]["image_id"],
            "image_sha256": sidecar["asset"]["sha256"],
            "package_sha256": "c" * 64,
            "sidecar_sha256": sidecar_sha,
            "sidecar_revision": 1,
            "accepted_at": "2026-10-29T12:00:02.000Z",
            "status": "qualified",
            "reason_codes": [],
        })
    head = {
        "fixture_provenance": "synthetic",
        "fixture_note": "SYNTHETIC_TEST_FIXTURE_ONLY",
        "capture_set_id": _uuid("capture-set"),
        "session_id": SESSION_ID,
        "revision": 1,
        "created_at": "2026-10-29T12:00:03.000Z",
        "status": "complete",
        "required_requests": [
            {"request_id": r["request_id"], "request_revision": 1}
            for r in receipts
        ],
        "receipts": receipts,
        "omissions": [],
        "calibration_assets": [],
    }
    head["manifest_sha256"] = prod.body_sha256(head)
    (ds / "capture_set.json").write_text(
        json.dumps(head, indent=2) + "\n", encoding="utf-8"
    )
    return ds


def _write_masks(mask_dir: Path, dataset_dir: Path, provenance="manual"):
    import hashlib as _hl

    capture = json.loads(
        (dataset_dir / "capture_set.json").read_text(encoding="utf-8")
    )
    mask_dir.mkdir(parents=True, exist_ok=True)
    for receipt in capture["receipts"]:
        sha = receipt["image_sha256"]
        gray = np.full((720, 1280), 255, dtype=np.uint8)
        png_path = mask_dir / f"{sha}.png"
        Image.fromarray(gray, mode="L").save(png_path)
        mask_sha = _hl.sha256(png_path.read_bytes()).hexdigest()
        (mask_dir / f"{sha}.json").write_text(
            json.dumps({"fixture_provenance": "synthetic",
                        "mask_sha256": mask_sha, "provenance": provenance,
                        "editor": "synthetic-test-operator",
                        "edited_bool": True},
                       indent=2) + "\n",
            encoding="utf-8",
        )


def _config(**overrides):
    cfg = json.loads(
        (FIXTURES / "config.synthetic-v1.json").read_text(encoding="utf-8")
    )
    cfg = {"grid": copy.deepcopy(cfg["grid"]),
           "carve": copy.deepcopy(cfg["carve"])}
    cfg.update(overrides)
    return cfg


def _two_view_dataset(tmp: Path, **kwargs):
    return _write_dataset(tmp, [
        ("view-a", (100.0, 80.0, -400.0), EPOCH, None),
        ("view-b", (-100.0, 80.0, -400.0), EPOCH, None),
    ], **kwargs)


def _three_view_dataset(tmp: Path, **kwargs):
    return _write_dataset(tmp, [
        ("view-a", (100.0, 80.0, -400.0), EPOCH, None),
        ("view-b", (-100.0, 80.0, -400.0), EPOCH, None),
        ("view-c", (0.0, -120.0, -420.0), EPOCH, None),
    ], **kwargs)


def _snapshot_files(root: Path) -> set:
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _validate_one_production(dataset_dir: Path, receipt: dict) -> dict:
    """Run the PRODUCTION validator on one staged receipt (no adapter)."""
    prod = _prod()
    sidecar_sha = receipt["sidecar_sha256"]
    sidecar, _ = prod.load_json_strict(
        dataset_dir / "sidecars" / f"{sidecar_sha}.json")
    request = {
        "receipt_id": receipt.get("receipt_id"),
        "package_sha256": receipt.get("package_sha256"),
        "image_sha256": receipt.get("image_sha256"),
        "sidecar_sha256": sidecar_sha,
        "package_id": receipt.get("package_id"),
        "image_id": receipt.get("image_id"),
        "sidecar": sidecar,
    }
    ctx = prod.build_ctx(request, dataset_dir)
    return prod.validate_sidecar(sidecar, ctx)


def _assert_synthetic_labels(testcase, dataset_dir: Path,
                             mask_dir: Path | None = None):
    """Every fixture file carries an explicit synthetic label."""
    profiles = list((dataset_dir / "spatial" / "profiles").glob("*.json"))
    testcase.assertGreaterEqual(len(profiles), 1)
    for path in profiles:
        doc = json.loads(path.read_text(encoding="utf-8"))
        testcase.assertEqual(doc.get("fixture_provenance"), "synthetic")
    capture = json.loads(
        (dataset_dir / "capture_set.json").read_text(encoding="utf-8"))
    testcase.assertEqual(capture.get("fixture_provenance"), "synthetic")
    for receipt in capture["receipts"]:
        sidecar = json.loads(
            (dataset_dir / "sidecars"
             / f"{receipt['sidecar_sha256']}.json").read_text(
                 encoding="utf-8"))
        testcase.assertIn("synthetic",
                          sidecar["provenance"]["producer"].lower())
        testcase.assertTrue(any("SYNTHETIC" in item for item in
                                sidecar["limitations"]))
        testcase.assertIn("synthetic", sidecar["pose"]["method"].lower())
        pkg = dataset_dir / "packages" / receipt["package_id"]
        manifest = json.loads(
            (pkg / "manifest.json").read_text(encoding="utf-8"))
        testcase.assertEqual(manifest.get("fixture_provenance"), "synthetic")
        img_bytes = (pkg / "img.jpg").read_bytes()
        testcase.assertIn(IMAGE_MARKER, img_bytes)
    if mask_dir is not None and mask_dir.is_dir():
        for path in mask_dir.glob("*.json"):
            meta = json.loads(path.read_text(encoding="utf-8"))
            testcase.assertEqual(meta.get("fixture_provenance"), "synthetic")
            testcase.assertIn("synthetic", meta["editor"].lower())


class TestSpatialAdapter(unittest.TestCase):
    def test_ready_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _two_view_dataset(tmp)
            md = tmp / "masks"
            _write_masks(md, ds)
            req = build_mesh_request(ds, md, _config())
            self.assertEqual(req.status, "READY")
            self.assertEqual(len(req.cameras), 2)
            self.assertEqual(len(req.masks), 2)
            self.assertEqual(list(req.cameras[0].K[:1]), [800.0])
            self.assertEqual(req.cameras[0].pose_epoch, EPOCH)
            self.assertEqual(req.grid.dims, (8, 8, 8))
            prov = req.provenance
            self.assertEqual(len(prov["capture_set_manifest_sha256"]), 64)
            self.assertEqual(prov["qualified_count"], 2)
            self.assertIn("profile-required", prov["assumptions"][5])
            self.assertEqual(len(prov["views"]), 2)
            self.assertEqual(len(prov["mask_entries"]), 2)
            self.assertTrue(
                prov["views"][0]["validator"]["metric_ready"]
            )

    def test_pending_evidence_rejected_sidecar(self):
        def _break(doc):
            doc["pose"]["image_sha256"] = "f" * 64

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [("solo", (100.0, 80.0, -400.0),
                                       EPOCH, _break)])
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.status, "PENDING_EVIDENCE")
            self.assertEqual(len(req.gates), 1)
            gate = req.gates[0]
            self.assertEqual(gate["gate"], "VALIDATOR_NOT_QUALIFIED")
            self.assertEqual(gate["detail"]["verdict"], "reject")
            self.assertIn("WRONG_SOURCE_IMAGE",
                          gate["detail"]["reasons"])

    def test_pending_evidence_no_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [])
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.gates[0]["gate"], "NO_RECEIPTS")

    def test_pending_evidence_profile_unstaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _two_view_dataset(tmp, profile=False)
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.gates[0]["gate"], "PROFILE_UNSTAGED")

    def test_pending_evidence_image_rehash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [("solo", (100.0, 80.0, -400.0),
                                       EPOCH, None)])
            capture = json.loads(
                (ds / "capture_set.json").read_text(encoding="utf-8")
            )
            pkg = ds / "packages" / capture["receipts"][0]["package_id"]
            (pkg / "img.jpg").write_bytes(b"tampered-bytes")
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.gates[0]["gate"], "IMAGE_REHASH_MISMATCH")

    def test_pending_masks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _two_view_dataset(tmp)
            md = tmp / "masks"
            md.mkdir()
            req = build_mesh_request(ds, md, _config())
            self.assertIsInstance(req, PendingMasks)
            self.assertEqual(req.status, "PENDING_MASKS")
            self.assertEqual(len(req.missing), 2)
            self.assertEqual(len(req.qualified), 2)
            self.assertTrue(all(m["image_sha256"] for m in req.missing))

    def test_mixed_epochs_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [
                ("view-a", (100.0, 80.0, -400.0), EPOCH, None),
                ("view-b", (-100.0, 80.0, -400.0), "epoch-moved-02", None),
            ])
            md = tmp / "masks"
            _write_masks(md, ds)
            with self.assertRaises(MeshProvenanceError) as ctx:
                build_mesh_request(ds, md, _config())
            self.assertIn("MIXED_POSE_EPOCH", str(ctx.exception))

    def test_sealed_manifest_tamper_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _two_view_dataset(tmp)
            path = ds / "capture_set.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["status"] = "complete-tampered"
            path.write_text(json.dumps(doc, indent=2) + "\n",
                            encoding="utf-8")
            with self.assertRaises(MeshProvenanceError) as ctx:
                build_mesh_request(ds, tmp / "masks", _config())
            self.assertIn("SEALED_MANIFEST_HASH_MISMATCH",
                          str(ctx.exception))

    def test_mask_hash_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [("solo", (100.0, 80.0, -400.0),
                                       EPOCH, None)])
            md = tmp / "masks"
            _write_masks(md, ds)
            capture = json.loads(
                (ds / "capture_set.json").read_text(encoding="utf-8")
            )
            sha = capture["receipts"][0]["image_sha256"]
            meta_path = md / f"{sha}.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["mask_sha256"] = "0" * 64
            meta_path.write_text(json.dumps(meta, indent=2) + "\n",
                                 encoding="utf-8")
            with self.assertRaises(MeshProvenanceError) as ctx:
                build_mesh_request(ds, md, _config())
            self.assertIn("MASK_SHA256_MISMATCH", str(ctx.exception))

    def test_receipt_not_qualified_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [("solo", (100.0, 80.0, -400.0),
                                       EPOCH, None)])
            prod = _prod()
            path = ds / "capture_set.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["receipts"][0]["status"] = "package_only"
            doc.pop("manifest_sha256", None)
            doc["manifest_sha256"] = prod.body_sha256(doc)
            path.write_text(json.dumps(doc, indent=2) + "\n",
                            encoding="utf-8")
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.gates[0]["gate"], "RECEIPT_NOT_QUALIFIED")

    def test_missing_grid_config_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _two_view_dataset(tmp)
            md = tmp / "masks"
            _write_masks(md, ds)
            with self.assertRaises(MeshCoverageError) as ctx:
                build_mesh_request(ds, md, {})
            self.assertIn("GRID_CONFIG_MISSING", str(ctx.exception))

    def test_cli_ingest_spatial_ready(self):
        from anastylos_mesh.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _two_view_dataset(tmp)
            md = tmp / "masks"
            _write_masks(md, ds)
            cfg_path = tmp / "config.json"
            cfg_path.write_text(json.dumps(_config()), encoding="utf-8")
            out = tmp / "out"
            code = main(["ingest-spatial", "--dataset", str(ds),
                         "--masks", str(md), "--config", str(cfg_path),
                         "--out", str(out)])
            self.assertEqual(code, 0)
            manifest = json.loads(
                (out / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema"], "mesh-evidence-v0")
            self.assertTrue(manifest["non_normative"])
            self.assertIn("ply", manifest["outputs"])
            self.assertIn("stl", manifest["outputs"])
            spatial = manifest["extra"]["spatial_provenance"]
            self.assertEqual(len(spatial["capture_set_manifest_sha256"]), 64)
            self.assertEqual(len(spatial["views"]), 2)
            self.assertTrue((out / "mesh.ply").exists())
            self.assertTrue((out / "mesh.stl").exists())

    def test_cli_ingest_spatial_pending_masks(self):
        from anastylos_mesh.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _two_view_dataset(tmp)
            md = tmp / "masks"
            md.mkdir()
            cfg_path = tmp / "config.json"
            cfg_path.write_text(json.dumps(_config()), encoding="utf-8")
            out = tmp / "out"
            code = main(["ingest-spatial", "--dataset", str(ds),
                         "--masks", str(md), "--config", str(cfg_path),
                         "--out", str(out)])
            self.assertEqual(code, 1)
            status = json.loads(
                (out / "ingest-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["status"], "PENDING_MASKS")
            self.assertEqual(len(status["missing"]), 2)
            self.assertFalse((out / "manifest.json").exists())


def _mutate_needs_review_null_gate(doc):
    doc["quality"]["pose"]["value"] = None


def _mutate_wrong_source_image(doc):
    doc["pose"]["image_sha256"] = "f" * 64


def _mutate_stale_interpolated(doc):
    doc["pose"]["solve_source"] = "timestamp_interpolated"
    doc["pose"]["sample_age_ms"] = 200
    doc["pose"]["bracketing_samples"] = [
        {"sample_id": _uuid("stale-br0"), "sensor_ns": "999000000",
         "pose_asset": {"asset_id": _uuid("stale-pa0"),
                       "sha256": "a" * 64}},
        {"sample_id": _uuid("stale-br1"), "sensor_ns": "1006000000",
         "pose_asset": {"asset_id": _uuid("stale-pa1"),
                       "sha256": "b" * 64}},
    ]


def _mutate_stale_saved(doc):
    doc["pose"]["sample_age_ms"] = 200


class TestSpatialAdapterNumbered(unittest.TestCase):
    """Task-numbered requirements (1)-(8) plus write/provenance checks."""

    def test_01_happy_path_three_views_end_to_end(self):
        from anastylos_mesh.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _three_view_dataset(tmp)
            md = tmp / "masks"
            _write_masks(md, ds)
            _assert_synthetic_labels(self, ds, md)
            capture = json.loads(
                (ds / "capture_set.json").read_text(encoding="utf-8"))
            self.assertEqual(len(capture["receipts"]), 3)
            for receipt in capture["receipts"]:
                result = _validate_one_production(ds, receipt)
                self.assertEqual(result["verdict"], "accept")
                self.assertTrue(result["metric_ready"])
            req = build_mesh_request(ds, md, _config())
            self.assertIsInstance(req, MeshRequest)
            self.assertEqual(req.status, "READY")
            self.assertEqual(len(req.cameras), 3)
            self.assertEqual(len(req.masks), 3)
            self.assertEqual(
                {c.pose_epoch for c in req.cameras}, {EPOCH})
            cfg_path = tmp / "config.json"
            cfg_path.write_text(json.dumps(_config()), encoding="utf-8")
            out = tmp / "out"
            code = main(["ingest-spatial", "--dataset", str(ds),
                         "--masks", str(md), "--config", str(cfg_path),
                         "--out", str(out)])
            self.assertEqual(code, 0)
            manifest = json.loads(
                (out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], "mesh-evidence-v0")
            self.assertTrue((out / "mesh.ply").exists())
            self.assertTrue((out / "mesh.stl").exists())
            image_hashes = [r["image_sha256"]
                            for r in capture["receipts"]]
            self.assertEqual(
                sorted(manifest["inputs"]["image_sha256"]),
                sorted(image_hashes))
            mask_hashes = [
                json.loads((md / f"{sha}.json").read_text(
                    encoding="utf-8"))["mask_sha256"]
                for sha in image_hashes
            ]
            self.assertEqual(
                sorted(manifest["inputs"]["mask_sha256"]),
                sorted(mask_hashes))
            spatial = manifest["extra"]["spatial_provenance"]
            self.assertEqual(
                spatial["capture_set_manifest_sha256"],
                capture["manifest_sha256"])
            self.assertEqual(len(spatial["views"]), 3)
            for view in spatial["views"]:
                self.assertTrue(
                    (ds / "sidecars"
                     / f"{view['sidecar_sha256']}.json").is_file())
                self.assertTrue(
                    (ds / "spatial" / "profiles"
                     / f"{view['profile_sha256']}.json").is_file())
                self.assertIn(view["image_sha256"], image_hashes)
            self.assertEqual(len(spatial["mask_entries"]), 3)
            for entry in spatial["mask_entries"]:
                self.assertIn(entry["mask_sha256"], mask_hashes)

    def test_02_invalid_sidecar_rejected_via_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [("solo", (100.0, 80.0, -400.0),
                                       EPOCH, _mutate_wrong_source_image)])
            capture = json.loads(
                (ds / "capture_set.json").read_text(encoding="utf-8"))
            result = _validate_one_production(
                ds, capture["receipts"][0])
            self.assertEqual(result["verdict"], "reject")
            self.assertIn("WRONG_SOURCE_IMAGE", result["reasons"])
            self.assertFalse(result["metric_ready"])
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.gates[0]["gate"],
                             "VALIDATOR_NOT_QUALIFIED")
            self.assertEqual(req.gates[0]["detail"]["verdict"], "reject")

    def test_02b_sidecar_file_tamper_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [("solo", (100.0, 80.0, -400.0),
                                       EPOCH, None)])
            capture = json.loads(
                (ds / "capture_set.json").read_text(encoding="utf-8"))
            receipt = capture["receipts"][0]
            path = ds / "sidecars" / f"{receipt['sidecar_sha256']}.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["quality"]["visibility_assessment"] = "occluded"
            path.write_text(json.dumps(doc, indent=2) + "\n",
                            encoding="utf-8")
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.gates[0]["gate"],
                             "SIDECAR_HASH_MISMATCH")

    def test_03_needs_review_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [
                ("view-a", (100.0, 80.0, -400.0), EPOCH, None),
                ("view-b", (-100.0, 80.0, -400.0), EPOCH, None),
                ("view-c", (0.0, -120.0, -420.0), EPOCH, None),
                ("view-d", (50.0, 60.0, -410.0), EPOCH,
                 _mutate_needs_review_null_gate),
            ])
            md = tmp / "masks"
            _write_masks(md, ds)
            capture = json.loads(
                (ds / "capture_set.json").read_text(encoding="utf-8"))
            verdicts = {}
            for receipt in capture["receipts"]:
                result = _validate_one_production(ds, receipt)
                verdicts[receipt["image_id"]] = result
            needs = [r for r in capture["receipts"]
                     if verdicts[r["image_id"]]["verdict"]
                     == "needs_review"]
            self.assertEqual(len(needs), 1)
            detail = verdicts[needs[0]["image_id"]]
            self.assertIn("NULL_GATE_VALUE", detail["reviews"])
            self.assertFalse(detail["metric_ready"])
            req = build_mesh_request(ds, md, _config())
            self.assertIsInstance(req, MeshRequest)
            self.assertEqual(req.status, "READY")
            self.assertEqual(len(req.cameras), 3)
            self.assertEqual(req.provenance["qualified_count"], 3)
            kept = {v["image_id"] for v in req.provenance["views"]}
            self.assertNotIn(needs[0]["image_id"], kept)

    def test_04_mixed_epochs_rejected_three_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [
                ("view-a", (100.0, 80.0, -400.0), EPOCH, None),
                ("view-b", (-100.0, 80.0, -400.0), EPOCH, None),
                ("view-c", (0.0, -120.0, -420.0), "epoch-moved-02", None),
            ])
            md = tmp / "masks"
            _write_masks(md, ds)
            with self.assertRaises(MeshProvenanceError) as ctx:
                build_mesh_request(ds, md, _config())
            self.assertIn("MIXED_POSE_EPOCH", str(ctx.exception))

    def test_05_missing_mask_pending_masks_image_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _three_view_dataset(tmp)
            md = tmp / "masks"
            md.mkdir()
            req = build_mesh_request(ds, md, _config())
            self.assertIsInstance(req, PendingMasks)
            self.assertEqual(req.status, "PENDING_MASKS")
            self.assertEqual(len(req.missing), 3)
            self.assertEqual(len(req.qualified), 3)
            for entry in req.missing:
                self.assertTrue(entry["image_id"])
                self.assertTrue(entry["image_sha256"])
                self.assertTrue(entry["evidence_id"])

    def test_06_no_metric_qualified_per_image_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [
                ("view-a", (100.0, 80.0, -400.0), EPOCH,
                 _mutate_wrong_source_image),
                ("view-b", (-100.0, 80.0, -400.0), EPOCH,
                 _mutate_needs_review_null_gate),
            ])
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.status, "PENDING_EVIDENCE")
            self.assertEqual(len(req.gates), 2)
            by_image = {g["image_id"]: g for g in req.gates}
            self.assertEqual(len(by_image), 2)
            verdicts = sorted(g["detail"]["verdict"]
                              for g in req.gates)
            self.assertEqual(verdicts, ["needs_review", "reject"])
            for gate in req.gates:
                self.assertEqual(gate["gate"],
                                 "VALIDATOR_NOT_QUALIFIED")

    def test_07_tampered_image_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [("solo", (100.0, 80.0, -400.0),
                                       EPOCH, None)])
            capture = json.loads(
                (ds / "capture_set.json").read_text(encoding="utf-8"))
            receipt = capture["receipts"][0]
            pkg = ds / "packages" / receipt["package_id"]
            (pkg / "img.jpg").write_bytes(b"tampered-image-bytes")
            prod = _prod()
            with self.assertRaises(ValueError) as ctx:
                prod.resolve_image_bytes(
                    ds, receipt["package_id"], receipt["image_id"],
                    receipt["image_sha256"])
            self.assertIn("re-hash", str(ctx.exception))
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.gates[0]["gate"],
                             "IMAGE_REHASH_MISMATCH")

    def test_08_stale_pose_sample_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [("stale", (100.0, 80.0, -400.0),
                                       EPOCH,
                                       _mutate_stale_interpolated)])
            capture = json.loads(
                (ds / "capture_set.json").read_text(encoding="utf-8"))
            result = _validate_one_production(
                ds, capture["receipts"][0])
            self.assertEqual(result["verdict"], "reject")
            self.assertIn("STALE_SAMPLE", result["reasons"])
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.gates[0]["gate"],
                             "VALIDATOR_NOT_QUALIFIED")
            self.assertIn("STALE_SAMPLE",
                          req.gates[0]["detail"]["reasons"])

    def test_08b_stale_saved_image_noted_as_needs_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _write_dataset(tmp, [("stale", (100.0, 80.0, -400.0),
                                       EPOCH, _mutate_stale_saved)])
            capture = json.loads(
                (ds / "capture_set.json").read_text(encoding="utf-8"))
            result = _validate_one_production(
                ds, capture["receipts"][0])
            self.assertEqual(result["verdict"], "needs_review")
            self.assertIn("STALE_SAMPLE_NOTED", result["reviews"])
            req = build_mesh_request(ds, tmp / "masks", _config())
            self.assertIsInstance(req, PendingEvidence)
            self.assertEqual(req.gates[0]["gate"],
                             "VALIDATOR_NOT_QUALIFIED")

    def test_09_adapter_writes_nothing_to_dataset_or_masks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ds = _three_view_dataset(tmp)
            md = tmp / "masks"
            _write_masks(md, ds)
            before_ds = _snapshot_files(ds)
            before_md = _snapshot_files(md)
            req = build_mesh_request(ds, md, _config())
            self.assertEqual(req.status, "READY")
            self.assertEqual(_snapshot_files(ds), before_ds)
            self.assertEqual(_snapshot_files(md), before_md)

    def test_10_committed_fixtures_labelled_synthetic(self):
        for name in ("profile.synthetic-v1.json",
                     "config.synthetic-v1.json",
                     "mask-meta.synthetic-template.json"):
            doc = json.loads(
                (FIXTURES / name).read_text(encoding="utf-8"))
            self.assertEqual(doc.get("fixture_provenance"), "synthetic")
            note = doc.get("fixture_note", "")
            self.assertIn("SYNTHETIC", note)
        self.assertTrue((FIXTURES / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
