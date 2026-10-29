"""Deterministic grounding check of guide answers against the session package.

Offline. Reads answers.md (``## Q<n>: question`` headings) and rejects an
answer that cites an id absent from session.json, names a cell or site that
the session does not hold, claims a verified identity, or states something
without either a citation or an explicit "not observed" statement.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ID = re.compile(r"\b(?:obs|link|dec|store|grp|phase)-\d{4}\b|\b[LER]-\d{4}\b")
CELL = re.compile(r"\b(?:cell|Zelle)[ -](\d{1,2})\b", re.IGNORECASE)
SITE = re.compile(r"\bW-[A-Z/]+\b|\bH\d+\b")
UNCERTAIN = re.compile(r"nicht beobachtet|not observed|unbekannt|unknown|weiß ich nicht|keine (?:Angabe|Information)", re.IGNORECASE)
IDENTITY = re.compile(r"\b(?:identifiziert|verifiziert|eindeutig erkannt|identified|verified)\b", re.IGNORECASE)
NEGATION = re.compile(r"\b(?:nicht|nie|kein|keine|not|never|no)\b", re.IGNORECASE)


def known(package: dict) -> dict:
    text = json.dumps(package)
    return {"ids": set(ID.findall(text)),
            "cells": {m for m in re.findall(r'"cell-(\d{1,2})"', text)},
            "sites": set(SITE.findall(text))}


def split_answers(markdown: str) -> list[tuple[str, str]]:
    parts = re.split(r"^## Q\d+:\s*(.*)$", markdown, flags=re.MULTILINE)
    return [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]


def problems(answer: str, facts: dict) -> list[str]:
    found = [f"unknown id {i}" for i in sorted(set(ID.findall(answer)) - facts["ids"])]
    found += [f"unknown cell {c}" for c in sorted(set(CELL.findall(answer)) - facts["cells"])]
    found += [f"unknown site {s}" for s in sorted(set(SITE.findall(answer)) - facts["sites"])]
    for sentence in re.split(r"(?<=[.!?])\s+", answer):
        if IDENTITY.search(sentence) and not NEGATION.search(sentence):
            found.append(f"claims verified identity: {sentence[:80]}")
    if not ID.search(answer) and not UNCERTAIN.search(answer):
        found.append("neither a citation nor an explicit 'not observed'")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--answers", required=True, type=Path)
    args = parser.parse_args()
    facts = known(json.loads(args.session.read_text(encoding="utf-8")))
    rejected = 0
    for question, answer in split_answers(args.answers.read_text(encoding="utf-8")):
        found = problems(answer, facts)
        rejected += bool(found)
        print(("REJECT " if found else "OK     ") + question[:70])
        for problem in found:
            print("    - " + problem)
    sys.exit(1 if rejected else 0)


if __name__ == "__main__":
    main()
