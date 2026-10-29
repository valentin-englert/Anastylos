"""S3 of slices.py: real next_action proposals on one synthetic episode, arbitrated by the l2sim validator.

Separate process because l2sim's `session` module shadows tools/session/session.py (used by the live core).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIVE / "l2sim"))

import wiring  # noqa: E402,F401  (sets the providers path)
import episodes  # noqa: E402
from meta import MetaProposer  # noqa: E402
from policies import ProposerPolicy  # noqa: E402
from run import CappedProposer  # noqa: E402
from simulator import INTEGRITY, simulate  # noqa: E402

REPLICATION_SEED = 7  # outside the preregistered 0-2, so the slice never pre-spends the L2 arm's requests


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cap", type=int, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    proposer = MetaProposer(args.out / "provider", max_calls=args.cap, environ={} if args.offline else None)
    episode = episodes.synthetic("stale_revisions", 1)
    policy = ProposerPolicy(CappedProposer(proposer, args.cap), "TL-slice-S3")
    result = simulate(episode, policy, REPLICATION_SEED)
    decisions = [{k: e.get(k) for k in ("turn", "provider_status", "model_action", "validator_verdict",
                                        "executed_by", "executed_action")} for e in result["trace"]
                 if "executed_by" in e]
    integrity = {name: result["counters"][name] for name in INTEGRITY}
    report = {"episode": episode.id, "decisions": decisions, "integrity": integrity,
              "executed_by": {k: sum(d["executed_by"] == k for d in decisions) for k in ("policy", "p0_fallback", "none")},
              "provider": [{"status": c["result"]["status"], "latency_s": c["result"]["latency_s"],
                            "usage": c["result"]["usage"]} for c in policy.calls],
              "ok": all(v == 0 for v in integrity.values())}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "l2_slice.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
