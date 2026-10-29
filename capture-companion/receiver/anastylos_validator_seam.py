#!/usr/bin/env python3
"""Spatial-validator seam for receipt qualification. Stdlib only.

CORE owns the delivery receipt and the atomic ``qualified`` promotion;
SPATIAL owns the deterministic validator. This module is the boundary:
:func:`run_validator` runs the approved offline validator over staged
immutable bytes and normalizes its report for
:meth:`ControlJournal.maybe_qualify`.

As-built contract (Wave 1.5 integration; replaces the planned
subprocess design, which SPATIAL never published — there is no
``<tools>/spatial-validator/validate.py``; the validator lives at
``calibration/spatial/anastylos_spatial/validator.py`` and is imported
in-process):

- Input: the journal's request payload ``{"receipt_id",
  "package_sha256", "image_sha256", "sidecar_sha256",
  "package_id", "image_id", "sidecar": dict | None}`` plus the
  receiver ``data_dir`` holding ``control.db`` and ``packages/``.
- Image bytes: resolved exactly like the asset route —
  ``packages/<package_id>/manifest.json`` entry for ``image_id``,
  confined under ``packages/`` — and re-hashed. Bytes that are
  missing, unreadable, or do not re-hash to ``image_sha256`` fail
  closed (unavailable): phone assertions alone never qualify.
- Optional chain bindings resolve from ``data_dir/spatial/``:
  ``layouts/<layout_sha256>.json``, ``profiles/<profile_sha256>.json``,
  ``epochs.json``. Staged layout/profile bytes must re-hash (raw file
  bytes, the spatial CLI convention) to the sidecar-declared hash or
  the store is corrupt and qualification is unavailable. Absent files
  leave those checks skipped (validator notes, never invented).
  ``epochs.json`` has no sidecar hash pin; integrity anchoring for
  epochs is custody-owned Wave-2 work.
- Report mapping: ``accept`` + ``metric_ready`` -> valid (qualified);
  ``reject`` -> invalid (rejected, validator reason codes carried);
  ``needs_review`` or accept-without-metric -> unavailable, so the
  receipt stays ``package_only``. Package acceptance and metric
  usability stay distinct: ``package_only`` means accepted but not
  metric-usable, ``qualified`` means both.
- Any exception, unimportable validator, or malformed input fails
  closed to unavailable. Bounds are structural (256 KiB sidecar cap,
  staged-file size cap) rather than wall-clock: the validator is pure
  stdlib over small inputs and performs no I/O or network calls.

Validator notes/reviews on the unavailable path are returned in the
``reason`` string but not persisted by the journal; persisting
validator notes is Wave-2 work (no receipt column exists yet).
Decisions are reproducible by re-running over the stored sidecar.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_STAGED_FILE_MAX_BYTES = 4 * 1024 * 1024


def _repo_spatial_dir():
    """Directory to import the spatial package from (this checkout)."""
    return Path(__file__).resolve().parents[2] / "calibration" / "spatial"


def _load_validator():
    """Import ``validate_sidecar``; raise ImportError when undelivered."""
    spatial_dir = _repo_spatial_dir()
    if str(spatial_dir) not in sys.path:
        sys.path.insert(0, str(spatial_dir))
    from anastylos_spatial.validator import validate_sidecar

    return validate_sidecar


def _unavailable(request, reason):
    receipt_id = None
    if isinstance(request, dict):
        receipt_id = request.get("receipt_id")
    return {"available": False, "reason": reason, "receipt_id": receipt_id}


def _read_staged_file(path):
    """Read a staged file with a size cap; raise OSError/ValueError."""
    raw = path.read_bytes()
    if len(raw) > _STAGED_FILE_MAX_BYTES:
        raise ValueError(f"staged file too large: {path.name}")
    return raw


def _resolve_image_bytes(data_dir, package_id, image_id, image_sha256):
    """Re-read staged image bytes and verify them against the receipt hash.

    Returns ``(sha256, byte_length)``. Raises ``OSError``/``ValueError``
    when the package, manifest, entry, or bytes cannot be verified.
    """
    packages_dir = Path(data_dir) / "packages"
    package_dir = packages_dir / str(package_id)
    try:
        manifest = json.loads(
            (package_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OSError(f"unreadable staged manifest: {exc}") from exc
    target = None
    for entry in manifest.get("files", []) or []:
        if (isinstance(entry, dict)
                and entry.get("image_id") == image_id
                and isinstance(entry.get("path"), str)):
            target = entry
            break
    if target is None:
        raise OSError("image_id not present in staged manifest")
    try:
        resolved = (package_dir / target["path"]).resolve()
        base = packages_dir.resolve()
    except OSError as exc:
        raise OSError(f"unstageable asset path: {exc}") from exc
    if base not in resolved.parents or not resolved.is_file():
        raise OSError("staged asset escapes packages dir or is not a file")
    data = resolved.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != image_sha256:
        raise ValueError("staged bytes do not re-hash to receipt image_sha256")
    return digest, len(data)


def _load_hash_pinned_json(staging_dir, subdir, declared_sha256, label):
    """Load an optional hash-pinned staging file.

    Returns ``(document, declared_sha256)`` or ``(None, None)`` when no
    file is staged. Raises ``OSError``/``ValueError`` when a staged file
    is corrupt (bytes do not re-hash to its pinned name or JSON is
    malformed): a corrupt store fails closed, never silently skipped.
    """
    if not declared_sha256:
        return None, None
    path = Path(staging_dir) / subdir / f"{declared_sha256}.json"
    if not path.is_file():
        return None, None
    try:
        raw = _read_staged_file(path)
    except (OSError, ValueError) as exc:
        raise OSError(f"unreadable staged {label}: {exc}") from exc
    if hashlib.sha256(raw).hexdigest() != declared_sha256:
        raise ValueError(f"staged {label} bytes fail re-hash")
    try:
        document = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ValueError(f"malformed staged {label} JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"staged {label} is not a JSON object")
    return document, declared_sha256


def _load_epochs(staging_dir):
    """Load the optional epoch list; None when unstaged.

    NOTE: epochs carry no sidecar hash pin, so staged epochs are
    trusted store content. Integrity anchoring is custody-owned
    Wave-2 work; until then a corrupt file fails closed.
    """
    path = Path(staging_dir) / "epochs.json"
    if not path.is_file():
        return None
    try:
        raw = _read_staged_file(path)
        epochs = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"unreadable staged epochs: {exc}") from exc
    if not isinstance(epochs, list):
        raise ValueError("staged epochs is not a JSON list")
    return epochs


def build_ctx(request, data_dir):
    """Build the spatial validator ``ctx`` from Core-staged bindings.

    Raises ``OSError``/``ValueError``/``TypeError`` on anything
    unverifiable; the caller maps those to unavailable (fail closed).
    """
    if not isinstance(request, dict):
        raise TypeError("validator request is not a dict")
    sidecar = request.get("sidecar")
    if not isinstance(sidecar, dict):
        raise ValueError("no accepted sidecar in validator request")
    package_id = request.get("package_id")
    image_id = request.get("image_id")
    image_sha256 = request.get("image_sha256")
    if not package_id or not image_id or not image_sha256:
        raise ValueError("validator request lacks package/image binding")
    digest, length = _resolve_image_bytes(
        data_dir, package_id, image_id, image_sha256)
    ctx = {"image_shas": {digest: length}}
    staging_dir = Path(data_dir) / "spatial"
    calibration = sidecar.get("calibration")
    calibration = calibration if isinstance(calibration, dict) else {}
    layout, layout_sha = _load_hash_pinned_json(
        staging_dir, "layouts", calibration.get("layout_sha256"), "layout")
    if layout is not None:
        ctx["layout"] = layout
        ctx["layout_sha256"] = layout_sha
    profile, profile_sha = _load_hash_pinned_json(
        staging_dir, "profiles", calibration.get("profile_sha256"), "profile")
    if profile is not None:
        ctx["profile"] = profile
        ctx["profile_sha256"] = profile_sha
    epochs = _load_epochs(staging_dir)
    if epochs is not None:
        ctx["epochs"] = epochs
    return ctx


def adapt_report(spatial_result, request):
    """Map a spatial result onto the Core validator report shape.

    Returns a journal-ready report dict: ``available True`` with a
    ``valid`` boolean for decisive verdicts, or ``available False``
    (receipt stays ``package_only``) for uncertain/non-metric verdicts.
    Hashes echo the request verbatim so the journal's exact-match gate
    compares receipt bytes, not validator claims.
    """
    verdict = spatial_result.get("verdict")
    metric_ready = spatial_result.get("metric_ready") is True
    reasons = spatial_result.get("reasons") or []
    report = {"available": True,
              "package_sha256": request.get("package_sha256"),
              "image_sha256": request.get("image_sha256"),
              "sidecar_sha256": request.get("sidecar_sha256")}
    if verdict == "accept" and metric_ready:
        report["valid"] = True
        report["reason_codes"] = []
        return report
    if verdict == "reject":
        report["valid"] = False
        report["reason_codes"] = list(reasons)
        return report
    head = ", ".join(reasons[:3]) or "no reasons"
    return _unavailable(
        request,
        f"spatial verdict={verdict} metric_ready={metric_ready}: {head}")


def run_validator(request, data_dir):
    """Run the approved validator; unavailable fails closed to package_only."""
    try:
        validate_sidecar = _load_validator()
        ctx = build_ctx(request, data_dir)
        spatial_result = validate_sidecar(request["sidecar"], ctx)
    except Exception as exc:  # noqa: BLE001 - fail closed by contract
        return _unavailable(request, f"spatial validator failed: {exc}")
    if not isinstance(spatial_result, dict):
        return _unavailable(request, "spatial validator returned no report")
    try:
        return adapt_report(spatial_result, request)
    except Exception as exc:  # noqa: BLE001 - fail closed by contract
        return _unavailable(request, f"spatial report unmappable: {exc}")
