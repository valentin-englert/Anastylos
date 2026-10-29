"""Import change-detection result.json files as observations. Standard library only.

The importer reads detection output verbatim: source view from
``rectified.view``, coordinates straight from each region, provenance from
the file itself. It never renames, merges, or interprets regions.
"""

from __future__ import annotations

import json
from pathlib import Path

from anastylos_memory.store import ValidationError, evidence_ref

UNTRUSTED_STATUSES = ("registration_invalid", "view_mismatch", "input_error")


def evidence_key(evidence: dict) -> tuple:
    """Stable identity of imported evidence: file bytes when known, else the cited path."""
    source = evidence.get("result_sha256") or f"path:{evidence.get('result_json')}"
    return (source, evidence.get("pair_id"), evidence.get("region"))


def load_result(path: str | Path) -> dict:
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read result file {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise ValidationError(f"result file {path} is not a JSON object")
    return result


def observations_from_result(
    result: dict,
    result_path: str | Path,
    session: str,
    region: str | None = None,
    kit_sha256: str | None = None,
    note: str | None = None,
    t_s: float | None = None,
) -> list[dict]:
    """Build observation events (without id/timestamp) from a result dict."""
    status = result.get("status")
    if status in UNTRUSTED_STATUSES:
        raise ValidationError(f"result status {status!r} has no trustworthy coordinates; refusing import")
    view = (result.get("rectified") or {}).get("view")
    if not view:
        raise ValidationError("result has no rectified view; refusing import")
    regions = result.get("regions") or []
    if region is not None:
        regions = [item for item in regions if item.get("label") == region]
        if not regions:
            raise ValidationError(f"region {region!r} not found in result {result.get('pair_id')}")
    pair_id = result.get("pair_id") or Path(str(result_path)).parent.name
    return [
        {
            "type": "observation",
            "session": session,
            "source_view": view,
            "bbox_mm": list(item.get("bbox_mm") or []),
            "centroid_mm": list(item.get("centroid_mm") or []),
            "evidence": evidence_ref(str(result_path), pair_id, item.get("label", "")),
            "kit_sha256": kit_sha256,
            "note": note,
            **({} if t_s is None else {"t_s": t_s}),
            "supersedes": None,
        }
        for item in regions
    ]
