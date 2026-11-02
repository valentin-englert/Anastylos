"""Run the pre-registered cell-items batch (PREREG-items.md). MODEL_API_KEY must be set for this process only.

    python tools/live/dialogue/eval/run_items_batch.py --out <folder outside the repo>
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from dialogue import items as cell_items  # noqa: E402
from dialogue.executor import MuseClient  # noqa: E402

MAX_CALLS = 30


def usable(output: dict | None, text: str) -> list[dict]:
    if not output or output.get("kind") != "answer":
        return []
    return cell_items.check_items(output["items"], text)


def passes(case: dict, output: dict | None, items: list[dict]) -> bool:
    expect = case["expect"]
    if expect == "no_name":
        return not items
    if expect == "meta":
        return bool(output) and output.get("kind") == "meta" or not items and bool(output)
    got = [[i["name"].lower(), i["count"], i["origin"]] for i in items]
    want = [[n.lower(), c, o] for n, c, o in expect]
    return got == want


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = json.loads((HERE / "cases-items.json").read_text(encoding="utf-8"))["cases"]
    client = MuseClient(max_calls=MAX_CALLS, retries=0, timeout_s=8.0, usage_log=args.out / "usage.jsonl")
    summary = {}
    for effort in ("minimal", "low"):
        rows = []
        for case in cases:
            res = client.call("extract_items", {"cell": "cell 3", "answer_text": case["text"]}, effort, case["id"])
            found = usable(res["output"], case["text"])
            rows.append({"id": case["id"], "text": case["text"], "status": res["status"], "output": res["output"],
                         "usable": found, "ok": res["status"] == "ok" and passes(case, res["output"], found),
                         "latency_s": round(res["latency_s"], 3)})
        times = sorted(r["latency_s"] for r in rows if r["status"] == "ok")
        p95 = times[min(len(times) - 1, int(round(0.95 * (len(times) - 1) + 0.4999)))] if times else None
        summary[effort] = {"passed": sum(r["ok"] for r in rows), "total": len(rows),
                           "not_ok_status": [r["id"] for r in rows if r["status"] != "ok"],
                           "rejected_by_check": [r["id"] for r in rows if r["status"] == "ok" and r["output"]["kind"] == "answer"
                                                 and r["output"]["items"] and not r["usable"]],
                           "latency_median_s": round(statistics.median(times), 3) if times else None,
                           "latency_p95_s": p95, "rows": rows}
    summary["calls_used"] = client.calls_used
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({e: {k: v for k, v in summary[e].items() if k != "rows"} for e in ("minimal", "low")}
                     | {"calls_used": client.calls_used}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
