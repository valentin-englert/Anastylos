"""Inspection orchestrator: selection, reader invocation, reconciliation.

One call = one MicroscopyInspection record for one image (+ optional ROI).
The orchestrator never decides what a component *is*; it runs the readers
the operator's mode/hypothesis justifies, keeps every native result, and
aggregates their verdicts conservatively.
"""

from __future__ import annotations

import os
import platform
import sys
import time
import traceback
from pathlib import Path

from anastylos_microscopy import (INSPECTION_SCHEMA, PINNED_ANASTYLOS_REVISION, __version__,
                                  assessment, calibration as cal, env, evidence)
from anastylos_microscopy.adapters import (OCR_READERS, REGISTRY, ReaderInputError,
                                           ReaderUnavailable, core_status)
from anastylos_microscopy.util import (atomic_write_json, canonical_json, json_safe, parse_utc,
                                       seconds_clock, sha256_bytes, sha256_file, utc_now)

SINGLE_MODES = tuple(REGISTRY)  # resistor, smd, pcb_ocr, markings, polarity, screw, ...
MODES = SINGLE_MODES + ("multi", "auto")

# Operator hypothesis -> readers that are meaningful for it. There is no
# general-purpose component detector; without a hypothesis "auto" only runs
# the object-agnostic text readers.
HYPOTHESES: dict[str, dict] = {
    "axial_resistor": {"readers": ["resistor"]},
    "smd_resistor": {"readers": ["smd"]},
    "ic": {"readers": ["markings", "polarity"], "defaults": {"polarity_family": "ic"}},
    "diode": {"readers": ["markings", "polarity"], "defaults": {"polarity_family": "diode"}},
    "capacitor": {"readers": ["markings", "polarity"]},
    "pcb_region": {"readers": ["pcb_ocr"]},
    "screw": {"readers": ["screw", "screw_measure"]},
    "washer": {"readers": ["washer"]},
    "connector": {"readers": ["connectors"]},
    "unknown": {"readers": ["pcb_ocr", "markings"]},
}

EPISTEMIC_STATEMENT = (
    "Every value in this record is either observed visual evidence or an algorithmic "
    "interpretation of it. Candidates are hypotheses, scores are uncalibrated heuristics, "
    "and nothing here is a verified physical specification, manufacturer, rating or "
    "interchangeability claim. Only a separate operator review record can confirm an "
    "identification.")

COORDINATES = "source-image pixels; origin top-left; +x right; +y down (ANASTYLOS convention)"


def core_code_sha256() -> str:
    return env.code_sha256(Path(__file__).resolve().parent)


def gallery_digest(path: str | None) -> str | None:
    if not path or not Path(path).is_dir():
        return None
    parts = []
    for item in sorted(Path(path).glob("*.json")):
        parts.append(item.name + ":" + sha256_file(item))
    return sha256_bytes("\n".join(parts).encode("utf-8"))


def select_readers(mode: str, readers: list[str] | None, hypothesis: str | None,
                   options: dict) -> dict:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose one of {list(MODES)}")
    rationale, skipped, defaults = [], [], {}
    if mode in SINGLE_MODES:
        chosen = [mode]
        rationale.append(f"explicit mode {mode!r}")
    elif mode == "multi":
        if not readers:
            raise ValueError("mode 'multi' needs --readers r1,r2,...")
        unknown = [r for r in readers if r not in REGISTRY]
        if unknown:
            raise ValueError(f"unknown readers {unknown}; available {sorted(REGISTRY)}")
        chosen = list(dict.fromkeys(readers))
        rationale.append("explicit reader list")
    else:
        key = hypothesis or "unknown"
        if key not in HYPOTHESES:
            raise ValueError(f"unknown hypothesis {key!r}; choose one of {sorted(HYPOTHESES)}")
        spec = HYPOTHESES[key]
        chosen = list(spec["readers"])
        defaults = dict(spec.get("defaults") or {})
        rationale.append(f"auto: operator hypothesis {key!r}" if hypothesis else
                         "auto without hypothesis: only object-agnostic text readers; no general "
                         "component detector exists, so category-specific readers are not guessed")
        for reader in ("screw_measure", "washer"):
            if reader in chosen and options.get("view") is None:
                chosen.remove(reader)
                skipped.append({"reader": reader, "reason": "needs an explicit --view (top/side)"})
        if options.get("gallery") and "fingerprints" not in chosen and key != "connector":
            chosen.append("fingerprints")
            rationale.append("fingerprint gallery supplied: gallery match added")
    return {"mode": mode, "hypothesis": hypothesis, "readers": chosen, "skipped": skipped,
            "rationale": rationale, "derived_options": defaults}


