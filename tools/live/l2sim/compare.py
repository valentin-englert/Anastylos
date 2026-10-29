"""Apply the PREREG §7 acceptance rule to three finished runs and write comparison.json beside the L2 run.

    python compare.py --p0 <run dir> --pr <run dir> --l2 <run dir>
"""

import argparse
import json
from pathlib import Path

from metrics import accept, paired


def _load(run_dir, arm):
    document = json.loads((Path(run_dir) / "results.json").read_text(encoding="utf-8"))
    if document["arm"] != arm:
        raise SystemExit(f"{run_dir} is arm {document['arm']}, expected {arm}")
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for arm in ("p0", "pr", "l2"):
        parser.add_argument(f"--{arm}", type=Path, required=True)
    args = parser.parse_args(argv)
    p0, pr, l2 = _load(args.p0, "P0"), _load(args.pr, "PR"), _load(args.l2, "L2")
    keys = [{(r["episode"], r["replication_seed"]) for r in d["episode_runs"]} for d in (p0, pr, l2)]
    if not keys[0] == keys[1] == keys[2]:
        raise SystemExit("runs cover different (episode, seed) sets; the comparison must be paired")
    if len({d["provenance"]["prereg_sha256"] for d in (p0, pr, l2)}) != 1:
        raise SystemExit("runs were made under different PREREG versions")
    comparison = {
        "schema": "anastylos-l2sim-comparison@1",
        "runs": {"P0": str(args.p0), "PR": str(args.pr), "L2": str(args.l2)},
        "resolved_per_interruption": {d["arm"]: d["summary"]["resolved_per_interruption"] for d in (p0, pr, l2)},
        "paired_l2_vs_p0": paired(l2["episode_runs"], p0["episode_runs"]),
        "paired_l2_vs_pr": paired(l2["episode_runs"], pr["episode_runs"]),
        "acceptance": accept(l2["summary"], p0["summary"], pr["summary"], l2["complete"]),
    }
    (Path(args.l2) / "comparison.json").write_text(json.dumps(comparison, indent=1), encoding="utf-8")
    print(json.dumps(comparison, indent=1))
    return comparison


if __name__ == "__main__":
    main()
