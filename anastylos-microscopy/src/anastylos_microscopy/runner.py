"""Batch, capture-manifest and replay drivers around inspect_image.

Batch outcome aggregation (v0.1.1, QA-01)
-----------------------------------------
Every entry gets a ``processing`` class that says whether the *processing*
worked - independent of what was recognized:

* ``completed``                    record written, readers produced an interpretable
                                   result (candidate / ambiguous / conflict / unknown /
                                   recorded). Abstentions are completed work.
* ``completed_with_reader_errors`` record written and interpretable, but at least one
                                   selected reader crashed (``reader_error``).
* ``unavailable``                  record written, no reader could run (missing optional
                                   dependency, e.g. Tesseract). Not a successful inspection.
* ``reader_error``                 record written, every reader failed (overall ``error``).
* ``input_error``                  record written, the image was refused at intake.
* ``manifest_rejected``            capture-manifest frame refused before any byte was read
                                   (traversal, confinement, missing frame, bad metadata).
* ``crashed``                      unexpected exception; no record.

``exit_code_for`` maps the classes to the process exit status (see cli.py).
"""

from __future__ import annotations

import traceback
from pathlib import Path

from anastylos_microscopy import BATCH_SCHEMA, capture, env, evidence
from anastylos_microscopy.orchestrator import inspect_image
from anastylos_microscopy.util import (atomic_write_json, canonical_json, read_json, sha256_bytes,
                                       sha256_file, utc_now)

PROCESSING_CLASSES = ("completed", "completed_with_reader_errors", "unavailable", "reader_error",
                      "input_error", "manifest_rejected", "crashed")
CLEAN = ("completed",)
# Entries with a usable inspection record (possibly incomplete).
INTERPRETABLE = ("completed", "completed_with_reader_errors")
ABSTENTION_STATUSES = ("unknown", "ambiguous", "conflict")
OCR_READER_KEYS = ("smd", "pcb_ocr", "markings")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
EXIT_PARTIAL = 4


def classify_record(record: dict) -> str:
    overall = record["uncertainty"]["overall_status"]
    if overall == "input_error":
        return "input_error"
    if overall == "unavailable":
        return "unavailable"
    if overall == "error":
        return "reader_error"
    if any(b.get("status") == "reader_error" for b in record.get("recognizers") or []):
        return "completed_with_reader_errors"
    return "completed"


def exit_code_for(classes: list[str]) -> int:
    """Process exit status for a list of entry processing classes.

    0  every entry ``completed`` (abstentions included)
    1  nothing usable: empty batch, or every entry failed (input/reader/crash/rejected)
    3  every entry ``unavailable`` (required reader dependency missing)
    4  partial: at least one usable entry and at least one problem entry
    """
    if not classes:
        return EXIT_FAILED
    if all(c in CLEAN for c in classes):
        return EXIT_OK
    if all(c == "unavailable" for c in classes):
        return EXIT_ENVIRONMENT
    if any(c in INTERPRETABLE for c in classes):
        return EXIT_PARTIAL
    return EXIT_FAILED


def _entry(path, record=None, record_path=None, error=None, processing=None):
    entry = {"file": str(path), "inspection_id": None, "overall_status": None, "digest": None,
             "inspection_json": None, "error": error, "processing": processing}
    if record is not None:
        entry.update(inspection_id=record["inspection_id"],
                     overall_status=record["uncertainty"]["overall_status"],
                     digest=record["digest"]["value"],
                     inspection_json=str(Path(record_path).relative_to(record_path.parents[1])),
                     processing=classify_record(record),
                     reader_statuses={b["reader"]: b["status"] for b in record["recognizers"]},
                     core_downgraded_readers=[b["reader"] for b in record["recognizers"]
                                              if (b.get("core_assessment") or {}).get("action")
                                              == "downgraded"])
        if entry["processing"] != "completed":
            reasons = record["uncertainty"].get("reasons") or []
            entry["error"] = "; ".join(reasons[:3]) or entry["processing"]
    return entry


