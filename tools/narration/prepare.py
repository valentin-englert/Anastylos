"""Build the frozen input bundle for an advisory Muse narration of a custody run.

Offline. Reads custody-events@1, custody-links@1 and (optionally) a
PROTO-01 score file, keeps only what a narration may cite, and writes
bundle.json (with the SHA-256 of every source) plus prompt.txt. The
network call is a separate step; nothing here contacts a service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

RULES = """You write a short plain-language narration of a disassembly custody run for a demo.
Rules, all mandatory:
1. Use only facts in the JSON bundle below. Never add parts, times, cells, sites or causes.
2. Cite every factual sentence with its ids in square brackets, e.g. [L-0001, E-0017].
3. Keep every status exactly as given. "candidate_temporal_exclusive" is a proposal that still needs
   operator confirmation, never a confirmed fact. "ambiguous" and "unattributed" stay unresolved;
   never guess a source for them.
4. Never claim that a part was identified or recognised as an individual instance.
5. Times in seconds exactly as in the bundle. At most 180 words. Language: {language}.
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_bundle(events_path: Path, custody_path: Path, score_path: Path | None) -> dict:
    events = json.loads(events_path.read_text(encoding="utf-8"))
    custody = json.loads(custody_path.read_text(encoding="utf-8"))
    sources = {"events": {"path": str(events_path), "sha256": sha256(events_path)},
               "custody": {"path": str(custody_path), "sha256": sha256(custody_path)}}
    bundle = {
        "schema": "anastylos-narration-bundle@1",
        "events": [{k: e[k] for k in ("id", "zone", "kind", "t_before_s", "t_after_s") if k in e}
                   for e in events["events"] if e["kind"] != "reference_set"],
        "links": custody["proposals"],
        "identity": custody["identity"],
        "scope_note": custody["scope_note"],
    }
    if score_path:
        score = json.loads(score_path.read_text(encoding="utf-8"))
        bundle["score"] = {k: score[k] for k in ("outcome", "counts", "controls", "claim_boundary") if k in score}
        sources["score"] = {"path": str(score_path), "sha256": sha256(score_path)}
    bundle["sources"] = sources
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--custody", required=True, type=Path)
    parser.add_argument("--score", type=Path)
    parser.add_argument("--language", default="German")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    bundle = build_bundle(args.events, args.custody, args.score)
    bundle_text = json.dumps(bundle, indent=1, ensure_ascii=False)
    (args.out / "bundle.json").write_text(bundle_text, encoding="utf-8")
    prompt = RULES.format(language=args.language) + "\nJSON bundle:\n" + bundle_text
    (args.out / "prompt.txt").write_text(prompt, encoding="utf-8")
    print(f"prompt.txt sha256 {hashlib.sha256(prompt.encode('utf-8')).hexdigest()}, {len(prompt)} chars")


if __name__ == "__main__":
    main()
