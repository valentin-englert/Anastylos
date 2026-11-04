"""Read-only SpatialDataset -> mesh request adapter (experimental).

Builds a mesh :class:`MeshRequest` (calibrated cameras + operator masks +
voxel grid) from a sealed Core capture set plus staged Spatial bindings,
reusing the PRODUCTION validators for every evidence gate. This module
contains no duplicate validation logic: each gate calls the production
function listed below and maps its verdict to an explicit code.

Dataset layout (``dataset_dir``, mirrors the Core receiver ``data_dir``
conventions consumed by the production seam)::

    dataset_dir/
      capture_set.json                  sealed Core capture set (carries
                                        manifest_sha256; verified with the
                                        production canonical hash)
      sidecars/<sidecar_sha256>.json    one sidecar per receipt (canonical
                                        hash pinned by the receipt)
      packages/<package_id>/
        manifest.json                   staged package manifest (files[] with
                                        image_id + confined relative path)
        <image bytes>                   staged image bytes (re-hashed)
      spatial/
        profiles/<profile_sha256>.json  REQUIRED (raw-bytes pinned)
        layouts/<layout_sha256>.json    optional (validator notes when absent)
        boards/<board_sha256>.json      optional (adapter-defined extension of
                                        the production pinning convention)
        epochs.json                     optional, NO hash pin (production gap)

Mask layout (``mask_dir``): per qualified image ``<image_sha256>.png`` plus
``<image_sha256>.json`` with exactly::

    {"mask_sha256": hex64, "provenance": "manual"|"auto",
     "editor": str, "edited_bool": bool}

``config`` schema::

    {"grid": {"bounds_mm": [[xmin,xmax],[ymin,ymax],[zmin,zmax]],
              "voxel_size_mm": float},
     "carve": {"min_observed": int, "min_views": int,
               "min_baseline_deg": float}}   # optional, mesh defaults

Production functions reused (read-only imports; repo root is resolved
from this file, ``parents[3]``):

- ``calibration/spatial/anastylos_spatial/validator.py:80``
  ``load_json_strict`` (sidecar parsing, rejects NaN/Infinity)
- ``validator.py:814`` ``validate_sidecar`` (verdicts, metric_ready,
  T_board_camera checks, exposure/pose association, epoch join; invokes
  the profile binding below when a profile is staged)
- ``calibration/spatial/anastylos_spatial/profiles.py:271``
  ``check_profile_binding`` (via ``validate_sidecar``)
- ``profiles.py:204`` ``validate_board_instance`` (staged board records)
- ``capture-companion/receiver/anastylos_validator_seam.py:90``
  ``_resolve_image_bytes`` (staged bytes re-hash vs receipt)
- ``anastylos_validator_seam.py:174`` ``build_ctx`` (image + staged
  layout/profile/epochs ctx assembly)
- ``anastylos_validator_seam.py:126`` ``_load_hash_pinned_json`` (via
  ``build_ctx``; directly for the adapter-defined boards/ subdir)
- ``capture-companion/receiver/anastylos_control_journal.py:141``
  ``canonical_bytes`` / ``:151`` ``body_sha256`` (sealed-manifest and
  sidecar canonical-hash verification)
- mesh production: ``Camera.validate``, ``Mask.validate``,
  ``masks.preprocess``, ``carve.check_pose_epoch_consistency``,
  ``carve.VoxelGrid`` (grid caps), ``export`` manifest writers (CLI).

Production-API gaps and adapter assumptions (every one recorded in the
result provenance ``assumptions`` list):

1. ``_resolve_image_bytes`` / ``_load_hash_pinned_json`` are private
   (underscore) seam functions; the adapter depends on their exact
   behavior (confinement under ``packages/``, raw-bytes re-hash).
2. The sealed ``capture_set.json`` FILE layout is adapter-defined: the
   journal seals capture sets into sqlite (``control_journal.py``
   ``finalize``); the adapter expects the same dict exported verbatim
   (``manifest_sha256`` recomputed with production ``canonical_bytes``).
3. ``package_sha256`` cannot be re-verified from a staged directory
   (no production verifier maps it to staged bytes); image bytes ARE
   re-verified, which is what the mesh consumes.
4. ``epochs.json`` carries no hash pin (custody-owned Wave-2 work per
   the seam docstring); the adapter records its raw sha256 for
   traceability and trusts store content, exactly like production.
5. ``spatial/boards/`` has no production sidecar-binding check and no
   production staging subdir; the adapter pins staged board bytes with
   the production loader and validates internal consistency with
   production ``validate_board_instance``. Absent boards are a note,
   not a gate.
6. The production validator silently SKIPS profile binding when no
   profile is staged (``validator.py:849`` has no else-branch); the
   adapter requires a staged profile (no K exists otherwise) and
   reports ``PROFILE_UNSTAGED`` as an explicit missing gate.
7. No registration transform exists anywhere (mesh ``carve`` rejects
   mixed epochs); mixed ``object_pose_epoch_ref`` raises
   ``MIXED_POSE_EPOCH`` via production ``check_pose_epoch_consistency``.
8. The mask sidecar JSON shape (``mask_sha256``/``provenance``/
   ``editor``/``edited_bool``) is adapter-defined; no production mask
   schema exists. ``provenance`` must be ``manual`` or ``auto``
   (``synthetic`` is rejected: real evidence only).
9. ``mesh-evidence-v0`` carries ONE ``mask_provenance`` label, so mixed
   manual+auto sets raise ``MIXED_MASK_PROVENANCE`` instead of being
   silently collapsed (per-view truth would survive in ``extra`` but
   the top-level label would misattribute evidence).
10. The ROI grid has no production estimator; ``config["grid"]`` is
    mandatory and passed to production ``VoxelGrid`` unchanged.
11. Receipt<->sidecar id cross-checks (request/revision/attempt/image
    binding) are adapter-added; the production seam only compares
    hashes, never sidecar id fields.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from anastylos_mesh.cameras import Camera
from anastylos_mesh.carve import VoxelGrid, check_pose_epoch_consistency
from anastylos_mesh.geometry import (
    MeshCalibrationError,
    MeshCoverageError,
    MeshProvenanceError,
)
from anastylos_mesh.masks import Mask, preprocess

__all__ = [
    "ASSUMPTIONS",
    "MeshRequest",
    "PendingEvidence",
    "PendingMasks",
    "build_mesh_request",
]

STATUS_READY = "READY"
STATUS_PENDING_EVIDENCE = "PENDING_EVIDENCE"
STATUS_PENDING_MASKS = "PENDING_MASKS"

ASSUMPTIONS = (
    "seam-private-resolver: image identity via production "
    "anastylos_validator_seam._resolve_image_bytes (private API)",
    "capture-set-file-layout: sealed capture_set.json export verified with "
    "production canonical_bytes (journal seals into sqlite)",
    "package-sha-unverifiable: package_sha256 recorded, not re-verified "
    "(no production staged-dir verifier); image bytes re-verified",
    "epochs-unpinned: spatial/epochs.json trusted store content, raw sha256 "
    "recorded (custody-owned Wave-2 per seam docstring)",
    "boards-adapter-staged: spatial/boards/ pinned with the production "
    "loader, validated with production validate_board_instance; absent "
    "boards are a note, not a gate",
    "profile-required: staged profile mandatory (production validator "
    "skips binding silently when unstaged); K/dist come from the ctx "
    "profile object the validator checked",
    "no-registration: mixed object_pose_epoch_ref rejects via production "
    "check_pose_epoch_consistency",
    "mask-sidecar-adapter-shape: {mask_sha256, provenance, editor, "
    "edited_bool}; provenance manual|auto only",
    "mask-provenance-uniform: mixed manual+auto sets raise instead of "
    "collapsing the single mesh-evidence-v0 label",
    "grid-config-mandatory: no production ROI estimator; config grid "
    "passed to production VoxelGrid unchanged",
    "receipt-sidecar-ids: request/revision/attempt/image id cross-checks "
    "are adapter-added (production compares hashes only)",
)

_PRODUCTION = None


def _repo_root() -> Path:
    """Repo root for read-only production imports."""
    return Path(__file__).resolve().parents[3]


def _load_production():
    """Import production validators read-only (cached)."""
    global _PRODUCTION
    if _PRODUCTION is not None:
        return _PRODUCTION
    root = _repo_root()
    spatial_dir = root / "calibration" / "spatial"
    receiver_dir = root / "capture-companion" / "receiver"
    for probe in (
        spatial_dir / "anastylos_spatial" / "validator.py",
        spatial_dir / "anastylos_spatial" / "profiles.py",
        receiver_dir / "anastylos_validator_seam.py",
        receiver_dir / "anastylos_control_journal.py",
    ):
        if not probe.is_file():
            raise MeshProvenanceError(
                f"PRODUCTION_SEAM_UNAVAILABLE:{probe.relative_to(root)}"
            )
    if str(spatial_dir) not in sys.path:
        sys.path.insert(0, str(spatial_dir))
    try:
        from anastylos_spatial import validator as validator_mod
        from anastylos_spatial import profiles as profiles_mod
    except ImportError as exc:
        raise MeshProvenanceError(
            f"PRODUCTION_SEAM_UNAVAILABLE:anastylos_spatial:{exc}"
        ) from exc

    def _load_standalone(name: str):
        path = receiver_dir / f"{name}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    try:
        seam = _load_standalone("anastylos_validator_seam")
        journal = _load_standalone("anastylos_control_journal")
    except (ImportError, OSError) as exc:
        raise MeshProvenanceError(
            f"PRODUCTION_SEAM_UNAVAILABLE:receiver:{exc}"
        ) from exc

    class _Prod:
        pass

    prod = _Prod()
    prod.repo_root = root
    prod.spatial_dir = spatial_dir
    prod.receiver_dir = receiver_dir
    prod.load_json_strict = validator_mod.load_json_strict
    prod.validate_sidecar = validator_mod.validate_sidecar
    prod.validate_board_instance = profiles_mod.validate_board_instance
    prod.resolve_image_bytes = seam._resolve_image_bytes
    prod.build_ctx = seam.build_ctx
    prod.load_hash_pinned_json = seam._load_hash_pinned_json
    prod.canonical_bytes = journal.canonical_bytes
    prod.body_sha256 = journal.body_sha256
    prod.imports = {
        "validator": "calibration/spatial/anastylos_spatial/validator.py",
        "profiles": "calibration/spatial/anastylos_spatial/profiles.py",
        "seam": "capture-companion/receiver/anastylos_validator_seam.py",
        "journal": "capture-companion/receiver/anastylos_control_journal.py",
    }
    _PRODUCTION = prod
    return prod


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_hex64(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower() and all(
        c in "0123456789abcdef" for c in value
    )


# ---------------------------------------------------------------------------
# Result types.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeshRequest:
    """READY request: validated cameras + masks + grid + provenance."""

    status: str = STATUS_READY
    cameras: tuple = ()
    masks: tuple = ()
    grid: VoxelGrid | None = None
    carve_params: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """JSON-able summary (cameras/masks/grids stay in-memory)."""
        grid = self.grid
        return {
            "status": self.status,
            "view_count": len(self.cameras),
            "grid": (
                {
                    "bounds_mm": [list(a) for a in grid.bounds_mm],
                    "voxel_size_mm": float(grid.voxel_size),
                    "dims": list(grid.dims),
                }
                if grid is not None
                else None
            ),
            "carve_params": dict(self.carve_params),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class PendingEvidence:
    """No metric-qualified real image; per-image missing gates, no guesses."""

    status: str = STATUS_PENDING_EVIDENCE
    gates: tuple = ()
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "gates": list(self.gates),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class PendingMasks:
    """Qualified views exist but operator masks are missing for some."""

    status: str = STATUS_PENDING_MASKS
    missing: tuple = ()
    qualified: tuple = ()
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "missing": list(self.missing),
            "qualified": list(self.qualified),
            "provenance": self.provenance,
        }


# ---------------------------------------------------------------------------
# Sealed capture set.
# ---------------------------------------------------------------------------


def _load_sealed_capture_set(dataset_dir: Path, prod):
    """Load capture_set.json; verify the seal with production canonical_bytes."""
    path = dataset_dir / "capture_set.json"
    if not path.is_file():
        raise MeshProvenanceError("SEALED_MANIFEST_MISSING:capture_set.json")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MeshProvenanceError(
            f"SEALED_MANIFEST_MALFORMED:{exc}"
        ) from exc
    if not isinstance(doc, dict):
        raise MeshProvenanceError("SEALED_MANIFEST_MALFORMED:not-an-object")
    claimed = doc.get("manifest_sha256")
    if not _is_hex64(claimed):
        raise MeshProvenanceError("SEALED_MANIFEST_MALFORMED:bad-manifest-sha")
    head = dict(doc)
    head.pop("manifest_sha256", None)
    try:
        recomputed = prod.body_sha256(head)
    except Exception as exc:
        raise MeshProvenanceError(
            f"SEALED_MANIFEST_MALFORMED:canonical-bytes:{exc}"
        ) from exc
    if recomputed != claimed:
        raise MeshProvenanceError("SEALED_MANIFEST_HASH_MISMATCH")
    if not isinstance(doc.get("receipts"), list):
        raise MeshProvenanceError("SEALED_MANIFEST_MALFORMED:bad-receipts")
    return doc, claimed


def _receipt_sort_key(receipt: dict) -> tuple:
    return (
        str(receipt.get("request_id") or ""),
        int(receipt.get("request_revision") or 0),
        str(receipt.get("attempt_id") or ""),
        str(receipt.get("receipt_id") or ""),
    )


# ---------------------------------------------------------------------------
# Per-image pipeline.
# ---------------------------------------------------------------------------


def _gate(receipt: dict, code: str, detail: dict | None = None) -> dict:
    entry = {
        "image_id": receipt.get("image_id"),
        "image_sha256": receipt.get("image_sha256"),
        "request_id": receipt.get("request_id"),
        "request_revision": receipt.get("request_revision"),
        "attempt_id": receipt.get("attempt_id"),
        "receipt_id": receipt.get("receipt_id"),
        "gate": code,
    }
    if detail:
        entry["detail"] = detail
    return entry


def _process_receipt(receipt: dict, dataset_dir: Path, prod, notes: list):
    """Qualify one receipt; returns (view | None, gate | None)."""
    required = (
        "receipt_id", "package_id", "image_id", "image_sha256",
        "request_id", "request_revision", "attempt_id", "status",
    )
    if not isinstance(receipt, dict) or any(
        receipt.get(k) is None for k in required
    ):
        bad = receipt if isinstance(receipt, dict) else {}
        return None, _gate(bad, "RECEIPT_MALFORMED")
    if receipt.get("status") != "qualified":
        return None, _gate(receipt, "RECEIPT_NOT_QUALIFIED",
                           {"status": receipt.get("status")})
    sidecar_sha = receipt.get("sidecar_sha256")
    if not _is_hex64(sidecar_sha):
        return None, _gate(receipt, "NO_SIDECAR")

    sidecar_path = dataset_dir / "sidecars" / f"{sidecar_sha}.json"
    if not sidecar_path.is_file():
        return None, _gate(receipt, "SIDECAR_FILE_MISSING")
    try:
        sidecar, _raw_sha = prod.load_json_strict(sidecar_path)
    except (OSError, ValueError) as exc:
        return None, _gate(receipt, "SIDECAR_MALFORMED",
                           {"error": f"{type(exc).__name__}"})
    try:
        canonical = prod.body_sha256(sidecar)
    except Exception:
        return None, _gate(receipt, "SIDECAR_MALFORMED",
                           {"error": "canonical-bytes"})
    if canonical != sidecar_sha:
        return None, _gate(receipt, "SIDECAR_HASH_MISMATCH")

    asset = sidecar.get("asset") if isinstance(sidecar, dict) else None
    bindings_ok = (
        isinstance(sidecar, dict)
        and sidecar.get("request_id") == receipt.get("request_id")
        and sidecar.get("request_revision") == receipt.get("request_revision")
        and sidecar.get("attempt_id") == receipt.get("attempt_id")
        and isinstance(asset, dict)
        and asset.get("image_id") == receipt.get("image_id")
        and asset.get("package_id") == receipt.get("package_id")
        and asset.get("sha256") == receipt.get("image_sha256")
    )
    if not bindings_ok:
        return None, _gate(receipt, "RECEIPT_SIDECAR_BINDING_MISMATCH")

    try:
        prod.resolve_image_bytes(
            dataset_dir, receipt["package_id"], receipt["image_id"],
            receipt["image_sha256"],
        )
    except ValueError as exc:
        if "re-hash" in str(exc):
            return None, _gate(receipt, "IMAGE_REHASH_MISMATCH")
        return None, _gate(receipt, "IMAGE_UNRESOLVED",
                           {"error": "ValueError"})
    except (OSError, TypeError):
        return None, _gate(receipt, "IMAGE_UNRESOLVED")

    request = {
        "receipt_id": receipt.get("receipt_id"),
        "package_sha256": receipt.get("package_sha256"),
        "image_sha256": receipt.get("image_sha256"),
        "sidecar_sha256": sidecar_sha,
        "package_id": receipt.get("package_id"),
        "image_id": receipt.get("image_id"),
        "sidecar": sidecar,
    }
    try:
        ctx = prod.build_ctx(request, dataset_dir)
    except Exception as exc:
        return None, _gate(
            receipt, "STAGED_BINDING_CORRUPT",
            {"error": f"{type(exc).__name__}:{str(exc)[:160]}"},
        )
    if "profile" not in ctx:
        return None, _gate(receipt, "PROFILE_UNSTAGED")
    if "layout" not in ctx:
        notes.append(f"LAYOUT_UNSTAGED:{receipt.get('image_id')}")
    if "epochs" not in ctx:
        notes.append(f"EPOCHS_UNSTAGED:{receipt.get('image_id')}")

    try:
        result = prod.validate_sidecar(sidecar, ctx)
    except Exception as exc:
        return None, _gate(receipt, "VALIDATOR_EXCEPTION",
                           {"error": f"{type(exc).__name__}"})
    verdict = result.get("verdict")
    metric_ready = result.get("metric_ready") is True
    if verdict != "accept" or not metric_ready:
        return None, _gate(
            receipt, "VALIDATOR_NOT_QUALIFIED",
            {
                "evidence_id": sidecar.get("evidence_id"),
                "verdict": verdict,
                "metric_ready": metric_ready,
                "reasons": list(result.get("reasons") or []),
                "reviews": list(result.get("reviews") or []),
                "notes": list(result.get("notes") or []),
            },
        )

    board_note = _check_board_binding(
        receipt, sidecar, dataset_dir, prod, notes
    )
    if board_note is not None:
        return None, board_note

    target = sidecar.get("target") or {}
    epoch = target.get("object_pose_epoch_ref")
    if not isinstance(epoch, str) or not epoch:
        return None, _gate(
            receipt, "OBJECT_EPOCH_MISSING",
            {"evidence_id": sidecar.get("evidence_id")},
        )

    camera = _build_camera(receipt, sidecar, ctx)
    profile = ctx["profile"]
    calibration = sidecar.get("calibration") or {}
    geo = sidecar.get("image_geometry") or {}
    view = {
        "camera": camera,
        "evidence_id": sidecar.get("evidence_id"),
        "request_id": receipt.get("request_id"),
        "request_revision": receipt.get("request_revision"),
        "attempt_id": receipt.get("attempt_id"),
        "receipt_id": receipt.get("receipt_id"),
        "image_id": receipt.get("image_id"),
        "image_sha256": receipt.get("image_sha256"),
        "sidecar_sha256": sidecar_sha,
        "profile_id": calibration.get("profile_id"),
        "profile_sha256": calibration.get("profile_sha256"),
        "board_instance_id": calibration.get("board_instance_id"),
        "board_instance_sha256": calibration.get(
            "board_instance_sha256"
        ),
        "board_frame_id": calibration.get("board_frame_id"),
        "layout_sha256": calibration.get("layout_sha256"),
        "pose_epoch": epoch,
        "width_px": geo.get("width_px"),
        "height_px": geo.get("height_px"),
        "validator": {
            "verdict": verdict,
            "metric_ready": metric_ready,
            "notes": list(result.get("notes") or []),
        },
        "profile_reference_size_px": list(
            profile.get("reference_image_size_px") or []
        ),
    }
    return view, None


def _check_board_binding(receipt, sidecar, dataset_dir, prod, notes):
    """Optional staged board record: pin + production validation.

    Returns a gate dict when the staged record is corrupt or rejected,
    else None (absent boards are a recorded note, never a gate).
    """
    calibration = sidecar.get("calibration") or {}
    declared = calibration.get("board_instance_sha256")
    try:
        board, _sha = prod.load_hash_pinned_json(
            dataset_dir / "spatial", "boards", declared, "board"
        )
    except (OSError, ValueError) as exc:
        return _gate(
            receipt, "BOARD_BINDING_CORRUPT",
            {"error": f"{type(exc).__name__}"},
        )
    if board is None:
        notes.append(f"BOARD_UNSTAGED:{receipt.get('image_id')}")
        return None
    try:
        outcome = prod.validate_board_instance(board)
    except Exception as exc:
        return _gate(receipt, "BOARD_BINDING_CORRUPT",
                     {"error": f"{type(exc).__name__}"})
    if outcome.get("verdict") != "accept":
        return _gate(
            receipt, "BOARD_REJECTED",
            {
                "reasons": list(outcome.get("reasons") or []),
                "reviews": list(outcome.get("reviews") or []),
            },
        )
    if board.get("board_instance_id") != calibration.get(
        "board_instance_id"
    ):
        return _gate(receipt, "BOARD_ID_MISMATCH")
    return None


def _build_camera(receipt, sidecar, ctx) -> Camera:
    """Construct a mesh Camera from validator-checked evidence.

    K/dist/reference size come from the ctx profile object the
    production validator already bound; T/geometry come from the
    accepted sidecar. ``Camera.validate`` re-checks every rule.
    """
    profile = ctx["profile"]
    geo = sidecar.get("image_geometry") or {}
    pose = sidecar.get("pose") or {}
    transform = pose.get("T_board_camera") or {}
    matrix = transform.get("matrix")
    if not isinstance(matrix, list):
        raise MeshCalibrationError("POSE_TRANSFORM_MISSING")
    target = sidecar.get("target") or {}
    camera = Camera(
        K=tuple(profile["K"]),
        dist=tuple(profile["distortion"]),
        width=int(geo["width_px"]),
        height=int(geo["height_px"]),
        T_board_camera=tuple(matrix),
        image_sha256=str(receipt["image_sha256"]),
        pose_epoch=str(target["object_pose_epoch_ref"]),
        reference_image_size_px=tuple(
            profile["reference_image_size_px"]
        ),
        reference_to_image=tuple(geo["reference_to_image"]),
    )
    return camera.validate()


# ---------------------------------------------------------------------------
# Operator masks.
# ---------------------------------------------------------------------------


def _decode_foreground(png_bytes: bytes) -> np.ndarray:
    """Decode mask PNG bytes with PIL; deterministic >=127 binarization."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise MeshProvenanceError(
            "MASK_DECODER_UNAVAILABLE:PIL"
        ) from exc
    try:
        with Image.open(io.BytesIO(bytes(png_bytes))) as img:
            gray = np.asarray(img.convert("L"))
    except Exception as exc:
        raise MeshProvenanceError(
            f"MASK_PNG_UNREADABLE:{type(exc).__name__}"
        ) from exc
    return preprocess(gray)


