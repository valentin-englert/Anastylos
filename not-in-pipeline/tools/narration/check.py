"""Deterministic provenance check of a narration against its frozen bundle.

Offline. Rejects a narration that cites an id the bundle does not hold,
names a cell, site or time the bundle does not hold, or calls a
non-confirmed link confirmed. Exit 0 = accepted, 1 = rejected.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ID = re.compile(r"\b[LE]-\d{4}\b")
CELL = re.compile(r"\b(?:cell|Zelle)[ -](\d{1,2})\b", re.IGNORECASE)
SITE = re.compile(r"\bW-[A-Z/]+\b|\bH\d\b")
SECONDS = re.compile(r"\b(\d+(?:[.,]\d+)?)\s?s\b")
CONFIRMED = re.compile(r"\b(confirmed|bestätigt|identifiziert|identified|recognised|recognized|erkannt als)\b", re.IGNORECASE)


def known_facts(bundle: dict) -> dict:
    ids, cells, sites, times = set(), set(), set(), set()
    for event in bundle["events"]:
        ids.add(event["id"])
        zone = event["zone"]
        (cells.add(zone.split("-")[1]) if zone.startswith("cell-") else sites.add(zone))
        times.update(round(float(event[k]), 1) for k in ("t_before_s", "t_after_s") if event.get(k) is not None)
    for link in bundle["links"]:
        ids.add(link["id"])
        ids.add(link["deposit"]["id"])
        if "removal" in link:
            ids.add(link["removal"]["event"])
            sites.add(link["removal"]["site"])
    return {"ids": ids, "cells": cells, "sites": sites, "times": times}


def problems(text: str, bundle: dict) -> list[str]:
    facts = known_facts(bundle)
    found = []
    found += [f"unknown id {i}" for i in sorted(set(ID.findall(text)) - facts["ids"])]
    found += [f"unknown cell {c}" for c in sorted(set(CELL.findall(text)) - facts["cells"])]
    found += [f"unknown site {s}" for s in sorted(set(SITE.findall(text)) - facts["sites"])]
    for value in SECONDS.findall(text):
        t = round(float(value.replace(",", ".")), 1)
        if all(abs(t - known) > 0.15 for known in facts["times"]):
            found.append(f"unknown time {value} s")
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if CONFIRMED.search(sentence) and not re.search(r"\b(nicht|not|keine?|no|never|nie|unbestätigt|unconfirmed)\b", sentence, re.IGNORECASE):
            found.append(f"claims confirmation or identity: {sentence.strip()[:80]}")
        if sentence.strip() and not ID.search(sentence) and re.search(r"\d", sentence):
            found.append(f"factual sentence without citation: {sentence.strip()[:80]}")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--narration", required=True, type=Path)
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    found = problems(args.narration.read_text(encoding="utf-8"), bundle)
    for problem in found:
        print("REJECT", problem)
    print("ACCEPTED" if not found else f"REJECTED ({len(found)})")
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
