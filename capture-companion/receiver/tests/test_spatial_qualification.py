"""Spatial <-> Core qualification integration tests (Wave 1.5).

Drives ``ControlJournal.maybe_qualify`` with the REAL seam runner and
the REAL spatial validator over staged package bytes plus frozen
spatial sidecar vectors. Positive and negative paths; temporary
data-dirs only — no synthetic receipts in provider-facing paths.

Run from the receiver directory::

    python -m unittest discover -s tests -v
"""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anastylos_control_journal as journal
import anastylos_validator_seam as seam

RECEIVER_DIR = Path(__file__).resolve().parents[1]
VECTORS = RECEIVER_DIR.parents[0] / "control" / "contracts" / "vectors"
SPATIAL_DIR = RECEIVER_DIR.parents[1] / "calibration" / "spatial"

NOW = datetime(2026, 10, 29, 12, 1, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-10-29T12:01:00Z"
OPEN_EXPIRES = "2026-10-29T12:31:00Z"
CONSENT_EXPIRES = "2026-10-29T12:21:00Z"

SOURCE = "00000000-0000-4000-8000-000000000003"
PRINCIPAL = "adapter:test-host"
IMAGE_BYTES = b"\xff\xd8fake-jpeg-stand-in" * 64  # 1024 bytes


def new_id():
    return str(uuid.uuid4())


def hex64(seed):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def load_vector(name):
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


class QualificationCase(unittest.TestCase):
    """Journal + seam + validator wired together over staged bytes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.journal = journal.ControlJournal(
            self.data_dir / "control.db",
            contract_sha="ec313f7ftest",
            now_fn=lambda: NOW,
            validator_runner=lambda payload: seam.run_validator(
                payload, self.data_dir),
        )
        self.addCleanup(self.journal.close)
        self.valid = load_vector("control-request-valid.json")
        self.spatial_valid = load_vector("spatial-valid.json")

    # -- flow helpers (mirror test_control_pipeline.py) --------------------

    def open_session(self, max_images=20):
        return self.journal.open_session(
            PRINCIPAL,
            {"operation_id": new_id(), "source_id": SOURCE,
             "max_images": max_images, "expires_at": OPEN_EXPIRES},
            SOURCE)

    def arm(self, session):
        return self.journal.consent(
            SOURCE,
            {"operation_id": new_id(), "session_id": session["session_id"],
             "expected_session_revision": session["revision"],
             "action": "arm", "consent_revision": 1,
             "max_images": 20, "expires_at": CONSENT_EXPIRES,
             "camera_id": "camera:0", "allow_auto_upload": True})

    def make_request(self, session_id, revision):
        valid = self.valid
        return self.journal.request_view(
            PRINCIPAL, SOURCE,
            {"operation_id": new_id(), "session_id": session_id,
             "expected_session_revision": revision, "source_id": SOURCE,
             "target_label": valid["target_label"],
             "board_id": valid["board_id"],
             "calibration_id": valid["calibration_id"],
             "target": copy.deepcopy(valid["target"]),
             "quality_profile_id": valid["quality_profile_id"],
             "expires_at": valid["expires_at"], "reason": valid["reason"],
             "board_registration_epoch_id":
                 valid["board_registration_epoch_id"],
             "object_pose_epoch_id": valid["object_pose_epoch_id"],
             "cad_context": valid["cad_context"]})

    def reserve(self, session_id, request_id, request_revision):
        valid = self.valid
        return self.journal.register_attempt(
            SOURCE,
            {"operation_id": new_id(), "session_id": session_id,
             "request_id": request_id, "request_revision": request_revision,
             "attempt_id": new_id(), "consent_revision": 1,
             "board_registration_epoch_id":
                 valid["board_registration_epoch_id"],
             "object_pose_epoch_id": valid["object_pose_epoch_id"]})

    def bind(self, attempt_id, image_sha256, package_sha256):
        return self.journal.bind_asset(
            SOURCE,
            {"operation_id": new_id(), "attempt_id": attempt_id,
             "package_id": new_id(), "observation_id": new_id(),
             "image_id": new_id(), "image_sha256": image_sha256,
             "package_sha256": package_sha256})

    def stage_package(self, package_id, image_id, image_bytes):
        package_dir = self.data_dir / "packages" / package_id
        package_dir.mkdir(parents=True)
        (package_dir / "frame.jpg").write_bytes(image_bytes)
        manifest = {"files": [{"image_id": image_id, "path": "frame.jpg",
                               "mime": "image/jpeg"}]}
        (package_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")

    def full_flow(self, image_bytes=IMAGE_BYTES):
        """Open -> request -> arm -> reserve -> bind -> accepted receipt."""
        session = self.open_session()
        created = self.make_request(session["session_id"], 1)
        self.arm({"session_id": session["session_id"],
                  "revision": created["session_revision"]})
        ack = self.reserve(session["session_id"],
                           created["request"]["request_id"], 1)
        image_sha = hashlib.sha256(image_bytes).hexdigest()
        package_sha = hex64("pkg" + ack["attempt_id"])
        self.bind(ack["attempt_id"], image_sha, package_sha)
        attempt = self.journal._attempt_row(ack["attempt_id"])
        receipt = self.journal.accept_package(
            SOURCE, ack["attempt_id"],
            {"package_id": attempt[9], "package_sha256": attempt[13],
             "observation_id": attempt[10], "image_id": attempt[11],
             "image_sha256": attempt[12]})
        self.stage_package(attempt[9], attempt[11], image_bytes)
        return {"session_id": session["session_id"],
                "request_id": created["request"]["request_id"],
                "attempt_id": ack["attempt_id"],
                "package_id": attempt[9], "observation_id": attempt[10],
                "image_id": attempt[11], "image_sha256": image_sha,
                "receipt": receipt}

    def real_sidecar(self, flow, mutate=None):
        """Frozen valid sidecar re-bound to this flow's real bindings."""
        sidecar = copy.deepcopy(self.spatial_valid)
        sidecar["evidence_id"] = new_id()
        sidecar["session_id"] = flow["session_id"]
        sidecar["source_id"] = SOURCE
        sidecar["request_id"] = flow["request_id"]
        sidecar["request_revision"] = 1
        sidecar["attempt_id"] = flow["attempt_id"]
        sidecar["asset"]["package_id"] = flow["package_id"]
        sidecar["asset"]["observation_id"] = flow["observation_id"]
        sidecar["asset"]["image_id"] = flow["image_id"]
        sidecar["asset"]["sha256"] = flow["image_sha256"]
        sidecar["asset"]["byte_length"] = len(IMAGE_BYTES)
        sidecar["pose"]["image_sha256"] = flow["image_sha256"]
        if mutate:
            mutate(sidecar)
        return {"operation_id": new_id(),
                "expected_sidecar_revision": 0, "sidecar": sidecar}

    def upload(self, flow, mutate=None):
        return self.journal.upload_sidecar(
            SOURCE, None, flow["attempt_id"],
            self.real_sidecar(flow, mutate), "source")

    def receipt_rows(self, attempt_id):
        return self.journal._conn.execute(
            "SELECT receipt_id, status, reason_codes_json FROM receipts"
            " WHERE attempt_id = ? ORDER BY accepted_at, receipt_id",
            (attempt_id,)).fetchall()

    # -- positive path -----------------------------------------------------

    def test_positive_flow_qualifies_with_real_validator(self):
        flow = self.full_flow()
        uploaded = self.upload(flow)
        self.assertEqual(uploaded["status"], "package_only")
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "qualified")
        self.assertEqual(out["reason_codes"], [])
        self.assertEqual(out["image_sha256"], flow["image_sha256"])
        self.assertNotEqual(out["receipt_id"], uploaded["receipt_id"])

    def test_history_rows_immutable_after_qualification(self):
        flow = self.full_flow()
        uploaded = self.upload(flow)
        before = {row[0]: (row[1], row[2])
                  for row in self.receipt_rows(flow["attempt_id"])}
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        after = {row[0]: (row[1], row[2])
                 for row in self.receipt_rows(flow["attempt_id"])}
        for receipt_id, status in before.items():
            self.assertEqual(after[receipt_id], status)
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[out["receipt_id"]][0], "qualified")

    def test_full_chain_bindings_qualify(self):
        """Staged layout + profile + epochs that all verify still qualify."""
        flow = self.full_flow()
        spatial_stage = self.data_dir / "spatial"
        profile_raw = (
            SPATIAL_DIR / "calibration-profile.synthetic-v1.json"
        ).read_bytes()
        profile_sha = hashlib.sha256(profile_raw).hexdigest()
        (spatial_stage / "profiles").mkdir(parents=True)
        (spatial_stage / "profiles" / f"{profile_sha}.json").write_bytes(
            profile_raw)
        layout = {"frames": {"surface_a3": {}},
                  "id_ranges": [{"role": "surface_a3",
                                 "used": [10, 11, 13]}]}
        layout_raw = json.dumps(layout, sort_keys=True).encode("utf-8")
        layout_sha = hashlib.sha256(layout_raw).hexdigest()
        (spatial_stage / "layouts").mkdir(parents=True)
        (spatial_stage / "layouts" / f"{layout_sha}.json").write_bytes(
            layout_raw)
        (spatial_stage / "epochs.json").write_text(json.dumps(
            [{"epoch_id": "e1", "start_utc": "2026-10-29T11:00:00Z",
              "end_utc": "2026-10-29T13:00:00Z"}]), encoding="utf-8")

        def mutate(sidecar):
            sidecar["calibration"]["profile_sha256"] = profile_sha
            sidecar["calibration"]["layout_sha256"] = layout_sha

        uploaded = self.upload(flow, mutate)
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "qualified")

    # -- negative paths: invalid evidence rejects --------------------------

    def test_wrong_source_image_rejects(self):
        flow = self.full_flow()

        def mutate(sidecar):
            sidecar["pose"]["image_sha256"] = "b" * 64

        uploaded = self.upload(flow, mutate)
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "rejected")
        self.assertIn("WRONG_SOURCE_IMAGE", out["reason_codes"])

    def test_stale_interpolated_pose_rejects(self):
        flow = self.full_flow()

        def mutate(sidecar):
            pose = sidecar["pose"]
            pose["solve_source"] = "timestamp_interpolated"
            pose["sample_age_ms"] = 5000
            pose["bracketing_samples"] = [
                {"sample_id": new_id(), "sensor_ns": "999000000"},
                {"sample_id": new_id(), "sensor_ns": "1010000000"}]

        uploaded = self.upload(flow, mutate)
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "rejected")

    def test_epoch_break_overlap_rejects(self):
        flow = self.full_flow()
        spatial_stage = self.data_dir / "spatial"
        spatial_stage.mkdir(parents=True)
        # Exposure is 2026-10-29T12:00:00Z; stage an epoch that ends first.
        (spatial_stage / "epochs.json").write_text(json.dumps(
            [{"epoch_id": "e1", "start_utc": "2026-10-29T11:00:00Z",
              "end_utc": "2026-10-29T11:59:00Z"}]), encoding="utf-8")

        def mutate(sidecar):
            relation = sidecar["observer_relation"]
            relation["association"] = "verified"
            relation["run_ref"] = {"asset_id": new_id(), "sha256": "c" * 64}
            relation["epoch_id"] = "e1"
            relation["time_mapping_ref"] = {"asset_id": new_id(),
                                            "sha256": "d" * 64}

        uploaded = self.upload(flow, mutate)
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "rejected")
        self.assertIn("EPOCH_OVERLAP_ACROSS_BREAK", out["reason_codes"])

    def test_unknown_marker_against_staged_layout_rejects(self):
        flow = self.full_flow()
        spatial_stage = self.data_dir / "spatial"
        # Staged bytes verify and the hash binds, but the layout does not
        # contain marker 13, which the sidecar claims as detected/inlier.
        layout = {"frames": {"surface_a3": {}},
                  "id_ranges": [{"role": "surface_a3", "used": [10, 11]}]}
        layout_raw = json.dumps(layout, sort_keys=True).encode("utf-8")
        (spatial_stage / "layouts").mkdir(parents=True)
        staged_sha = hashlib.sha256(layout_raw).hexdigest()
        (spatial_stage / "layouts" / f"{staged_sha}.json").write_bytes(
            layout_raw)

        def mutate(sidecar):
            sidecar["calibration"]["layout_sha256"] = staged_sha

        uploaded = self.upload(flow, mutate)
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "rejected")
        self.assertIn("UNKNOWN_MARKER_ID", out["reason_codes"])

    # -- negative paths: uncertainty fails closed to package_only ----------

    def test_stale_saved_pose_never_qualifies(self):
        flow = self.full_flow()

        def mutate(sidecar):
            sidecar["pose"]["sample_age_ms"] = 5000

        uploaded = self.upload(flow, mutate)
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "package_only")
        self.assertEqual(out["receipt_id"], uploaded["receipt_id"])

    def test_nominal_calibration_never_qualifies(self):
        flow = self.full_flow()

        def mutate(sidecar):
            sidecar["calibration"]["metric_validity"] = "nominal_only"
            sidecar["quality"]["overall"] = "unknown"
            for gate in ("pose", "motion", "focus", "exposure",
                         "visibility"):
                sidecar["quality"][gate]["status"] = "unknown"

        uploaded = self.upload(flow, mutate)
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertNotEqual(out["status"], "qualified")

    def test_tampered_staged_bytes_never_qualify(self):
        flow = self.full_flow()
        package_dir = self.data_dir / "packages" / flow["package_id"]
        (package_dir / "frame.jpg").write_bytes(b"tampered" * 128)
        uploaded = self.upload(flow)
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "package_only")
        self.assertEqual(out["receipt_id"], uploaded["receipt_id"])

    def test_missing_staged_package_never_qualifies(self):
        flow = self.full_flow()
        manifest = (self.data_dir / "packages" / flow["package_id"]
                    / "manifest.json")
        manifest.unlink()
        uploaded = self.upload(flow)
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "package_only")

    def test_no_sidecar_receipt_never_qualifies(self):
        flow = self.full_flow()
        out = self.journal.maybe_qualify(flow["receipt"]["receipt_id"])
        self.assertEqual(out["status"], "package_only")

    def test_corrupt_staged_profile_fails_closed(self):
        flow = self.full_flow()
        spatial_stage = self.data_dir / "spatial"
        (spatial_stage / "profiles").mkdir(parents=True)
        declared = self.spatial_valid["calibration"]["profile_sha256"]
        (spatial_stage / "profiles" / f"{declared}.json").write_bytes(
            b"{not the pinned bytes}")
        uploaded = self.upload(flow)
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "package_only")

    def test_validator_import_failure_fails_closed(self):
        flow = self.full_flow()
        uploaded = self.upload(flow)
        with mock.patch.object(seam, "_load_validator",
                               side_effect=ImportError("no validator")):
            out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["status"], "package_only")

    def test_old_receipt_never_promoted_after_qualification(self):
        flow = self.full_flow()
        uploaded = self.upload(flow)
        qualified = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(qualified["status"], "qualified")
        # A stale receipt joined against a later sidecar fails closed:
        # returned unchanged, no new version, never qualified.
        before = self.receipt_rows(flow["attempt_id"])
        stale = self.journal.maybe_qualify(flow["receipt"]["receipt_id"])
        self.assertEqual(stale["receipt_id"],
                         flow["receipt"]["receipt_id"])
        self.assertEqual(stale["status"], "package_only")
        self.assertEqual(self.receipt_rows(flow["attempt_id"]), before)
        requal = self.journal.maybe_qualify(qualified["receipt_id"])
        self.assertEqual(requal["receipt_id"], qualified["receipt_id"])
        self.assertEqual(requal["status"], "qualified")
        self.assertEqual(self.receipt_rows(flow["attempt_id"]), before)

    def test_tampered_sidecar_store_never_qualifies(self):
        flow = self.full_flow()
        uploaded = self.upload(flow)
        with self.journal._conn:
            self.journal._conn.execute(
                "UPDATE sidecars SET sidecar_json = ? WHERE attempt_id = ?",
                (json.dumps({"schema": "anastylos-spatial-evidence@1",
                             "tampered": True}), flow["attempt_id"]))
        out = self.journal.maybe_qualify(uploaded["receipt_id"])
        self.assertEqual(out["receipt_id"], uploaded["receipt_id"])
        self.assertEqual(out["status"], "package_only")