def reconcile(reader_blocks: list[dict]) -> dict:
    claims = []
    for block in reader_blocks:
        for claim in block.get("claims") or []:
            claims.append({"reader": block["reader"], **claim})
    conflicts, agreements = [], []
    for domain in sorted({c["domain"] for c in claims}):
        group = [c for c in claims if c["domain"] == domain]
        values = sorted({repr(c["value"]) for c in group})
        readers = sorted({c["reader"] for c in group})
        if len(readers) < 2:
            continue
        entry = {"domain": domain, "readers": readers,
                 "values": [{"reader": c["reader"], "value": c["value"]} for c in group]}
        (conflicts if len(values) > 1 else agreements).append(entry)
    return {"claims": claims, "conflicts": conflicts, "agreements": agreements,
            "note": "Agreement between readers is consistency, not verification."}


def overall_status(reader_blocks, reconciliation, quality) -> tuple[str, list[str]]:
    statuses = [b["status"] for b in reader_blocks]
    reasons = []
    if not statuses:
        return "unknown", ["no reader was run"]
    failing = {"unavailable", "reader_error", "input_error", "not_applicable"}
    if all(s in failing for s in statuses):
        status = "unavailable" if "unavailable" in statuses else "error"
        reasons.append("no reader produced an interpretable result")
        return status, reasons
    if reconciliation["conflicts"]:
        reasons += [f"conflicting {c['domain']} between {', '.join(c['readers'])}"
                    for c in reconciliation["conflicts"]]
        return "conflict", reasons
    blocking = [f["flag"] for f in quality.get("flags", []) if f.get("blocking")]
    if "ok" in statuses and blocking:
        reasons.append(f"image quality flags {blocking}: reader 'ok' verdicts are held as ambiguous "
                       "until reviewed")
        return "ambiguous", reasons
    if "ambiguous" in statuses:
        reasons.append("at least one reader is ambiguous")
        return "ambiguous", reasons
    if "ok" in statuses:
        return "candidate", ["at least one reader produced a clear candidate (not a confirmation)"]
    if all(s in ("enrolled", "observed") or s in failing for s in statuses):
        return "recorded", ["evidence recorded (enrollment/observation only)"]
    reasons.append("readers abstained (unknown/rejected)")
    return "unknown", reasons


def _reader_internal_versions(key: str) -> dict:
    versions = {}
    try:
        if key == "fingerprints":
            from anastylos_fingerprints.describe import DESCRIPTOR_VERSION
            versions["descriptor_version"] = DESCRIPTOR_VERSION
        elif key == "connectors":
            from anastylos_connectors.observe import OBSERVATION_VERSION
            versions["observation_version"] = OBSERVATION_VERSION
    except Exception:  # noqa: BLE001 - informational only
        pass
    return versions


def _write_png(path: Path, image) -> str | None:
    import cv2

    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        return None
    data = buffer.tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256_bytes(data)


ASSESSED_READERS = {"resistor": assessment.assess_resistor}


