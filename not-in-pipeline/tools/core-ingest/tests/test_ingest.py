"""Core Capture Evidence Index tests. Standard library only.

Run from tools/core-ingest:

    py -3.12 -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anastylos_core_ingest.discover import list_staged_packages
from anastylos_core_ingest.ingest import ingest_package, ingest_receiver, sha256_bytes
from anastylos_core_ingest.store import EvidenceStore

STAMP = "2026-01-15T10:30:00.000Z"


def _jpeg_payload(tag: bytes = b"SYNTH") -> bytes:
    # Minimal JPEG SOI + payload (not a valid decode target; integrity is hash/length).
    return b"\xff\xd8\xff\xe0" + tag + bytes(range(64))


def build_package_bytes(
    *,
    package_id: str | None = None,
    session_id: str | None = None,
    observation_id: str | None = None,
    image_id: str | None = None,
    payload: bytes | None = None,
    corrupt_asset: bool = False,
    filename: str = "front.jpg",
) -> tuple[bytes, dict]:
    package_id = package_id or str(uuid.uuid4())
    session_id = session_id or str(uuid.uuid4())
    observation_id = observation_id or str(uuid.uuid4())
    image_id = image_id or str(uuid.uuid4())
    payload = payload if payload is not None else _jpeg_payload()
    digest = sha256_bytes(payload)
    asset_path = f"assets/{observation_id}/{filename}"
    image = {
        "schema_version": 1,
        "image_id": image_id,
        "observation_id": observation_id,
        "role": "front",
        "filename": filename,
        "mime": "image/jpeg",
        "byte_length": len(payload),
        "sha256": digest,
        "captured_at": STAMP,
        "crop": None,
    }
    observation = {
        "schema_version": 1,
        "observation_id": observation_id,
        "session_id": session_id,
        "component_reference": None,
        "tray_reference": None,
        "captured_at": STAMP,
        "capture_source": "camera",
        "review_state": "unreviewed",
        "operator_notes": None,
        "markings": [],
        "category": {"value": None, "state": "unknown"},
        "measurements": [],
        "quantity": {"value": None, "state": "unknown"},
        "candidates": [],
        "provenance": {
            "app": "anastylos-capture",
            "app_version": "0.1.0",
            "user_agent": "Android/test",
        },
        "export_history": [],
        "images": [image],
    }
    session = {
        "schema_version": 1,
        "session_id": session_id,
        "name": "test",
        "created_at": STAMP,
        "updated_at": STAMP,
        "assembly_memory_session": None,
        "tray_default": None,
    }
    manifest = {
        "schema_version": 1,
        "package_id": package_id,
        "exported_at": STAMP,
        "generator": {"app": "anastylos-capture", "app_version": "0.1.0"},
        "sessions": [session],
        "observations": [observation],
        "files": [
            {
                "path": asset_path,
                "kind": "original",
                "observation_id": observation_id,
                "image_id": image_id,
                "sha256": digest,
                "bytes": len(payload),
                "mime": "image/jpeg",
            }
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr(asset_path, payload if not corrupt_asset else payload + b"X")
    data = buf.getvalue()
    meta = {
        "package_id": package_id,
        "session_id": session_id,
        "observation_id": observation_id,
        "image_id": image_id,
        "sha256": digest,
        "byte_length": len(payload),
        "zip_sha256": sha256_bytes(data),
        "zip_bytes": len(data),
        "payload": payload,
    }
    return data, meta


def stage_receiver_package(receiver_dir: Path, package_bytes: bytes, meta: dict) -> Path:
    """Stage like the Receiver: package.zip + extracted members + index entry."""
    pid = meta["package_id"]
    pkg_dir = receiver_dir / "packages" / pid
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.zip").write_bytes(package_bytes)
    with zipfile.ZipFile(pkg_dir / "package.zip") as zf:
        zf.extractall(pkg_dir)
    report = {
        "status": "valid",
        "package_id": pid,
        "accepted_for_review": [meta["observation_id"]],
        "observations": [{"observation_id": meta["observation_id"], "accepted": True}],
    }
    (pkg_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    index_path = receiver_dir / "index.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index = {"packages": {}}
    index.setdefault("packages", {})[pid] = {
        "staged_at": STAMP,
        "zip_sha256": meta["zip_sha256"],
        "zip_bytes": meta["zip_bytes"],
        "status": "valid",
        "accepted_for_review": [meta["observation_id"]],
        "observation_ids": [meta["observation_id"]],
        "session_ids": [meta["session_id"]],
        "duplicate_deliveries": 0,
        "last_seen_at": STAMP,
    }
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return pkg_dir


class IngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="core-ingest-"))
        self.receiver = self.tmp / "receiver"
        self.core = self.tmp / "core"
        self.receiver.mkdir()
        (self.receiver / "packages").mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ingest_lookup_replay_and_restart(self) -> None:
        data, meta = build_package_bytes()
        stage_receiver_package(self.receiver, data, meta)
        associate = str(uuid.uuid4())

        with EvidenceStore(self.core) as store:
            results = ingest_receiver(
                store,
                self.receiver,
                session_id=meta["session_id"],
                associate_session=associate,
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].result, "complete")
            self.assertEqual(results[0].image_sha256s, [meta["sha256"]])

            found = store.lookup_session(associate)
            self.assertEqual(len(found), 1)
            obs = found[0]["observation"]
            self.assertEqual(obs["observation_id"], meta["observation_id"])
            self.assertEqual(obs["capture_session_id"], meta["session_id"])
            self.assertEqual(obs["core_session_id"], associate)
            self.assertEqual(obs["association_basis"], "operator_provided")
            self.assertEqual(found[0]["images"][0]["sha256"], meta["sha256"])
            blob = store.blob_bytes(meta["sha256"])
            self.assertEqual(hashlib.sha256(blob).hexdigest(), meta["sha256"])
            self.assertEqual(blob, meta["payload"])

            # Replay: still one logical observation / package.
            again = ingest_receiver(store, self.receiver, session_id=meta["session_id"])
            self.assertEqual(again[0].result, "duplicate")
            self.assertEqual(store.status()["observations"], 1)
            self.assertEqual(store.status()["packages"], 1)

        # Restart: new process/handle, evidence still retrievable.
        with EvidenceStore(self.core) as store2:
            self.assertEqual(store2.status()["observations"], 1)
            found = store2.lookup_session(associate)
            self.assertEqual(len(found), 1)
            self.assertEqual(
                store2.blob_bytes(meta["sha256"]),
                meta["payload"],
            )
            ledger = store2.ledger_tail(5)
            self.assertTrue(any(row["result"] == "complete" for row in ledger))
            self.assertTrue(any(row["result"] == "duplicate" for row in ledger))

    def test_reject_corrupted_asset_bytes(self) -> None:
        data, meta = build_package_bytes()
        stage_receiver_package(self.receiver, data, meta)
        # Tamper with staged original after staging (simulates bit-rot / mismatch).
        asset = next((self.receiver / "packages" / meta["package_id"] / "assets").rglob("*.jpg"))
        asset.write_bytes(asset.read_bytes() + b"TAMPER")

        with EvidenceStore(self.core) as store:
            result = ingest_receiver(store, self.receiver, package_id=meta["package_id"])[0]
            self.assertEqual(result.result, "rejected")
            self.assertIn("integrity failure", result.detail)
            self.assertEqual(store.status()["packages"], 0)
            self.assertEqual(store.status()["observations"], 0)
            blob_files = [
                path
                for path in (self.core / "blobs").rglob("*")
                if path.is_file()
            ] if (self.core / "blobs").exists() else []
            self.assertEqual(blob_files, [])

    def test_reject_zip_sha_mismatch(self) -> None:
        data, meta = build_package_bytes()
        stage_receiver_package(self.receiver, data, meta)
        index = json.loads((self.receiver / "index.json").read_text(encoding="utf-8"))
        index["packages"][meta["package_id"]]["zip_sha256"] = "0" * 64
        (self.receiver / "index.json").write_text(
            json.dumps(index, indent=2) + "\n", encoding="utf-8"
        )
        with EvidenceStore(self.core) as store:
            result = ingest_receiver(store, self.receiver, package_id=meta["package_id"])[0]
            self.assertEqual(result.result, "rejected")
            self.assertIn("package.zip sha256 mismatch", result.detail)

    def test_duplicate_observation_across_packages(self) -> None:
        session_id = str(uuid.uuid4())
        observation_id = str(uuid.uuid4())
        image_id = str(uuid.uuid4())
        payload = _jpeg_payload(b"SAME")
        data1, meta1 = build_package_bytes(
            session_id=session_id,
            observation_id=observation_id,
            image_id=image_id,
            payload=payload,
        )
        data2, meta2 = build_package_bytes(
            session_id=session_id,
            observation_id=observation_id,
            image_id=image_id,
            payload=payload,
        )
        self.assertNotEqual(meta1["package_id"], meta2["package_id"])
        stage_receiver_package(self.receiver, data1, meta1)
        stage_receiver_package(self.receiver, data2, meta2)

        with EvidenceStore(self.core) as store:
            results = ingest_receiver(store, self.receiver, session_id=session_id)
            self.assertEqual({r.result for r in results}, {"complete"})
            self.assertEqual(store.status()["packages"], 2)
            self.assertEqual(store.status()["observations"], 1)
            self.assertEqual(store.status()["images"], 1)
            found = store.lookup_session(session_id)
            self.assertEqual(len(found), 1)

    def test_conflict_on_observation_hash_disagreement(self) -> None:
        session_id = str(uuid.uuid4())
        observation_id = str(uuid.uuid4())
        image_id = str(uuid.uuid4())
        data1, meta1 = build_package_bytes(
            session_id=session_id,
            observation_id=observation_id,
            image_id=image_id,
            payload=_jpeg_payload(b"A"),
        )
        data2, meta2 = build_package_bytes(
            session_id=session_id,
            observation_id=observation_id,
            image_id=image_id,
            payload=_jpeg_payload(b"B"),
        )
        stage_receiver_package(self.receiver, data1, meta1)
        stage_receiver_package(self.receiver, data2, meta2)
        with EvidenceStore(self.core) as store:
            first = ingest_receiver(store, self.receiver, package_id=meta1["package_id"])[0]
            self.assertEqual(first.result, "complete")
            second = ingest_receiver(store, self.receiver, package_id=meta2["package_id"])[0]
            self.assertEqual(second.result, "conflict")
            self.assertEqual(store.status()["packages"], 1)
            self.assertEqual(store.status()["observations"], 1)
            self.assertEqual(
                store.get_observation(observation_id)["images"][0]["sha256"],
                meta1["sha256"],
            )

    def test_out_of_order_session_filter(self) -> None:
        sid_a = str(uuid.uuid4())
        sid_b = str(uuid.uuid4())
        data_a, meta_a = build_package_bytes(session_id=sid_a)
        data_b, meta_b = build_package_bytes(session_id=sid_b)
        stage_receiver_package(self.receiver, data_b, meta_b)
        stage_receiver_package(self.receiver, data_a, meta_a)
        visible = list_staged_packages(self.receiver, session_id=sid_a)
        self.assertEqual([p.package_id for p in visible], [meta_a["package_id"]])
        with EvidenceStore(self.core) as store:
            results = ingest_receiver(store, self.receiver, session_id=sid_a)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].package_id, meta_a["package_id"])

    def test_does_not_write_assembly_memory(self) -> None:
        data, meta = build_package_bytes()
        stage_receiver_package(self.receiver, data, meta)
        memory = self.tmp / "session" / "memory.jsonl"
        memory.parent.mkdir()
        memory.write_text("", encoding="utf-8")
        with EvidenceStore(self.core) as store:
            ingest_receiver(store, self.receiver, package_id=meta["package_id"])
        self.assertEqual(memory.read_text(encoding="utf-8"), "")
        # No fabricated removal fields in the Core record.
        with EvidenceStore(self.core) as store:
            row = store.get_observation(meta["observation_id"])
            text = json.dumps(row)
            for banned in ("bbox_mm", "centroid_mm", "part_id", "tray_link"):
                self.assertNotIn(banned, text)


class RealXiaomiReceiverTests(unittest.TestCase):
    """Optional REAL gate against workstation Receiver staging."""

    RECEIVER = Path.home() / "AppData" / "Local" / "Anastylos" / "receiver"
    # Known Xiaomi package from local staging (EXIF Make=Xiaomi).
    PACKAGE_ID = "01bc62df-aa94-3d1f-a749-c50a6c94b89e"
    SESSION_ID = "3638448a-6465-4a70-a7ca-51e5b23adc4b"
    OBSERVATION_ID = "1f267e31-5674-4759-bc50-74b6f1d4221d"
    IMAGE_SHA256 = (
        "08e4ced3c89a0b9d157857b8dfc6c5a960decd37092b8f9a6921fde90e5851f5"
    )

    def test_real_xiaomi_package_round_trip(self) -> None:
        pkg = self.RECEIVER / "packages" / self.PACKAGE_ID
        if not (self.RECEIVER / "index.json").is_file() or not pkg.is_dir():
            self.skipTest("Receiver staging with Xiaomi package not present")
        tmp = Path(tempfile.mkdtemp(prefix="core-ingest-real-"))
        try:
            core = tmp / "core"
            with EvidenceStore(core) as store:
                results = ingest_receiver(
                    store,
                    self.RECEIVER,
                    package_id=self.PACKAGE_ID,
                    associate_session=self.SESSION_ID,
                )
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].result, "complete")
                self.assertIn(self.IMAGE_SHA256, results[0].image_sha256s)
                blob = store.blob_bytes(self.IMAGE_SHA256)
                self.assertEqual(hashlib.sha256(blob).hexdigest(), self.IMAGE_SHA256)
                self.assertIn(b"Xiaomi", blob[:65536])
                found = store.lookup_session(self.SESSION_ID)
                self.assertTrue(
                    any(
                        row["observation"]["observation_id"] == self.OBSERVATION_ID
                        for row in found
                    )
                )
                replay = ingest_receiver(
                    store, self.RECEIVER, package_id=self.PACKAGE_ID
                )[0]
                self.assertEqual(replay.result, "duplicate")
                self.assertEqual(store.status()["observations"], 1)
            with EvidenceStore(core) as store2:
                self.assertEqual(
                    hashlib.sha256(store2.blob_bytes(self.IMAGE_SHA256)).hexdigest(),
                    self.IMAGE_SHA256,
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