def _load_mask(mask_dir: Path, view: dict, camera: Camera):
    """Load one operator mask; None when the linked files are absent.

    Returns (mask, mask_provenance_entry). Raises typed errors on
    corrupt (present but invalid) mask evidence.
    """
    sha = view["image_sha256"]
    png_path = mask_dir / f"{sha}.png"
    json_path = mask_dir / f"{sha}.json"
    if not png_path.is_file() or not json_path.is_file():
        return None
    try:
        meta = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MeshProvenanceError(
            f"MASK_SIDECAR_MALFORMED:{type(exc).__name__}"
        ) from exc
    if not isinstance(meta, dict):
        raise MeshProvenanceError("MASK_SIDECAR_MALFORMED:not-an-object")
    mask_sha = meta.get("mask_sha256")
    provenance = meta.get("provenance")
    editor = meta.get("editor")
    edited = meta.get("edited_bool")
    if not _is_hex64(mask_sha):
        raise MeshProvenanceError("MASK_SIDECAR_MALFORMED:bad-mask-sha256")
    if provenance not in ("manual", "auto"):
        raise MeshProvenanceError("MASK_PROVENANCE_NOT_OPERATOR")
    if not isinstance(editor, str) or not editor:
        raise MeshProvenanceError("MASK_SIDECAR_MALFORMED:bad-editor")
    if not isinstance(edited, bool):
        raise MeshProvenanceError("MASK_SIDECAR_MALFORMED:bad-edited-bool")
    try:
        raw = png_path.read_bytes()
    except OSError as exc:
        raise MeshProvenanceError(
            f"MASK_PNG_UNREADABLE:{type(exc).__name__}"
        ) from exc
    foreground = _decode_foreground(raw)
    mask = Mask(
        png_bytes=raw,
        sha256=mask_sha,
        image_sha256=camera.image_sha256,
        provenance=provenance,
        width=camera.width,
        height=camera.height,
        foreground=foreground,
        pose_epoch=camera.pose_epoch,
    )
    mask.validate(camera)
    entry = {
        "image_id": view["image_id"],
        "image_sha256": sha,
        "evidence_id": view["evidence_id"],
        "mask_sha256": mask_sha,
        "provenance": provenance,
        "editor": editor,
        "edited_bool": edited,
    }
    return mask, entry


