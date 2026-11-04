"""Ingest a staged Receiver package into the Core Capture Evidence Index."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import SCHEMA
from .discover import StagedPackage
from .paths import is_sha256, is_uuid, safe_join, unsafe_rel_path_reason
from .store import ConflictError, EvidenceStore, utc_now


class RejectedPackage(ValueError):
    """Package failed integrity or structure checks; nothing committed."""


@dataclass
class IngestResult:
    package_id: str
    result: str  # complete | duplicate | rejected | conflict
    detail: str
    observation_ids: list[str]
    image_sha256s: list[str]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(package_dir: Path) -> dict[str, Any]:
    path = package_dir / "manifest.json"
    if not path.is_file():
        raise RejectedPackage("staged package lacks manifest.json")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RejectedPackage(f"unreadable manifest.json: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RejectedPackage("manifest.json must be an object")
    if manifest.get("schema_version") != 1:
        raise RejectedPackage(
            f"unsupported manifest schema_version {manifest.get('schema_version')!r}"
        )
    return manifest


def _verify_zip(staged: StagedPackage) -> tuple[str, int]:
    if not staged.zip_path.is_file():
        raise RejectedPackage("package.zip missing")
    digest = sha256_file(staged.zip_path)
    size = staged.zip_path.stat().st_size
    if not staged.zip_sha256 or not is_sha256(staged.zip_sha256):
        raise RejectedPackage("receiver index zip_sha256 missing or malformed")
    if digest != staged.zip_sha256:
        raise RejectedPackage(
            f"package.zip sha256 mismatch: file={digest} index={staged.zip_sha256}"
        )
    if staged.zip_bytes is not None and staged.zip_bytes != size:
        raise RejectedPackage(
            f"package.zip byte length mismatch: file={size} index={staged.zip_bytes}"
        )
    return digest, size


def _observation_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    observations = manifest.get("observations")
    if not isinstance(observations, list) or not observations:
        raise RejectedPackage("manifest has no observations")
    out: dict[str, dict[str, Any]] = {}
    for obs in observations:
        if not isinstance(obs, dict):
            raise RejectedPackage("observation entry must be an object")
        oid = obs.get("observation_id")
        if not is_uuid(oid):
            raise RejectedPackage(f"invalid observation_id {oid!r}")
        sid = obs.get("session_id")
        if not is_uuid(sid):
            raise RejectedPackage(f"invalid session_id on observation {oid}")
        out[oid] = obs
    return out


def _original_files(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise RejectedPackage("manifest has no files[]")
    originals = []
    for entry in files:
        if not isinstance(entry, dict):
            raise RejectedPackage("files[] entry must be an object")
        if entry.get("kind") != "original":
            continue
        originals.append(entry)
    if not originals:
        raise RejectedPackage("manifest has no original files")
    return originals


def _image_meta(obs: dict[str, Any], image_id: str) -> dict[str, Any]:
    images = obs.get("images")
    if not isinstance(images, list):
        raise RejectedPackage(f"observation {obs.get('observation_id')} lacks images")
    for image in images:
        if isinstance(image, dict) and image.get("image_id") == image_id:
            return image
    raise RejectedPackage(
        f"image {image_id} not listed on observation {obs.get('observation_id')}"
    )


def ingest_package(
    store: EvidenceStore,
    staged: StagedPackage,
    *,
    associate_session: str | None = None,
) -> IngestResult:
    """Ingest one staged package. Receiver files are never modified."""
    started = utc_now()
    package_id = staged.package_id

    if associate_session is not None and not is_uuid(associate_session):
        raise ValueError("associate_session must be a UUID")

    if store.package_complete(package_id):
        detail = "package already ingested"
        finished = utc_now()
        store.record_ledger(package_id, started, finished, "duplicate", detail)
        return IngestResult(package_id, "duplicate", detail, [], [])

    if staged.status is not None and staged.status != "valid":
        detail = f"refusing non-valid staged status {staged.status!r}"
        finished = utc_now()
        store.record_ledger(package_id, started, finished, "rejected", detail)
        return IngestResult(package_id, "rejected", detail, [], [])

    partial_dir = store.root / "imports" / package_id / ".partial"
    if partial_dir.exists():
        shutil.rmtree(partial_dir, ignore_errors=True)
    partial_dir.mkdir(parents=True, exist_ok=True)

    try:
        zip_sha, zip_bytes = _verify_zip(staged)
        manifest = _load_manifest(staged.package_dir)
        manifest_pid = manifest.get("package_id")
        if manifest_pid is not None and manifest_pid != package_id:
            raise RejectedPackage(
                f"manifest package_id {manifest_pid!r} != staged {package_id!r}"
            )
        observations = _observation_map(manifest)
        originals = _original_files(manifest)

        prepared_images: list[dict[str, Any]] = []
        prepared_obs: dict[str, dict[str, Any]] = {}

        for entry in originals:
            rel = entry.get("path")
            if not isinstance(rel, str):
                raise RejectedPackage("original file path missing")
            reason = unsafe_rel_path_reason(rel)
            if reason is not None:
                raise RejectedPackage(f"unsafe asset path {rel!r}: {reason}")
            oid = entry.get("observation_id")
            iid = entry.get("image_id")
            if not is_uuid(oid) or not is_uuid(iid):
                raise RejectedPackage("original file needs UUID observation_id/image_id")
            if oid not in observations:
                raise RejectedPackage(f"files[] references unknown observation {oid}")
            declared_sha = entry.get("sha256")
            declared_bytes = entry.get("bytes")
            if not is_sha256(declared_sha):
                raise RejectedPackage(f"invalid files[] sha256 for {iid}")
            if not isinstance(declared_bytes, int) or isinstance(declared_bytes, bool):
                raise RejectedPackage(f"invalid files[] bytes for {iid}")

            asset_path = safe_join(staged.package_dir, rel)
            if not asset_path.is_file():
                raise RejectedPackage(f"missing staged asset {rel}")
            data = asset_path.read_bytes()
            actual_sha = sha256_bytes(data)
            if len(data) != declared_bytes or actual_sha != declared_sha:
                raise RejectedPackage(
                    f"asset integrity failure for {iid}: "
                    f"bytes={len(data)}/{declared_bytes} sha={actual_sha}/{declared_sha}"
                )

            image = _image_meta(observations[oid], iid)
            img_sha = image.get("sha256")
            img_len = image.get("byte_length")
            if img_sha != declared_sha or img_len != declared_bytes:
                raise RejectedPackage(
                    f"image metadata disagrees with files[] for {iid}"
                )

            obs = observations[oid]
            capture_session = obs["session_id"]
            if associate_session is not None:
                core_session = associate_session
                association_basis = "operator_provided"
            else:
                core_session = capture_session
                association_basis = "capture_session"

            prepared_obs[oid] = {
                "observation_id": oid,
                "capture_session_id": capture_session,
                "core_session_id": core_session,
                "captured_at": obs.get("captured_at"),
                "capture_source": obs.get("capture_source"),
                "review_state": obs.get("review_state"),
                "provenance": obs.get("provenance") or {},
                "first_package_id": package_id,
                "ingested_at": started,
                "association_basis": association_basis,
            }
            prepared_images.append(
                {
                    "image_id": iid,
                    "observation_id": oid,
                    "sha256": actual_sha,
                    "byte_length": len(data),
                    "mime": entry.get("mime") or image.get("mime"),
                    "filename": Path(str(image.get("filename") or "image.bin")).name,
                    "role": image.get("role"),
                    "captured_at": image.get("captured_at"),
                    "data": data,
                    "first_package_id": package_id,
                }
            )

        # Persist blobs before the DB commit so a crash leaves re-hashable bytes
        # without a completed package row (retryable, not silently complete).
        for image in prepared_images:
            rel = store.write_blob(image["sha256"], image["data"])
            image["blob_relpath"] = rel
            del image["data"]

        receipt = {
            "schema": SCHEMA,
            "package_id": package_id,
            "zip_sha256": zip_sha,
            "zip_bytes": zip_bytes,
            "staged_at": staged.staged_at,
            "ingested_at": started,
            "observation_ids": sorted(prepared_obs),
            "image_sha256s": sorted({img["sha256"] for img in prepared_images}),
            "associate_session": associate_session,
            "epistemic": {
                "kind": "capture_scene_evidence",
                "not_assembly_memory_observation": True,
                "staged_not_confirmed": True,
            },
        }

        store.begin()
        try:
            receipt_rel = store.write_receipt(package_id, receipt)
            store.insert_package(
                {
                    "package_id": package_id,
                    "zip_sha256": zip_sha,
                    "zip_bytes": zip_bytes,
                    "staged_at": staged.staged_at,
                    "ingested_at": started,
                    "source_path": str(staged.package_dir),
                    "session_ids": sorted(
                        {obs["capture_session_id"] for obs in prepared_obs.values()}
                    ),
                    "observation_ids": sorted(prepared_obs),
                    "receipt_path": receipt_rel,
                }
            )
            for obs in prepared_obs.values():
                store.upsert_observation(obs)
                store.link_package_observation(package_id, obs["observation_id"])
            for image in prepared_images:
                store.upsert_image(image)
            finished = utc_now()
            store.record_ledger(
                package_id,
                started,
                finished,
                "complete",
                f"ingested {len(prepared_obs)} observation(s), "
                f"{len(prepared_images)} image(s)",
            )
            store.commit()
        except Exception:
            store.rollback()
            raise

        shutil.rmtree(partial_dir, ignore_errors=True)
        return IngestResult(
            package_id=package_id,
            result="complete",
            detail=f"ingested {len(prepared_obs)} observation(s)",
            observation_ids=sorted(prepared_obs),
            image_sha256s=sorted({img["sha256"] for img in prepared_images}),
        )
    except ConflictError as exc:
        finished = utc_now()
        store.record_ledger(package_id, started, finished, "conflict", str(exc))
        shutil.rmtree(partial_dir, ignore_errors=True)
        return IngestResult(package_id, "conflict", str(exc), [], [])
    except RejectedPackage as exc:
        finished = utc_now()
        store.record_ledger(package_id, started, finished, "rejected", str(exc))
        shutil.rmtree(partial_dir, ignore_errors=True)
        return IngestResult(package_id, "rejected", str(exc), [], [])
    except Exception as exc:
        finished = utc_now()
        store.record_ledger(package_id, started, finished, "rejected", f"error: {exc}")
        shutil.rmtree(partial_dir, ignore_errors=True)
        raise


def ingest_receiver(
    store: EvidenceStore,
    receiver_data_dir: Path,
    *,
    session_id: str | None = None,
    package_id: str | None = None,
    associate_session: str | None = None,
) -> list[IngestResult]:
    from .discover import list_staged_packages

    staged = list_staged_packages(
        receiver_data_dir, session_id=session_id, package_id=package_id
    )
    return [
        ingest_package(store, item, associate_session=associate_session)
        for item in staged
    ]
