"""Run one arm over the preregistered episodes and write results.json (+ calls.jsonl for L2) to --out.

    python run.py --arm P0 --out C:\\ANASTYLOS\\data\\live-dev\\D\\<run>
    python run.py --arm L2 --proposer <module:factory> --max-calls 910 --out ...

`--proposer` is wired here only (dependency injection); the simulator never imports a provider. A real provider
module must be importable (e.g. PYTHONPATH=tools/live). The factory receives `call_cap` and `run_dir` if it
accepts them; the hard call cap is enforced here regardless.
"""

import argparse
import hashlib
import importlib
import inspect
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import episodes
import fakes
from metrics import summarize
from policies import P0Policy, ProposerPolicy, RandomValidPolicy
from simulator import simulate

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DEFAULT_DATA = Path(r"C:\ANASTYLOS\data\session-runs-v21\FULL-01")
DEFAULT_MAX_CALLS = 910   # PREREG v2: turn-limit bound (792) + retry bound (117) over the 39 episode-runs


class CappedProposer:
    """Hard per-run call cap: beyond it the inner proposer is never called again."""

    def __init__(self, inner, cap):
        self._inner, self._cap = inner, cap
        self.calls = 0
        self.exhausted = False

    def propose(self, task, payload, request_id):
        if self.calls >= self._cap:
            self.exhausted = True
            return fakes.provider_result(request_id, task, payload, "budget_exhausted")
        self.calls += 1
        return self._inner.propose(task, payload, request_id)


def load_proposer(spec, call_cap, run_dir):
    if spec == "fake" or spec.startswith("fake:"):
        return fakes.make(spec.partition(":")[2] or "good")
    module_name, _, factory_name = spec.partition(":")
    if not factory_name:
        raise SystemExit("--proposer must be fake, fake:<good|random|adversarial> or <module:factory>")
    factory = getattr(importlib.import_module(module_name), factory_name)
    offered = {"call_cap": call_cap, "run_dir": run_dir}
    accepted = inspect.signature(factory).parameters
    return factory(**{k: v for k, v in offered.items() if k in accepted})


def select_episodes(which, data):
    if which == "primary":
        return episodes.primary(data)
    if which == "full01":
        return episodes.load_full01(data)
    if which == "synthetic":
        return [episodes.synthetic(f, s) for f in episodes.FAMILIES for s in episodes.GENERATOR_SEEDS]
    raise SystemExit(f"unknown episode set {which}")


def provenance():
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True,
                              check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=HERE, capture_output=True,
                                    text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError) as error:
        head, dirty = f"unavailable: {error}", None
    return {"git_head": head, "l2sim_dirty": dirty, "prereg_sha256": sha(HERE / "PREREG.md"),
            "sources_sha256": {p.name: sha(p) for p in sorted(HERE.glob("*.py"))}}


def check_out_dir(out):
    out = out.resolve()
    if out == REPO or REPO in out.parents:
        raise SystemExit(f"--out must be outside the repository (CONTRACT §2): {out}")
    if (out / "results.json").exists():
        raise SystemExit(f"{out / 'results.json'} exists; choose a new run directory")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm", required=True, choices=("P0", "PR", "L2"))
    parser.add_argument("--proposer", default="fake")
    parser.add_argument("--seeds", default="0,1,2", help="replication seeds (oracle and PR)")
    parser.add_argument("--episodes", default="primary", choices=("primary", "full01", "synthetic"))
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="FULL-01 v21 run root (read-only)")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help="hard proposer call cap per run")
    parser.add_argument("--retry-limit", type=int, default=3, help="provider retries per episode")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    out = check_out_dir(args.out)
    seeds = [int(s) for s in args.seeds.split(",")]
    selected = select_episodes(args.episodes, args.data)
    run_id = out.name
    capped = None
    if args.arm == "P0":
        policy = P0Policy()
    elif args.arm == "PR":
        policy = RandomValidPolicy()
    else:
        out.mkdir(parents=True, exist_ok=True)
        capped = CappedProposer(load_proposer(args.proposer, args.max_calls, out), args.max_calls)
        policy = ProposerPolicy(capped, run_id, retry_limit=args.retry_limit)

    results = [simulate(episode, policy, seed) for seed in seeds for episode in selected]
    complete = not (capped and capped.exhausted) and not getattr(policy, "stopped_status", None)
    document = {
        "schema": "anastylos-l2sim-results@1",
        "run_id": run_id,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "argv": sys.argv[1:] if argv is None else list(argv),
        "arm": args.arm,
        "proposer": args.proposer if args.arm == "L2" else None,
        "seeds": seeds,
        "episode_set": args.episodes,
        "episodes": [{"id": e.id, "source": e.source, "questions": len(e.questions),
                      "provenance": e.provenance} for e in selected],
        "limits": {"max_calls": args.max_calls, "retry_limit_per_episode": args.retry_limit},
        "complete": complete,
        "proposer_calls_made": capped.calls if capped else 0,
        "provider_stopped_status": getattr(policy, "stopped_status", None),
        "provenance": provenance(),
        "summary": summarize(results),
        "summary_by_source": {source: summarize([r for r in results if r["source"] == source])
                              for source in sorted({r["source"] for r in results})},
        "episode_runs": results,
    }
    out.mkdir(parents=True, exist_ok=True)
    if isinstance(policy, ProposerPolicy):
        with (out / "calls.jsonl").open("w", encoding="utf-8") as handle:
            for call in policy.calls:
                handle.write(json.dumps(call, ensure_ascii=False, sort_keys=True) + "\n")
    (out / "results.json").write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
    s = document["summary"]
    print(f"{args.arm}: runs={s['episode_runs']} resolved={s['resolved_parts']} "
          f"interruptions={s['totals']['interruptions']} resolved/interruption={s['resolved_per_interruption']} "
          f"invalid_rate={s['invalid_action_rate']} integrity_ok={s['integrity_ok']} complete={complete} -> {out}")
    return document


if __name__ == "__main__":
    main()
