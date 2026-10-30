"""Conformance check of a coordinator journal against CONTRACT §3.4 (fields required since 0.2).

    python tools/live/integration/journal_check.py <journal.jsonl>

Returns the list of problems (empty when conformant). Extra fields are allowed; required keys must be present
(their value may be null where the contract marks them static or LIVE-01-only).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA = "anastylos-live-journal@1"
EVERY = ("schema", "seq", "kind", "t_wall")
STATIC = ("slot", "question_kind", "cell", "deposit_window_s", "removal_window_s", "source_box_mm", "deposit_t_wall")
REQUIRED = {
    "session_start": ("live_session_id",),
    "question_state": ("op_key", "question_id", "revision", "state") + STATIC,
    "intent": ("op_key", "planned"),
    "memory_result": ("op_key", "status", "written_ids"),
    "provider_call": ("op_key", "task", "result"),
    "fallback": ("op_key", "task", "reason"),
    "recovery": (),
    "done": (),
    "ui_event": ("action",),
}
ON_STATE = {
    "ASKED": ("wording", "text", "anchor_possible", "anchor_ref", "die_ordinal"),
    "ANSWERED": ("input",),
    "CONFIRMED": ("resolved",),
}
RESOLVED = ("outcome", "source_box_mm", "destination", "place_text")
OUTCOMES = ("source", "destination", "no_part")


def problems(records: list[dict]) -> list[str]:
    found = []
    ok_results: dict[str, int] = {}
    for n, r in enumerate(records, 1):
        where = f"record {n} ({r.get('kind')})"
        found += [f"{where}: missing {k!r}" for k in EVERY if k not in r]
        if r.get("schema") != SCHEMA:
            found.append(f"{where}: schema {r.get('schema')!r}")
        if r.get("kind") not in REQUIRED:
            found.append(f"{where}: unknown kind")
            continue
        found += [f"{where}: missing {k!r}" for k in REQUIRED[r["kind"]] if k not in r]
        if r["kind"] == "question_state":
            found += [f"{where} {r.get('state')}: missing {k!r}" for k in ON_STATE.get(r.get("state"), ()) if k not in r]
            if r.get("state") == "CONFIRMED":
                resolved = r.get("resolved") or {}
                found += [f"{where}: resolved lacks {k!r}" for k in RESOLVED if k not in resolved]
                if resolved.get("outcome") not in OUTCOMES:
                    found.append(f"{where}: resolved.outcome {resolved.get('outcome')!r}")
        if r["kind"] == "memory_result" and r.get("status") == "ok":
            ok_results[r["op_key"]] = ok_results.get(r["op_key"], 0) + 1
    found += [f"op_key {k}: {v} memory_result records with status ok (one per commit)" for k, v in ok_results.items() if v > 1]
    return found


def check_file(path: Path) -> list[str]:
    records = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return problems(records)


if __name__ == "__main__":
    issues = check_file(Path(sys.argv[1]))
    print(json.dumps({"conformant": not issues, "problems": issues}, indent=1))
    raise SystemExit(1 if issues else 0)