def _apply_core_assessment(key, block, native, normalized, image) -> None:
    """QA-03: hold an unsupported native 'ok' as 'ambiguous'; never edit native evidence."""
    assess = ASSESSED_READERS.get(key)
    if assess is None or block["status"] != "ok":
        if assess is not None:
            block["core_assessment"] = {"assessor": assessment.ASSESSOR, "action": "none",
                                        "applies_to_native_status": block["native_status"],
                                        "note": "only native 'ok' verdicts are assessed"}
        return
    try:
        result = assess(native, image)
    except Exception as exc:  # noqa: BLE001 - fail closed: an unassessable 'ok' is not supported
        result = {"assessor": assessment.ASSESSOR, "supports_ok": False,
                  "failed_checks": ["assessment_error"],
                  "checks": [{"check": "assessment_error", "status": "fail",
                              "detail": f"{type(exc).__name__}: {exc}"}]}
    if result["supports_ok"]:
        result["action"] = "kept"
        block["core_assessment"] = result
        return
    failed = [c for c in result["checks"] if c.get("status") == "fail"]
    result["action"] = "downgraded"
    result["from_status"], result["to_status"] = "ok", "ambiguous"
    result["candidate_preserved"] = True
    block["core_assessment"] = result
    block["status"] = "ambiguous"
    block["reason"] = ("Core assessment holds the reader's 'ok' as ambiguous: "
                       + "; ".join(f"{c['check']}: {c['detail']}" for c in failed))
    for candidate in normalized.get("candidates") or []:
        candidate["core_assessment"] = "not_supported_by_band_evidence"


def _run_reader(key, image, roi, ctx, inspection_dir: Path, pinned: dict, root: Path) -> dict:
    adapter = REGISTRY[key]
    identity = env.reader_identity(root, key, pinned)
    reader_dir = inspection_dir / "readers" / key
    reader_dir.mkdir(parents=True, exist_ok=True)
    ctx = {**ctx, "reader_dir": str(reader_dir)}
    _, cal_note = adapter.reader_calibration(ctx)
    block = {**identity, "api": adapter.description, "internal_versions": {},
             "calibration_adaptation": cal_note, "status": None, "native_status": None,
             "reason": None, "ocr_backend": None, "native_result": None, "artifacts": []}
    started = time.perf_counter()
    native = None
    try:
        if not identity["present"]:
            raise ReaderUnavailable(f"module {identity['module']} not found under ANASTYLOS_ROOT")
        native = json_safe(adapter.run(image, roi, ctx))
        native_status = native.get("status", native.get("verdict"))
        block["native_status"] = native_status
        block["status"] = core_status(native_status)
        block["reason"] = native.get("reason") or ("; ".join(native["reasons"])
                                                   if isinstance(native.get("reasons"), list)
                                                   and native.get("reasons") else None)
    except ReaderUnavailable as exc:
        block.update(status="unavailable", reason=str(exc))
    except ReaderInputError as exc:
        block.update(status="input_error", reason=str(exc))
    except ValueError as exc:
        block.update(status="input_error", reason=f"reader rejected input: {exc}")
    except Exception as exc:  # noqa: BLE001 - a reader crash must not stop the batch
        block.update(status="reader_error", reason=f"{type(exc).__name__}: {exc}",
                     traceback_tail=traceback.format_exc().strip().splitlines()[-3:])
    block["internal_versions"] = _reader_internal_versions(key)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    if native is not None:
        block["native_result"] = native
        if isinstance(native.get("backend"), dict):
            block["ocr_backend"] = native["backend"]
        sha = atomic_write_json(reader_dir / "result.json", native)
        block["artifacts"].append({"role": "native_result", "path": f"readers/{key}/result.json",
                                   "sha256": sha})
        try:
            overlay = adapter.annotate(image, native, roi)
            if overlay is not None:
                png_sha = _write_png(reader_dir / "annotated.png", overlay)
                if png_sha:
                    block["artifacts"].append({"role": "annotated_overlay",
                                               "path": f"readers/{key}/annotated.png",
                                               "sha256": png_sha, "derived": True})
        except Exception as exc:  # noqa: BLE001 - overlays are optional
            block["annotation_error"] = f"{type(exc).__name__}: {exc}"
        normalized = adapter.normalize(native)
        _apply_core_assessment(key, block, native, normalized, image)
    else:
        normalized = {"observed": [], "interpreted": [], "candidates": [], "claims": []}
    calib_file = reader_dir / "screw_measurement_calibration.json"
    if calib_file.exists():
        block["artifacts"].append({"role": "adapted_calibration",
                                   "path": f"readers/{key}/screw_measurement_calibration.json",
                                   "sha256": sha256_file(calib_file)})
    if key in OCR_READERS and block["status"] == "unavailable" and block["reason"] is None:
        block["reason"] = "OCR backend unavailable"
    block.update({k: normalized.get(k, []) for k in ("observed", "interpreted", "candidates", "claims")})
    for extra in ("installation", "not_estimated", "geometry_mm", "calibration_error"):
        if extra in normalized:
            block[extra] = normalized[extra]
    return block, duration_ms