class AdaptReportTests(unittest.TestCase):
    def test_accept_without_metric_stays_unavailable(self):
        request = {"receipt_id": "r", "package_sha256": "p",
                   "image_sha256": "i", "sidecar_sha256": "s"}
        out = seam.adapt_report({"verdict": "accept", "reasons": [],
                                 "metric_ready": False}, request)
        self.assertFalse(out["available"])
        self.assertEqual(out["receipt_id"], "r")

    def test_needs_review_stays_unavailable(self):
        request = {"receipt_id": "r", "package_sha256": "p",
                   "image_sha256": "i", "sidecar_sha256": "s"}
        out = seam.adapt_report({"verdict": "needs_review",
                                 "reasons": ["NULL_UNCERTAINTY"],
                                 "metric_ready": False}, request)
        self.assertFalse(out["available"])

    def test_reject_carries_reason_codes(self):
        request = {"receipt_id": "r", "package_sha256": "p",
                   "image_sha256": "i", "sidecar_sha256": "s"}
        out = seam.adapt_report({"verdict": "reject",
                                 "reasons": ["WRONG_SOURCE_IMAGE"],
                                 "metric_ready": False}, request)
        self.assertTrue(out["available"])
        self.assertFalse(out["valid"])
        self.assertEqual(out["reason_codes"], ["WRONG_SOURCE_IMAGE"])
        self.assertEqual(out["image_sha256"], "i")

    def test_build_ctx_rejects_unverifiable_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                seam.build_ctx({"receipt_id": "r", "sidecar": None}, tmp)
            with self.assertRaises((TypeError, ValueError)):
                seam.build_ctx(None, tmp)


if __name__ == "__main__":
    unittest.main()
