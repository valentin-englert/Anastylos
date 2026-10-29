"""Human review records: separate, append-only files; inspections are never edited."""

from __future__ import annotations

from pathlib import Path

from anastylos_microscopy import REVIEW_SCHEMA
from anastylos_microscopy.util import atomic_write_json, parse_utc, read_json, sha256_file, utc_now

DECISIONS = ("confirmed", "rejected", "needs_recapture", "inconclusive")


def write_review(inspection_path, *, operator: str, decision: str, identification: str | None = None,
                 notes: str | None = None, evidence: list[str] | None = None, clock: str | None = None,
                 supersedes: str | None = None) -> Path:
    inspection_path = Path(inspection_path)
    record = read_json(inspection_path)
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if not operator or not operator.strip():
        raise ValueError("operator name is required for a review")
    if decision == "confirmed" and not (identification and identification.strip()):
        raise ValueError("a confirmed review must state the confirmed identification")
    stamp = parse_utc(clock) if clock else utc_now()
    reviews_dir = inspection_path.parent / "reviews"
    reviews_dir.mkdir(exist_ok=True)
    index = len(list(reviews_dir.glob("review-*.json"))) + 1
    path = reviews_dir / f"review-{index:03d}.json"
    if path.exists():
        raise ValueError(f"refusing to overwrite {path}")
    atomic_write_json(path, {
        "schema": REVIEW_SCHEMA,
        "inspection_id": record["inspection_id"],
        "inspection_sha256": sha256_file(inspection_path),
        "inspection_digest": record["digest"]["value"],
        "reviewed_utc": stamp,
        "operator": operator.strip(),
        "decision": decision,
        "confirmed_identification": identification if decision == "confirmed" else None,
        "operator_evidence": evidence or [],
        "notes": notes,
        "supersedes": supersedes,
        "note": "Operator statement. Assembly Memory is not written by the Core; any import is a "
                "separate, explicit, operator-run step.",
    })
    return path
