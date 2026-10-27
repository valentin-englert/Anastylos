"""Evidence inputs: detection results, images, inventories, confirmations.

Every input is read with its provenance (path plus SHA-256) and validated
before use. Detection ``result.json`` files are consumed verbatim; their
status, view, and rectified frame decide whether millimetre coordinates
are trustworthy. Assembly Memory logs are read read-only as one possible
source of expected observations; nothing is ever written back.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

UNTRUSTED = ("registration_invalid", "view_mismatch", "input_error")


def sha256_file(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def load_json(path: str | Path, what: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {what} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{what} {path} must hold a JSON object")
    return payload


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


def load_detection(path: str | Path) -> dict:
    """Load a change-detection result.json with provenance and region checks."""
    path = Path(path)
    result = load_json(path, "detection result")
    issues: list[str] = []
    status = result.get("status")
    rectified = result.get("rectified") if isinstance(result.get("rectified"), dict) else None
    view = (rectified or {}).get("view") or result.get("view")
    regions = []
    for item in result.get("regions") or []:
        bbox = item.get("bbox_mm")
        centroid = item.get("centroid_mm")
        if not (isinstance(bbox, list) and len(bbox) == 4 and all(_is_number(v) for v in bbox)):
            issues.append(f"region {item.get('label', '?')} dropped: bad bbox_mm")
            continue
        if not (isinstance(centroid, list) and len(centroid) == 2
                and all(_is_number(v) for v in centroid)):
            issues.append(f"region {item.get('label', '?')} dropped: bad centroid_mm")
            continue
        regions.append(item)
    if status not in UNTRUSTED and status not in ("no_change", "single_change", "multiple_changes"):
        issues.append(f"unknown detection status {status!r}; treating regions as untrusted")
        status = "input_error"
    if status in ("single_change", "multiple_changes") and not regions:
        issues.append("detection reports changes but no usable regions survived validation")
    ppm = (rectified or {}).get("px_per_mm") if rectified else None
    provenance = {"result_json": str(path), "result_sha256": sha256_file(path),
                  "pair_id": result.get("pair_id") or path.parent.name,
                  "status": status, "view": view,
                  "px_per_mm": ppm if _is_number(ppm) else None,
                  "before": result.get("before"), "after": result.get("after"),
                  "warnings": list(result.get("warnings") or [])}
    trusted = status not in UNTRUSTED and rectified is not None and bool(view)
    files = result.get("output_files") or {}
    return {"result": result, "pair_dir": path.parent, "regions": regions,
            "provenance": provenance, "trusted": trusted, "issues": issues,
            "rectified_files": {
                "before": files.get("before_rectified", "before_rectified.jpg"),
                "after": files.get("after_rectified", "after_rectified.jpg")}}


def load_frame_meta(path: str | Path | None):
    """Optional pixels-per-mm metadata for direct image pairs."""
    if path is None:
        return None
    meta = load_json(path, "frame metadata")
    ppm = meta.get("px_per_mm")
    if not _is_number(ppm) or ppm <= 0 or ppm > 1000:
        raise ValueError(f"frame metadata {path}: px_per_mm must be in (0, 1000]")
    return {"px_per_mm": float(ppm), "view": meta.get("view"),
            "origin": meta.get("origin"), "provenance": str(path)}


def _expected_item(raw: dict, index: int) -> dict:
    ident = raw.get("id")
    if not isinstance(ident, str) or not ident.strip():
        raise ValueError(f"expected entry {index}: id must be a non-empty string")
    bbox = raw.get("bbox_mm")
    if not (isinstance(bbox, list) and len(bbox) == 4 and all(_is_number(v) for v in bbox)):
        raise ValueError(f"expected entry {ident!r}: bbox_mm must be 4 finite numbers")
    if bbox[2] <= 0 or bbox[3] <= 0:
        raise ValueError(f"expected entry {ident!r}: bbox_mm must have positive size")
    frame = raw.get("frame")
    if not isinstance(frame, str) or not frame.strip():
        raise ValueError(f"expected entry {ident!r}: frame must be a non-empty string")
    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"expected entry {ident!r}: source must name the evidence")
    return {"id": ident.strip(), "bbox_mm": [float(v) for v in bbox],
            "frame": frame.strip(), "source": source.strip(),
            "note": raw.get("note")}


def _from_memory_log(path: Path) -> list[dict]:
    """Read observation events from a memory.jsonl file. Read-only."""
    items = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"memory log {path} line {lineno}: invalid JSON") from exc
        if not isinstance(event, dict) or event.get("type") != "observation":
            continue
        bbox = event.get("bbox_mm") or []
        centroid = event.get("centroid_mm") or []
        if len(bbox) != 4 or not all(_is_number(v) for v in bbox):
            continue
        evidence = event.get("evidence") or {}
        items.append({"id": str(event.get("id") or f"line-{lineno}"),
                      "bbox_mm": [float(v) for v in bbox],
                      "frame": str(event.get("source_view") or "unknown"),
                      "source": f"{path}:{event.get('id')}",
                      "note": f"memory observation; pair {evidence.get('pair_id')}; "
                              f"region {evidence.get('region')}"})
    return items


def load_expected(path: str | Path | None) -> tuple[list[dict], dict]:
    """Expected-component observations: our schema file or a memory.jsonl."""
    if path is None:
        return [], {"source": None, "entries": 0}
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"expected file not found: {path}")
    if path.suffix == ".jsonl" or path.name == "memory.jsonl":
        items = _from_memory_log(path)
        return items, {"source": str(path), "entries": len(items), "kind": "memory_log"}
    payload = load_json(path, "expected inventory")
    raw = payload.get("expected")
    if not isinstance(raw, list):
        raise ValueError(f"expected file {path}: 'expected' must be a list")
    items = [_expected_item(entry, index) for index, entry in enumerate(raw)]
    seen = set()
    for item in items:
        if item["id"] in seen:
            raise ValueError(f"expected file {path}: duplicate id {item['id']!r}")
        seen.add(item["id"])
    return items, {"source": str(path), "entries": len(items), "kind": "expected_file",
                   "coverage": payload.get("coverage", "unknown")}


def load_associations(path: str | Path | None) -> list[dict]:
    """Possible component associations (e.g. from fingerprint matching)."""
    if path is None:
        return []
    payload = load_json(path, "associations")
    raw = payload.get("associations")
    if not isinstance(raw, list):
        raise ValueError(f"associations file {path}: 'associations' must be a list")
    items = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"associations file {path} entry {index}: must be an object")
        region = entry.get("region")
        candidate = entry.get("candidate_id")
        score = entry.get("score")
        source = entry.get("source")
        if not isinstance(region, str) or not region.strip():
            raise ValueError(f"associations file {path} entry {index}: bad region")
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError(f"associations file {path} entry {index}: bad candidate_id")
        if not _is_number(score) or not 0.0 <= score <= 1.0:
            raise ValueError(f"associations file {path} entry {index}: score must be in [0, 1]")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"associations file {path} entry {index}: bad source")
        items.append({"region": region.strip(), "candidate_id": candidate.strip(),
                      "score": float(score), "source": source.strip()})
    return items


def load_confirmations(path: str | Path | None) -> list[dict]:
    """External operator confirmations. The only route to confirmed_missing."""
    if path is None:
        return []
    payload = load_json(path, "confirmations")
    raw = payload.get("confirmations")
    if not isinstance(raw, list):
        raise ValueError(f"confirmations file {path}: 'confirmations' must be a list")
    items = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"confirmations file {path} entry {index}: must be an object")
        target = entry.get("expected_id")
        by = entry.get("confirmed_by")
        statement = entry.get("statement")
        if not isinstance(target, str) or not target.strip():
            raise ValueError(f"confirmations file {path} entry {index}: bad expected_id")
        if not isinstance(by, str) or not by.strip():
            raise ValueError(f"confirmations file {path} entry {index}: bad confirmed_by")
        if statement not in ("missing", "present"):
            raise ValueError(f"confirmations file {path} entry {index}: "
                             "statement must be 'missing' or 'present'")
        items.append({"expected_id": target.strip(), "confirmed_by": by.strip(),
                      "statement": statement})
    return items