# ---------------------------------------------------------------------------
# Grid + carve config.
# ---------------------------------------------------------------------------


def _build_grid(config: dict) -> VoxelGrid:
    if not isinstance(config, dict):
        raise MeshCoverageError("BAD_CONFIG:not-an-object")
    grid_cfg = config.get("grid")
    if not isinstance(grid_cfg, dict):
        raise MeshCoverageError("GRID_CONFIG_MISSING")
    bounds = grid_cfg.get("bounds_mm")
    voxel = grid_cfg.get("voxel_size_mm")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 3:
        raise MeshCoverageError("BAD_GRID_BOUNDS")
    try:
        bounds_t = tuple(
            (float(axis[0]), float(axis[1])) for axis in bounds
        )
        voxel_f = float(voxel)
    except (TypeError, ValueError, IndexError) as exc:
        raise MeshCoverageError("BAD_GRID_BOUNDS") from exc
    return VoxelGrid(bounds_mm=bounds_t, voxel_size=voxel_f)


def _carve_params(config: dict) -> dict:
    raw = config.get("carve") or {}
    if not isinstance(raw, dict):
        raise MeshCoverageError("BAD_CARVE_PARAMS:not-an-object")
    try:
        params = {
            "min_observed": int(raw.get("min_observed", 2)),
            "min_views": int(raw.get("min_views", 2)),
            "min_baseline_deg": float(raw.get("min_baseline_deg", 5.0)),
        }
    except (TypeError, ValueError) as exc:
        raise MeshCoverageError("BAD_CARVE_PARAMS") from exc
    if params["min_observed"] < 1 or params["min_views"] < 1:
        raise MeshCoverageError("BAD_CARVE_PARAMS")
    return params


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def _epochs_sha(dataset_dir: Path) -> str | None:
    path = dataset_dir / "spatial" / "epochs.json"
    if not path.is_file():
        return None
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


