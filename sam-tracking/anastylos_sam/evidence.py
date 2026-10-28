"""sam-evidence-v1: versioned, additive request evidence. Stdlib only.

One directory per API request::

    <out>/<request_id>/
      evidence.json      # this schema, self-describing
      transcript.jsonl   # raw Responses SSE event dicts, one per line
      masks/mask_<frame>_<object>.png  # binary rasters (0/255)

Coordinates are source/display pixels (origin top-left, +x right,
+y down) in the ``w``/``h`` space the response reports. Boxes are
half-open ``[left, right) x [top, bottom)`` as normalized by the
official parser. No millimetres: no calibration is cited for the
SNES footage, so none are computed.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from . import EVIDENCE_SCHEMA, MODEL_ID, PARSER_COMMIT, PARSER_VERSION
from .util import atomic_write_bytes, atomic_write_json, sha256_bytes, sha256_file

RATES_SOURCE = "https://dev.meta.ai/docs/pricing-rate-limits#sam-pricing"
RATES = {
    "image_usd_each": 0.0025,          # $2.50 per 1,000 images
    "video_usd_per_1k_frames": 0.20,   # $0.20 per 1,000 frames
    "video_min_frames_billed": 50,
    "retrieved_utc": "2026-10-28",
    "source": RATES_SOURCE,
}
COST_BASIS = "ESTIMATED_FROM_PUBLIC_RATES"


def estimate_cost(*, media_kind: str, frames: int) -> dict[str, Any]:
    if media_kind == "image":
        billed, usd = 1, RATES["image_usd_each"]
    elif media_kind == "video":
        billed = max(int(frames), RATES["video_min_frames_billed"])
        usd = round(billed / 1000 * RATES["video_usd_per_1k_frames"], 6)
    else:
        raise ValueError(f"unknown media_kind {media_kind!r}")
    return {"units_billed": billed, "usd": usd, "basis": COST_BASIS,
            "rates": dict(RATES)}


def new_request_id(prefix: str = "samq") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def build_record(
    *,
    request_id: str,
    created_utc: str,
    prompt: dict[str, Any],
    media: dict[str, Any],
    params: dict[str, Any],
    response: dict[str, Any],
    usage: dict[str, Any],
    records: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    tool_versions: dict[str, Any],
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "request_id": request_id,
        "created_utc": created_utc,
        "model": MODEL_ID,
        "prompt": prompt,
        "media": media,
        "params": params,
        "response": response,
        "usage": usage,
        "records": records,
        "diagnostics": diagnostics,
        "provenance": {
            "parser": f"meta-sam-parser {PARSER_VERSION}",
            "parser_commit": PARSER_COMMIT,
            "tool_versions": tool_versions,
        },
        "notes": list(notes or []),
    }


def write_request(
    out_dir: Path,
    record: dict[str, Any],
    *,
    transcript_events: list[dict[str, Any]],
    mask_pngs: dict[str, bytes],
) -> Path:
    """Write evidence.json + transcript.jsonl + masks/. Returns the dir."""
    if record.get("schema") != EVIDENCE_SCHEMA:
        raise ValueError("refusing to write a record with an unknown schema id")
    request_dir = out_dir / record["request_id"]
    masks_dir = request_dir / "masks"
    mask_refs: dict[str, dict[str, str]] = {}
    for name, png in sorted(mask_pngs.items()):
        target = masks_dir / name
        atomic_write_bytes(target, png)
        mask_refs[name] = {"sha256": sha256_bytes(png), "bytes": len(png)}
    transcript_path = request_dir / "transcript.jsonl"
    lines = "".join(json.dumps(event) + "\n" for event in transcript_events)
    atomic_write_bytes(transcript_path, lines.encode("utf-8"))
    record["files"] = {
        "transcript": {"path": "transcript.jsonl",
                       "sha256": sha256_bytes(lines.encode("utf-8")),
                       "events": len(transcript_events)},
        "masks": mask_refs,
    }
    atomic_write_json(request_dir / "evidence.json", record)
    return request_dir


def verify_request(request_dir: str | Path) -> dict[str, Any]:
    """Re-hash committed bytes; return {ok, problems[]}."""
    problems: list[str] = []
    request_dir = Path(request_dir)
    try:
        with open(request_dir / "evidence.json", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError) as exc:
        return {"ok": False, "problems": [f"evidence.json unreadable: {exc}"]}
    if record.get("schema") != EVIDENCE_SCHEMA:
        problems.append(f"schema is {record.get('schema')!r}, want {EVIDENCE_SCHEMA!r}")
    files = record.get("files", {})
    transcript = files.get("transcript", {})
    try:
        actual = sha256_file(request_dir / "transcript.jsonl")
        if actual != transcript.get("sha256"):
            problems.append("transcript.jsonl hash mismatch")
    except OSError as exc:
        problems.append(f"transcript.jsonl missing: {exc}")
    for name, ref in (files.get("masks") or {}).items():
        try:
            actual = sha256_file(request_dir / "masks" / name)
        except OSError:
            problems.append(f"mask {name} missing")
            continue
        if actual != ref.get("sha256"):
            problems.append(f"mask {name} hash mismatch")
    return {"ok": not problems, "problems": problems}