def _summary(entries):
    """Backward-compatible v0.1.0 field: overall_status counts ('crashed' = no record)."""
    counts = {}
    for entry in entries:
        key = entry["overall_status"] or (
            "manifest_rejected" if entry.get("processing") == "manifest_rejected" else "crashed")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _outcome(entries) -> dict:
    classes = [e["processing"] for e in entries]
    counts = {name: classes.count(name) for name in PROCESSING_CLASSES}
    reader_counts: dict[str, dict] = {}
    for entry in entries:
        for reader, status in (entry.get("reader_statuses") or {}).items():
            bucket = reader_counts.setdefault(reader, {})
            bucket[status] = bucket.get(status, 0) + 1
    ocr = {key: reader_counts[key] for key in OCR_READER_KEYS if key in reader_counts}
    code = exit_code_for(classes)
    label = {EXIT_OK: "success", EXIT_PARTIAL: "partial_failure",
             EXIT_ENVIRONMENT: "environment_unavailable", EXIT_FAILED: "failure"}[code]
    if not entries:
        label = "empty"
    return {
        "outcome": label,
        "exit_code": code,
        "outcome_counts": {
            "total": len(entries),
            **counts,
            "usable_records": sum(counts[c] for c in INTERPRETABLE),
            "problem_entries": len(entries) - counts["completed"],
            "abstentions": sum(1 for e in entries if e["processing"] in INTERPRETABLE
                               and e["overall_status"] in ABSTENTION_STATUSES),
            "candidates": sum(1 for e in entries if e["overall_status"] == "candidate"),
            "core_downgraded": sum(1 for e in entries if e.get("core_downgraded_readers")),
        },
        "reader_status_counts": reader_counts,
        "ocr": {
            "readers": ocr,
            "unavailable_entries": sum(1 for e in entries if any(
                (e.get("reader_statuses") or {}).get(k) == "unavailable" for k in OCR_READER_KEYS)),
            "note": "OCR readers with status 'unavailable' did not run; they are never counted "
                    "as OCR results",
        },
        "exit_code_semantics": "0 all completed; 1 nothing usable or empty; 3 all unavailable; "
                               "4 partial (see outcome_counts)",
    }


def _write_batch(out_dir: Path, entries, extra: dict, clock) -> tuple[dict, Path]:
    batch = {"schema": BATCH_SCHEMA, "created_utc": clock or utc_now(), **extra,
             "count": len(entries), "status_counts": _summary(entries), **_outcome(entries),
             "entries": entries}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "batch.json"
    atomic_write_json(path, batch)
    return batch, path


def _preflight(kwargs) -> None:
    """Fatal setup check before any image is touched (raises ReaderEnvironmentError)."""
    env.resolve_root(kwargs.get("anastylos_root"))


def inspect_batch(paths, out_dir, **kwargs) -> tuple[dict, Path]:
    """Inspect several images; one failing image never stops the others.

    Raises env.ReaderEnvironmentError before processing anything when
    ANASTYLOS_ROOT is missing/invalid (fatal setup failure, nothing written).
    """
    _preflight(kwargs)
    out_dir = Path(out_dir)
    entries = []
    for path in paths:
        try:
            record, record_path = inspect_image(path, out_dir, **{**kwargs, "capture": {
                **(kwargs.get("capture") or {}), "origin": "batch"}})
            entries.append(_entry(path, record, record_path))
        except Exception as exc:  # noqa: BLE001 - recorded, batch continues
            entries.append(_entry(path, error=f"{type(exc).__name__}: {exc}", processing="crashed")
                           | {"traceback_tail": traceback.format_exc().splitlines()[-3:]})
    return _write_batch(out_dir, entries, {}, kwargs.get("clock"))


def inspect_directory(directory, out_dir, **kwargs):
    _preflight(kwargs)
    images = evidence.list_images(Path(directory))
    if not images:
        raise ValueError(f"empty batch: no images ({', '.join(evidence.IMAGE_EXTENSIONS)}) in "
                         f"{directory}")
    return inspect_batch(images, out_dir, **kwargs)