def _view_summary(view: dict) -> dict:
    return {
        k: view[k]
        for k in (
            "evidence_id", "request_id", "request_revision",
            "attempt_id", "receipt_id", "image_id", "image_sha256",
            "sidecar_sha256", "profile_id", "profile_sha256",
            "board_instance_id", "board_instance_sha256",
            "board_frame_id", "layout_sha256", "pose_epoch",
            "width_px", "height_px", "validator",
            "profile_reference_size_px",
        )
    }


def build_mesh_request(dataset_dir, mask_dir, config):
    """Build a mesh request from sealed Spatial evidence (read-only).

    Returns :class:`MeshRequest` (READY), :class:`PendingEvidence`
    (no metric-qualified real image), or :class:`PendingMasks`
    (qualified views lack operator masks). Raises typed
    ``MeshProvenanceError`` / ``MeshCalibrationError`` /
    ``MeshCoverageError`` with explicit codes on corrupt seals,
    mixed epochs, corrupt masks, or bad grid config.
    """
    prod = _load_production()
    ds = Path(dataset_dir)
    md = Path(mask_dir)
    if not ds.is_dir():
        raise MeshProvenanceError("DATASET_DIR_MISSING")

    capture_set, manifest_sha = _load_sealed_capture_set(ds, prod)
    receipts = sorted(
        (r for r in capture_set["receipts"] if isinstance(r, dict)),
        key=_receipt_sort_key,
    )
    if len(receipts) != len(capture_set["receipts"]):
        raise MeshProvenanceError("SEALED_MANIFEST_MALFORMED:bad-receipt")

    notes: list[str] = []
    views: list[dict] = []
    gates: list[dict] = []
    for receipt in receipts:
        view, gate = _process_receipt(receipt, ds, prod, notes)
        if view is not None:
            views.append(view)
        else:
            gates.append(gate)

    base_provenance = {
        "adapter": "anastylos_mesh.spatial_adapter",
        "capture_set_id": capture_set.get("capture_set_id"),
        "capture_set_revision": capture_set.get("revision"),
        "capture_set_manifest_sha256": manifest_sha,
        "capture_set_status": capture_set.get("status"),
        "session_id": capture_set.get("session_id"),
        "dataset_dir": str(ds),
        "mask_dir": str(md),
        "epochs_sha256": _epochs_sha(ds),
        "receipt_count": len(receipts),
        "qualified_count": len(views),
        "production_imports": dict(prod.imports),
        "assumptions": list(ASSUMPTIONS),
    }

    if not views:
        provenance = dict(base_provenance)
        provenance["notes"] = sorted(set(notes))
        if not receipts:
            gates = [
                {
                    "image_id": None,
                    "image_sha256": None,
                    "request_id": None,
                    "request_revision": None,
                    "attempt_id": None,
                    "receipt_id": None,
                    "gate": "NO_RECEIPTS",
                }
            ]
        return PendingEvidence(
            status=STATUS_PENDING_EVIDENCE,
            gates=tuple(gates),
            provenance=provenance,
        )

    cameras = [v["camera"] for v in views]
    check_pose_epoch_consistency(cameras)

    masks: list[Mask] = []
    mask_entries: list[dict] = []
    missing: list[dict] = []
    for view, camera in zip(views, cameras):
        loaded = _load_mask(md, view, camera)
        if loaded is None:
            missing.append(
                {
                    "image_id": view["image_id"],
                    "image_sha256": view["image_sha256"],
                    "evidence_id": view["evidence_id"],
                }
            )
        else:
            mask, entry = loaded
            masks.append(mask)
            mask_entries.append(entry)

    if missing:
        provenance = dict(base_provenance)
        provenance["views"] = [_view_summary(v) for v in views]
        provenance["mask_entries"] = mask_entries
        provenance["notes"] = sorted(set(notes))
        return PendingMasks(
            status=STATUS_PENDING_MASKS,
            missing=tuple(missing),
            qualified=tuple(_view_summary(v) for v in views),
            provenance=provenance,
        )

    provenances = {m.provenance for m in masks}
    if len(provenances) > 1:
        raise MeshProvenanceError(
            "MIXED_MASK_PROVENANCE:"
            + ",".join(sorted(provenances))
        )

    grid = _build_grid(config)
    carve_cfg = _carve_params(config if isinstance(config, dict) else {})

    provenance = dict(base_provenance)
    provenance["views"] = [_view_summary(v) for v in views]
    provenance["mask_entries"] = mask_entries
    provenance["notes"] = sorted(set(notes))
    return MeshRequest(
        status=STATUS_READY,
        cameras=tuple(cameras),
        masks=tuple(masks),
        grid=grid,
        carve_params=carve_cfg,
        provenance=provenance,
    )
