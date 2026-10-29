"""Read-only supplemental observation adapter (stdlib only, file contracts only).

Builds an ``anastylos-supplemental-view@1`` projection from a validated
spatial sidecar plus its validator report: refs, time interval, region,
epoch association, current observations, geometry eligibility, and
explicit unknowns. Memory subjects resolve by id from a read-only
``memory.jsonl`` parse -- this module never imports Assembly Memory code
and never writes to the log (the additive ``supplemental_evidence``
event does not exist yet; until the contract owner freezes it, views
stay external projections, never Memory integration).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

SUPPLEMENTAL_SCHEMA_ID = "anastylos-supplemental-view@1"


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_memory_events(memory_jsonl: str | Path) -> tuple[dict[str, dict], str]:
    """Read-only parse of a memory log. Returns (events by id, bytes sha256)."""
    raw = Path(memory_jsonl).read_bytes()
    events: dict[str, dict] = {}
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if isinstance(event, dict) and event.get("id"):
            events[event["id"]] = event
    return events, _sha256_bytes(raw)


def _sorted_claims(sidecar: dict, geometry_eligible: bool) -> tuple[dict, list[str]]:
    """Sort interpretations by basis; unqualified geometry becomes unknown."""
    buckets = {"observations": [], "measurements": [], "advisory": [], "testimony": []}
    unknowns: list[str] = []
    for item in sidecar.get("interpretations") or []:
        if not isinstance(item, dict):
            continue
        entry = {"claim": item.get("claim"), "basis": item.get("basis"),
                 "state": item.get("state"), "region_ref": item.get("region_ref")}
        basis = item.get("basis")
        if basis == "visual_observation":
            buckets["observations"].append(entry)
        elif basis == "geometric_measurement":
            if geometry_eligible:
                buckets["measurements"].append(entry)
            else:
                unknowns.append(f"unqualified_geometry: {item.get('claim')}")
        elif basis in ("model_inference", "model_assumption"):
            buckets["advisory"].append(entry)
        elif basis == "operator_testimony":
            buckets["testimony"].append(entry)
        else:
            unknowns.append(f"unrecognized_basis: {item.get('claim')}")
    return buckets, unknowns


def build_view(sidecar: dict, sidecar_sha256: str, report: dict, report_sha256: str,
               events_by_id: dict[str, dict], memory_sha256: str) -> dict:
    """Project one sidecar + validator report onto a supplemental view."""
    pose = sidecar.get("pose") or {}
    time = sidecar.get("capture_time") or {}
    target = sidecar.get("target") or {}
    roi = target.get("requested_roi") or {}
    relation = sidecar.get("observer_relation") or {}
    geometry_eligible = bool(report.get("metric_ready")) and pose.get("validity") == "valid"

    subject_ref = target.get("subject_ref")
    if subject_ref is None:
        memory_subject: dict = {"ref": None, "status": "unknown_subject"}
    elif subject_ref in events_by_id:
        found = events_by_id[subject_ref]
        memory_subject = {"ref": subject_ref, "status": "linked",
                          "memory_type": found.get("type"),
                          "operator_label": found.get("operator_label")}
    else:
        memory_subject = {"ref": subject_ref, "status": "unresolved_subject"}

    buckets, unknowns = _sorted_claims(sidecar, geometry_eligible)
    unknowns.extend([
        "part_identity_unknown: this view links a memory subject, it never identifies a part",
        "history_unknown: a later image never establishes an earlier transfer",
        "hidden_geometry_unknown: unobserved surfaces stay absent, never defaulted",
    ])
    association = relation.get("association")
    if association in ("unknown", "ambiguous", None):
        unknowns.append(f"epoch_join_unknown: association={association}")
    if memory_subject["status"] != "linked":
        unknowns.append(f"subject_unresolved: {memory_subject['status']}")
    if time.get("exposure_utc_estimate") is None or time.get("uncertainty_ms") is None:
        unknowns.append("clock_unknown: no exposure estimate or uncertainty")

    return {
        "schema": SUPPLEMENTAL_SCHEMA_ID,
        "view_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{SUPPLEMENTAL_SCHEMA_ID}:{sidecar_sha256}")),
        "evidence_id": sidecar.get("evidence_id"),
        "sidecar_sha256": sidecar_sha256,
        "report_sha256": report_sha256,
        "report_verdict": report.get("verdict"),
        "report_reasons": list(report.get("reasons") or []),
        "session_id": sidecar.get("session_id"),
        "source_id": sidecar.get("source_id"),
        "request_id": sidecar.get("request_id"),
        "request_revision": sidecar.get("request_revision"),
        "attempt_id": sidecar.get("attempt_id"),
        "time_interval": {"exposure_utc_estimate": time.get("exposure_utc_estimate"),
                          "uncertainty_ms": time.get("uncertainty_ms")},
        "region": {"frame": roi.get("frame"), "vertices_mm": roi.get("vertices_mm"),
                   "basis": roi.get("basis")},
        "epoch_association": {"association": association, "epoch_id": relation.get("epoch_id")},
        "geometry_eligible": geometry_eligible,
        "pose_summary": {"validity": pose.get("validity"), "solve_source": pose.get("solve_source")},
        **buckets,
        "memory_subject": memory_subject,
        "memory_sha256": memory_sha256,
        "custody_claims": [],
        "custody_note": "Supplemental views carry no custody claims: no removal, deposit, route, or transfer is asserted.",
        "unknowns": unknowns,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a read-only supplemental view (never writes to memory).")
    parser.add_argument("--sidecar", required=True)
    parser.add_argument("--report", required=True, help="validator report JSON for the sidecar")
    parser.add_argument("--memory", required=True, help="memory.jsonl (read-only)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    try:
        sidecar_raw = Path(args.sidecar).read_bytes()
        sidecar = json.loads(sidecar_raw.decode("utf-8"))
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        events, memory_before = load_memory_events(args.memory)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load inputs: {exc}", file=sys.stderr)
        sys.exit(2)

    view = build_view(sidecar, _sha256_bytes(sidecar_raw), report,
                      _sha256_bytes(Path(args.report).read_bytes()), events, memory_before)
    raw = (json.dumps(view, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    Path(args.out).write_bytes(raw)

    _events_after, memory_after = load_memory_events(args.memory)
    print(f"view sha256 {_sha256_bytes(raw)}")
    print(json.dumps({"view_id": view["view_id"], "memory_sha256_before": memory_before,
                      "memory_sha256_after": memory_after,
                      "memory_unchanged": memory_before == memory_after}))
    if memory_before != memory_after:
        print("error: memory log changed during a read-only projection", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