def inspect_manifest(manifest_path, out_dir, **kwargs) -> tuple[dict, Path]:
    _preflight(kwargs)
    manifest = capture.load(manifest_path)
    manifest_sha = sha256_file(manifest_path)
    out_dir = Path(out_dir)
    entries = []
    calibration_doc = kwargs.pop("calibration_doc", None)
    origin = kwargs.pop("calibration_origin", None)
    if calibration_doc is None and manifest["calibration"] is not None:
        calibration_doc, origin = manifest["calibration"], f"capture_manifest:{manifest_path}"
    session = kwargs.pop("session_id", None) or manifest.get("session_id")
    base_roi = kwargs.pop("roi", None)
    for entry in manifest["entries"]:
        if entry.get("problems"):
            entries.append(_entry(entry.get("file"), processing="manifest_rejected",
                                  error="manifest entry rejected: " + "; ".join(entry["problems"]))
                           | {"rejection": entry.get("rejection")})
            continue
        try:
            # Confined single read: these exact bytes are hashed, decoded and preserved.
            data, confinement = capture.read_frame(manifest["capture_root"], entry["rel"])
        except capture.ConfinementError as exc:
            entries.append(_entry(entry["file"], processing="manifest_rejected",
                                  error=f"manifest entry rejected: {exc.code}: {exc}")
                           | {"rejection": exc.code})
            continue
        ctx = capture.capture_context(manifest, entry, manifest_sha)
        ctx["manifest"]["confinement"] = {k: confinement[k] for k in
                                          ("policy", "links_followed", "identity_check", "single_read")}
        try:
            record, record_path = inspect_image(
                entry["path"], out_dir, roi=entry.get("roi_xywh") or base_roi, capture=ctx,
                calibration_doc=calibration_doc, calibration_origin=origin, session_id=session,
                component_observation_id=entry.get("component_observation_id"),
                expected_sha256=entry["sha256"], image_bytes=data, **kwargs)
            entries.append(_entry(entry["file"], record, record_path))
        except Exception as exc:  # noqa: BLE001
            entries.append(_entry(entry["file"], error=f"{type(exc).__name__}: {exc}",
                                  processing="crashed"))
    return _write_batch(out_dir, entries, {
        "capture_manifest": {"path": str(manifest_path), "sha256": manifest_sha,
                             "validation_class": manifest["validation_class"],
                             "capture_root": manifest["capture_root"]}}, kwargs.get("clock"))


def replay(inspection_path, out_dir, *, image=None, anastylos_root=None, ocr_backend=None) -> dict:
    """Re-run a stored inspection configuration and compare digests.

    Capture-manifest records are re-read through the same confinement as the
    original run (unless the operator overrides --image).
    """
    original = read_json(inspection_path)
    rep = original["provenance"]["replay"]
    config = rep["config"]
    image_path = image or original["image"]["original_path"]
    image_bytes = None
    manifest_ctx = (rep.get("capture") or {}).get("manifest") or {}
    if image is None and (rep.get("capture") or {}).get("origin") == "capture_manifest" \
            and manifest_ctx.get("capture_root") and manifest_ctx.get("frame_file"):
        rel = capture.safe_relative(manifest_ctx["frame_file"])
        image_bytes, _ = capture.read_frame(manifest_ctx["capture_root"], rel)
    record, path = inspect_image(
        image_path, out_dir, mode=config["mode"], readers=config["requested_readers"],
        roi=config["roi"], options=config["options"], hypothesis=config["hypothesis"],
        calibration_doc=rep["calibration_document"],
        calibration_origin=original["calibration"].get("source"),
        capture=rep["capture"], session_id=rep["session_id"],
        component_observation_id=rep["component_observation_id"], clock=rep["clock"],
        anastylos_root=anastylos_root, ocr_backend=ocr_backend,
        tesseract_cmd=(config.get("ocr") or {}).get("tesseract_cmd"),
        expected_sha256=manifest_ctx.get("declared_sha256") if image_bytes is not None else None,
        image_bytes=image_bytes)
    differences = []
    if record["digest"]["value"] != original["digest"]["value"]:
        for key in sorted(set(original) | set(record)):
            if key in ("digest", "run"):
                continue
            if sha256_bytes(canonical_json(original.get(key))) != \
                    sha256_bytes(canonical_json(record.get(key))):
                differences.append(key)
    return {"original_inspection_id": original["inspection_id"],
            "replay_inspection_id": record["inspection_id"],
            "original_digest": original["digest"]["value"],
            "replay_digest": record["digest"]["value"],
            "identical": record["digest"]["value"] == original["digest"]["value"],
            "differing_sections": differences, "replay_path": str(path)}