def _input_error_record(base: dict, code: str, message: str) -> dict:
    base["uncertainty"] = {"overall_status": "input_error", "reasons": [f"{code}: {message}"],
                           "quality_flags": [], "unavailable_readers": [],
                           "statement": EPISTEMIC_STATEMENT}
    base["input_error"] = {"code": code, "message": message}
    return base


def inspect_image(image_path, out_dir, *, mode="auto", readers=None, roi=None, options=None,
                  hypothesis=None, calibration_doc=None, calibration_origin=None, capture=None,
                  session_id=None, component_observation_id=None, clock=None,
                  anastylos_root=None, ocr_backend=None, tesseract_cmd=None,
                  expected_sha256=None, image_bytes=None) -> tuple[dict, Path]:
    """Inspect one image; write <out>/<inspection_id>/inspection.json. Returns (record, path).

    ``image_bytes`` (v0.1.1): bytes the caller already read (confined capture
    manifest / dataset read). ``image_path`` is then a label only and is never
    reopened; hashing, decoding and evidence preservation use these bytes.
    """
    wall_start = utc_now()
    t0 = time.perf_counter()
    options = dict(options or {})
    ingested = parse_utc(clock) if clock else utc_now()
    root = env.resolve_root(anastylos_root)
    env.activate(root)
    pinned = env.load_pinned_hashes()
    capture = dict(capture or {})
    out_dir = Path(out_dir)

    selection = select_readers(mode, readers, hypothesis, options)
    for k, v in selection["derived_options"].items():
        options.setdefault(k, v)

    # --- intake -------------------------------------------------------
    intake_error = None
    image = None
    try:
        source_record, image = evidence.intake(image_path, data=image_bytes)
        if expected_sha256 and source_record["sha256"] != expected_sha256:
            raise evidence.IntakeError("manifest_sha256_mismatch",
                                       f"file hash {source_record['sha256'][:16]}... does not match "
                                       f"capture manifest {expected_sha256[:16]}...")
    except evidence.IntakeError as exc:
        intake_error = exc
        source_record = {"original_path": str(image_path),
                         "file_name": evidence.display_name(str(image_path))}
        data = image_bytes if image_bytes is not None else (
            Path(image_path).read_bytes() if Path(image_path).is_file() else None)
        if data is not None:
            source_record.update(size_bytes=len(data), sha256=sha256_bytes(data),
                                 format=evidence.detect_format(data))
    roi_list = None
    if intake_error is None:
        try:
            roi_list = evidence.validate_roi(roi, source_record["resolution"]["width"],
                                             source_record["resolution"]["height"])
        except evidence.IntakeError as exc:
            intake_error = exc

    # --- calibration & config --------------------------------------------
    resolution = source_record.get("resolution") or {"width": 0, "height": 0}
    calibration = cal.resolve(calibration_doc, resolution, calibration_origin) if calibration_doc \
        is not None else cal.absent()
    ocr_info = ({"backend": getattr(ocr_backend, "name", "injected"),
                 "version": ocr_backend.version() if hasattr(ocr_backend, "version") else None,
                 "injected": True} if ocr_backend is not None else
                {"backend": "tesseract", "tesseract_cmd": tesseract_cmd, "injected": False})
    config = {
        "mode": mode, "requested_readers": readers, "hypothesis": hypothesis,
        "selected_readers": selection["readers"], "roi": roi_list if roi_list is not None else roi,
        "options": options, "ocr": ocr_info,
        "calibration_document_sha256": sha256_bytes(canonical_json(calibration_doc))
        if calibration_doc is not None else None,
        "gallery_sha256": gallery_digest(options.get("gallery")),
        "core_version": __version__,
    }
    config_sha = sha256_bytes(canonical_json(config))
    image_sha = source_record.get("sha256") or "no-file"
    # The capture context (manifest frame index, declared hash), session and component ids are
    # part of the identity so that byte-identical frames in one session never share a record.
    context_sha = sha256_bytes(canonical_json({"capture": capture, "session_id": session_id,
                                               "component_observation_id": component_observation_id}))
    inspection_id = "mi-" + sha256_bytes(
        f"{image_sha}|{config_sha}|{context_sha}|{ingested}".encode())[:20]
    inspection_dir = out_dir / inspection_id
    inspection_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "schema": INSPECTION_SCHEMA,
        "core_version": __version__,
        "inspection_id": inspection_id,
        "session_id": session_id,
        "component_observation_id": component_observation_id,
        "ingested_utc": ingested,
        "source": {
            "origin": capture.get("origin", "file"),
            "camera_id": capture.get("camera_id"),
            "node_id": capture.get("node_id"),
            "optical_profile": capture.get("optical_profile"),
            "capture_utc": capture.get("captured_utc"),
            "capture_manifest": capture.get("manifest"),
            "validation_class": capture.get("validation_class", "unspecified"),
        },
        "image": source_record,
        "orientation": {
            "stored_pixels_modified": False,
            "applied_by_core": False,
            "declared_display_rotation_cw_deg": capture.get("orientation_cw_deg"),
            "declared_by": capture.get("orientation_source") if capture.get("orientation_cw_deg")
            is not None else None,
            "exif_orientation": source_record.get("exif_orientation"),
            "reader_frame": "stored pixel grid as decoded (EXIF rotation ignored); readers that "
                            "search orientations (pcb-ocr) do so internally",
        },
        "roi": {"xywh": roi_list, "coordinates": COORDINATES,
                "provided_by": capture.get("roi_source", "operator") if roi_list else None},
        "calibration": calibration,
        "request": {"mode": mode, "readers": readers, "hypothesis": hypothesis, "options": options},
        "selection": {k: v for k, v in selection.items() if k != "derived_options"},
        "quality": None,
        "recognizers": [],
        "observed_evidence": [],
        "interpretations": [],
        "candidates": [],
        "reconciliation": None,
        "uncertainty": None,
        "human_review": {"status": "pending", "required": True, "confirmed_identification": None,
                         "note": "Confirmation is recorded only in a separate review record "
                                 "(python -m anastylos_microscopy review); this file is never edited."},
        "artifacts": [],
        "provenance": {
            "anastylos": {"root": str(root), **env.detect_revision(root)},
            "core": {"version": __version__, "code_sha256": core_code_sha256()},
            "blobcam_runtime_dependency": False,
            "assembly_memory_writes": False,
            "config_sha256": config_sha,
            "replay": {"config": config, "clock": ingested,
                       "calibration_document": calibration_doc, "capture": capture,
                       "session_id": session_id,
                       "component_observation_id": component_observation_id},
        },
    }
    durations = {}
    if source_record.get("sha256") and (image_bytes is not None or Path(image_path).is_file()) and \
            (intake_error is None or intake_error.code != "file_not_found"):
        try:
            record["artifacts"].append(evidence.preserve_original(
                image_path, source_record["sha256"], source_record.get("format") or "bin",
                inspection_dir / "evidence", data=image_bytes))
            record["artifacts"][-1]["path"] = "evidence/" + record["artifacts"][-1]["path"]
        except evidence.IntakeError as exc:
            intake_error = intake_error or exc

    if intake_error is not None:
        _input_error_record(record, intake_error.code, str(intake_error))
        record["reconciliation"] = {"claims": [], "conflicts": [], "agreements": [], "note": None}
    else:
        record["quality"] = evidence.quality_indicators(image, roi_list)
        if roi_list:
            x, y, w, h = roi_list
            sha = _write_png(inspection_dir / "evidence" / "roi_crop.png", image[y:y + h, x:x + w])
            record["artifacts"].append({"role": "roi_crop", "path": "evidence/roi_crop.png",
                                        "sha256": sha, "derived": True,
                                        "note": "lossless crop for review; readers receive the full "
                                                "image plus ROI and report source-frame coordinates"})
        ctx = {"image_name": source_record["file_name"], "image_sha256": source_record["sha256"],
               "calibration": calibration, "options": options, "ocr_backend": ocr_backend,
               "tesseract_cmd": tesseract_cmd, "reader_clock": seconds_clock(ingested)}
        for key in selection["readers"]:
            block, durations[key] = _run_reader(key, image, roi_list, ctx, inspection_dir, pinned, root)
            record["recognizers"].append(block)
            for layer, field in (("observed_evidence", "observed"), ("interpretations", "interpreted"),
                                 ("candidates", "candidates")):
                record[layer] += [{"reader": key, **item} for item in block[field]]
        record["reconciliation"] = reconcile(record["recognizers"])
        status, reasons = overall_status(record["recognizers"], record["reconciliation"],
                                         record["quality"])
        for block in record["recognizers"]:
            if (block.get("core_assessment") or {}).get("action") == "downgraded":
                reasons.append(f"{block['reader']}: native 'ok' held as ambiguous by Core evidence "
                               f"assessment (failed: {', '.join(block['core_assessment']['failed_checks'])}); "
                               "native candidate preserved for operator review")
        if calibration["status"] == "invalid":
            reasons.append("calibration rejected: " + "; ".join(calibration["reasons"]) +
                           " (pixel-only output)")
        elif calibration["status"] == "absent":
            reasons.append("no calibration: all geometry is pixel-only")
        record["uncertainty"] = {
            "overall_status": status,
            "reasons": reasons,
            "quality_flags": record["quality"]["flags"],
            "unavailable_readers": [{"reader": b["reader"], "status": b["status"], "reason": b["reason"]}
                                    for b in record["recognizers"]
                                    if b["status"] in ("unavailable", "reader_error", "input_error")],
            "reader_statuses": {b["reader"]: b["status"] for b in record["recognizers"]},
            "native_reader_statuses": {b["reader"]: b["native_status"] for b in record["recognizers"]},
            "statement": EPISTEMIC_STATEMENT,
        }
    for block in record["recognizers"]:
        record["artifacts"] += [{"reader": block["reader"], **a} for a in block["artifacts"]]

    record = json_safe(record)
    digest_scope = {k: v for k, v in record.items() if k not in ("digest", "run")}
    record["digest"] = {"algorithm": "sha256",
                        "scope": "canonical JSON (sorted keys, compact) of the record without "
                                 "'digest' and 'run'",
                        "value": sha256_bytes(canonical_json(digest_scope))}
    record["run"] = {
        "volatile": True,
        "wall_clock_started_utc": wall_start,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        "reader_duration_ms": durations,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "out_dir": str(out_dir),
        "pid": os.getpid(),
    }
    path = inspection_dir / "inspection.json"
    atomic_write_json(path, record)
    from anastylos_microscopy.report import write_report
    write_report(record, inspection_dir / "report.md")
    return record, path
