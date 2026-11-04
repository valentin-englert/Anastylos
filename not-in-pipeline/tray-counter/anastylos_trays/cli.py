"""CLI: assess detection pairs against configured tray regions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anastylos_trays.annotate import annotate_pair
from anastylos_trays.config import ConfigError, load_config
from anastylos_trays.counter import analyze_pair, find_results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="count", description="ANASTYLOS automatic tray counter")
    parser.add_argument("--config", required=True, help="tray configuration JSON")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pairs-dir", default=None, help="detection output with <pair>/result.json")
    source.add_argument("--result-json", default=None, help="single pair result.json")
    parser.add_argument("--out", default="tray_out", help="output directory")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 2
    if args.result_json:
        targets = [Path(args.result_json)]
    else:
        targets = find_results(Path(args.pairs_dir))
        if not targets:
            print(f"no <pair>/result.json under {args.pairs_dir}")
            return 1
    out = Path(args.out)
    answers = []
    for target in targets:
        if not target.is_file():
            print(f"missing result file {target}")
            return 1
        try:
            answer = analyze_pair(target, config)
        except ValueError as exc:
            print(str(exc))
            return 1
        pair_out = out / answer["pair_id"]
        pair_out.mkdir(parents=True, exist_ok=True)
        overlay = annotate_pair(target.parent, answer, config, pair_out / "tray_overlay.png")
        answer["overlay"] = str(overlay) if overlay else None
        (pair_out / "tray_result.json").write_text(json.dumps(answer, indent=2) + "\n", encoding="utf-8")
        answers.append(answer)
    summary = {"pairs": len(answers),
               "verdicts": [tray["verdict"] for answer in answers for tray in answer["trays"]],
               "config": str(Path(args.config).resolve())}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0
