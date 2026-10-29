"""LAT-0: per-stage wall-clock timing of the unchanged tools/session/session.py.

Imports the production module, wraps its subprocess runner with a timer and calls its own main().
No pipeline file is modified; every output (runs, memory) goes under --out in the scratchpad.

    python lat0_harness.py --video V --phase P --out DIR [--label L]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\HP\Documents\Anastylos")
sys.path.insert(0, str(REPO / "tools" / "session"))
import session  # noqa: E402  (production module, unchanged)

STEPS: list[dict] = []
_original_run = session._run


def _stage(args: list) -> str:
    names = [Path(str(a)).name for a in args]
    if "custody.py" in names:
        return "custody." + str(args[names.index("custody.py") + 1])
    if "detect.py" in names:
        return "change-detection.run"
    if "memory.py" in names:
        rest = [str(a) for a in args[names.index("memory.py") + 1:] if not str(a).startswith("--") and not Path(str(a)).exists()]
        return "memory." + "-".join(rest[:2])
    return "other"


def timed_run(args: list, cwd: Path | None = None) -> str:
    start = time.perf_counter()
    try:
        return _original_run(args, cwd)
    finally:
        STEPS.append({"stage": _stage(args), "seconds": round(time.perf_counter() - start, 3)})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    run_dir, memory_dir = args.out / "run", args.out / "memory"
    session._run = timed_run
    sys.argv = ["session.py", "--video", str(args.video), "--phase", args.phase,
                "--custody-dir", str(REPO / "custody-ledger"), "--config", "custody-config.v15.json",
                "--memory", str(memory_dir), "--out", str(run_dir)]
    started = time.perf_counter()
    session.main()
    total = time.perf_counter() - started
    events = json.loads((run_dir / "pass2" / "events.json").read_text(encoding="utf-8"))
    video = events["provenance"]["video"]
    duration = (video.get("frame_count") or 0) / (video.get("fps") or 1)
    stages: dict[str, float] = {}
    for step in STEPS:
        stages[step["stage"]] = round(stages.get(step["stage"], 0.0) + step["seconds"], 3)
    report = {"schema": "anastylos-lat0@1", "label": args.label, "phase": args.phase, "video": str(args.video),
              "video_sha256": video.get("sha256"), "video_duration_s": duration,
              "total_s": round(total, 3), "stages_s": stages, "steps": STEPS,
              "ratio_total_to_video": round(total / duration, 3) if duration else None,
              "host": {"python": platform.python_version(), "machine": platform.processor(), "cpus": os.cpu_count()},
              "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (args.out / "lat0.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("label", "video_duration_s", "total_s", "stages_s", "ratio_total_to_video")}))


if __name__ == "__main__":
    main()
