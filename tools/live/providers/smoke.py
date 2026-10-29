"""Bounded real smoke run against the Meta Model API: one synthetic request per task (4 calls, cap 8).

Writes traces and recorded fixtures under `--out` (default `C:\\ANASTYLOS\\data\\live-dev\\C\\<run-id>`) and
copies the fixtures, with a MANIFEST.json, into `tests/fixtures/recorded/`. Inputs are synthetic only.
The key comes from the process environment (MODEL_API_KEY) and is never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from meta import MetaProposer  # noqa: E402

SMOKE_CALL_CAP = 8
RECORDED_DIR = Path(__file__).resolve().parent / "tests" / "fixtures" / "recorded"

SYNTHETIC_PAYLOADS = {
    "question_wording": {
        "op_key": "SMOKE-1:Q-1:1", "kind": "source_of_part", "mode": "open",
        "facts": {"cell": "4", "phase": "Rückseite", "previous_part": "das Teil aus Zelle 3"},
        "l0_text": "Rückseite: Was hast du nach dem Teil aus Zelle 3 herausgenommen und in Zelle 4 gelegt?",
    },
    "answer_interpretation": {
        "op_key": "SMOKE-1:Q-2:1", "kind": "source_of_part", "allowed_choices": ["cand-1", "cand-2"],
        "question_text": "Woher kam das Teil in Zelle 4? Eins: links oben. Zwei: rechts unten.",
        "operator_text": "Das war das zweite, rechts unten.",
    },
    "next_action": {
        "state": {"open_questions": [{"id": "Q-3", "revision": 2, "kind": "source_of_part", "candidates": 2,
                                      "deposit_age_s": 14, "label": "label#1"}],
                  "operator": {"busy": False}, "budgets": {"questions_left": 5, "prompts_left": 2}},
        "valid_actions": [
            {"action": "ask", "question_id": "Q-3", "question_revision": 2},
            {"action": "defer", "question_id": "Q-3", "until": "next_pause"},
        ],
    },
    "guide_claims": {
        "package_sha256": "0" * 64,
        "operator_question": "Wo liegt P-03 und was muss vorher rein?",
        "package_excerpt": {"parts": [{"id": "P-03", "stored_in": "Zelle 2", "basis": "operator_declared"},
                                      {"id": "P-01", "stored_in": "Zelle 1", "basis": "automatic"}],
                            "install_order": {"steps": ["P-01", "P-03"],
                                              "basis": "Umkehrung der beobachteten Ausbaureihenfolge"}},
    },
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_fixtures(run_dir: Path) -> list[dict]:
    RECORDED_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for source in sorted((run_dir / "cache").glob("*.json")):
        target = RECORDED_DIR / source.name
        shutil.copyfile(source, target)
        entries.append({"file": source.name, "source": str(source), "sha256": _sha256_file(target)})
    manifest_path = RECORDED_DIR / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"files": []}
    kept = [entry for entry in manifest["files"] if entry["file"] not in {e["file"] for e in entries}]
    manifest = {"schema": "anastylos-fixture-manifest@1", "inputs": "synthetic (providers/smoke.py)",
                "files": sorted(kept + entries, key=lambda entry: entry["file"])}
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    run_id = time.strftime("smoke-%Y%m%dT%H%M%SZ", time.gmtime())
    parser.add_argument("--out", type=Path, default=Path(r"C:\ANASTYLOS\data\live-dev\C") / run_id)
    args = parser.parse_args()

    proposer = MetaProposer(args.out, max_calls=SMOKE_CALL_CAP)
    rows = []
    for task, payload in SYNTHETIC_PAYLOADS.items():
        result = proposer.propose(task, payload, request_id=f"{args.out.name}:{task}")
        usage = result["usage"] or {}
        rows.append({"task": task, "status": result["status"], "model": result["model"],
                     "effort_requested": result["reasoning_effort_requested"],
                     "effort_returned": result["reasoning_effort_returned"], "latency_s": result["latency_s"],
                     "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                     "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
                     "cache_hit": result["cache_hit"], "error": result["error"], "output": result["output"]})
    summary = {"run_dir": str(args.out), "calls_used": proposer.calls_used, "call_cap": SMOKE_CALL_CAP,
               "rows": rows, "recorded": _record_fixtures(args.out)}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if all(row["status"] == "ok" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
