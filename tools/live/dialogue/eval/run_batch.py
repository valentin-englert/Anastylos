"""Run the pre-registered dialogue batch (PREREG.md). MODEL_API_KEY must be set for this process only.

    python tools/live/dialogue/eval/run_batch.py --out <folder outside the repo>
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from dialogue.executor import FIELDS, MuseClient, ground  # noqa: E402

MAX_CALLS = 30
_ARTICLE = re.compile(r"^(?:an?|the)\s+")


def norm(value):
    return None if value is None else _ARTICLE.sub("", value.strip().lower())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    client = MuseClient(max_calls=MAX_CALLS, retries=1, usage_log=args.out / "usage.jsonl")
    summary = {}
    for effort in ("none", "low"):
        rows = []
        for case in cases["choice"]:
            res = client.call("choose_question", {"state": case["state"], "allowed": case["allowed"] + ["none"]}, effort, case["id"])
            picked = (res["output"] or {}).get("question_type")
            rows.append({"id": case["id"], "status": res["status"], "got": picked, "expect": case["expect"],
                         "ok": res["status"] == "ok" and picked == case["expect"], "latency_s": round(res["latency_s"], 3)})
        for case in cases["extract"]:
            res = client.call("extract_fields", {"question_type": case["question_type"], "answer_text": case["text"]}, effort, case["id"])
            out = res["output"] or {}
            grounded, dropped = ground(out, case["text"]) if out else ({}, [])
            ok = res["status"] == "ok" and grounded.get("intent") == case["expect"]["intent"] and all(
                norm(grounded.get(f)) == norm(case["expect"][f]) for f in FIELDS)
            rows.append({"id": case["id"], "status": res["status"], "got": out, "after_grounding": grounded, "ungrounded": dropped,
                         "ok": ok, "latency_s": round(res["latency_s"], 3)})
        latencies = [r["latency_s"] for r in rows if r["status"] == "ok"]
        summary[effort] = {
            "choice_correct": sum(r["ok"] for r in rows if r["id"].startswith("C")), "choice_total": len(cases["choice"]),
            "extract_correct": sum(r["ok"] for r in rows if r["id"].startswith("E")), "extract_total": len(cases["extract"]),
            "ungrounded_outputs": sum(1 for r in rows if r.get("ungrounded")),
            "not_ok_status": [r["id"] for r in rows if r["status"] != "ok"],
            "latency_median_s": round(statistics.median(latencies), 3) if latencies else None,
            "latency_max_s": max(latencies) if latencies else None, "rows": rows}
    summary["calls_used"] = client.calls_used
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({e: {k: v for k, v in summary[e].items() if k != "rows"} for e in ("none", "low")} | {"calls_used": client.calls_used}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
