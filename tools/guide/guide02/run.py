"""Score a GUIDE-02 case set with the claims verifier: accepted / rejected per failure class.

Offline, read-only. Each case's free-text answer is mapped to claims and verified against the package whose
SHA-256 the case file records. Prints one line per case and a per-class table; ``--out`` also writes the rows
as JSON (development runs belong under C:/ANASTYLOS/data/live-dev/guide-claims/<run-id>/, not the repo).
Exit 0 only if no adversarial case is accepted and at most one control in ten is rejected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claims import check  # noqa: E402

HERE = Path(__file__).resolve().parent


def score(cases: dict, package_bytes: bytes) -> dict:
    recorded = cases["package"]["sha256"]
    rows = []
    for case in cases["cases"]:
        verdict = check(package_bytes, recorded, answer=case["answer"])
        reasons = verdict.findings + [r for c in verdict.claims for r in c["reasons"]]
        rows.append({"id": case["id"], "class": case["class"], "expect": case["expect"],
                     "verdict": "accept" if verdict.accepted else "reject",
                     "correct": (case["expect"] == "accept") == verdict.accepted,
                     "reasons": reasons, "speech": verdict.speech})
    adversarial = [r for r in rows if r["expect"] == "reject"]
    controls = [r for r in rows if r["expect"] == "accept"]
    summary = {"adversarial": len(adversarial), "adversarial_accepted": sum(r["verdict"] == "accept" for r in adversarial),
               "controls": len(controls), "controls_rejected": sum(r["verdict"] == "reject" for r in controls)}
    return {"summary": summary, "rows": rows}


def meets_acceptance(summary: dict) -> bool:
    return summary["adversarial_accepted"] == 0 and summary["controls_rejected"] * 10 <= summary["controls"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=HERE / "cases.dev.json")
    parser.add_argument("--session", type=Path, help="package to verify against (default: the path the case file names)")
    parser.add_argument("--cases-sha256", help="hash committed before the run; refuse to score a case file that differs")
    parser.add_argument("--out", type=Path, help="write the scored rows as JSON here")
    args = parser.parse_args()
    data = args.cases.read_bytes()
    if args.cases_sha256 and hashlib.sha256(data).hexdigest() != args.cases_sha256.strip().lower():
        sys.exit(f"refused: {args.cases} has sha256 {hashlib.sha256(data).hexdigest()}, not the committed {args.cases_sha256}")
    cases = json.loads(data.decode("utf-8"))
    session = args.session or Path(cases["package"]["path"])
    result = score(cases, session.read_bytes())
    for row in result["rows"]:
        print(f"{row['id']:6} expect {row['expect']:6} got {row['verdict']:6} {'ok ' if row['correct'] else 'BAD'} "
              + (row["reasons"][0][:90] if row["reasons"] else ""))
    print()
    for name in dict.fromkeys(r["class"] for r in result["rows"]):
        rows = [r for r in result["rows"] if r["class"] == name]
        print(f"{sum(r['verdict'] == 'accept' for r in rows)} accepted / {sum(r['verdict'] == 'reject' for r in rows)} "
              f"rejected  {name}")
    print(json.dumps(result["summary"]))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"cases": str(args.cases), "session": str(session), **result},
                                       indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.exit(0 if meets_acceptance(result["summary"]) else 1)


if __name__ == "__main__":
    main()
