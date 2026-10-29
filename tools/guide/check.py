"""Deterministic grounding check of guide answers against the session package.

Offline. Reads answers.md (``## Q<n>: question`` headings) and rejects an
answer that cites an id absent from session.json, names a cell or site that
the session does not hold, claims a verified identity, or states something
without either a citation or an explicit "not observed" statement.

Since PROV-01 it also checks each sentence's *relations* against the package:
a cell named next to cited ids must be where one of those parts is stored, a
site named must appear in one of their sources, an operator-declared part must
not be called observed, an automatic part must not be attributed to the
operator, and an automatic link that was concluded by a rule must not be
called observed. Group ids stand for all their members.
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
OBSERVED = re.compile(r"\b(?:beobachtet|gesehen|gefilmt|observed|seen)\b|von der Kamera", re.IGNORECASE)
BY_OPERATOR = re.compile(r"laut Operator|nach Angabe des Operators|according to the operator|operator says", re.IGNORECASE)
GROUP_ID = re.compile(r"\bgrp-\d{4}\b")


def known(package: dict) -> dict:
    text = json.dumps(package)
    return {"ids": set(ID.findall(text)),
            "cells": {m for m in re.findall(r'"cell-(\d{1,2})"', text)},
            "sites": set(SITE.findall(text))}


def relations(package: dict) -> dict:
    """Per cited id: basis, storage cell, source text, whether its link was concluded; groups map to members."""
    parts, groups = {}, {}
    for part in package.get("parts", []):
        stored = part.get("stored_in") or {}
        location = stored.get("location") or ""
        info = {"id": part["id"], "basis": part["basis"], "cell": location[5:] if location.startswith("cell-") else None,
                "source": json.dumps(part.get("source"), ensure_ascii=False), "concluded": bool(stored.get("inference"))}
        parts[part["id"]] = [info]
        if stored.get("record"):
            parts[stored["record"]] = [info]
        if part.get("group"):
            groups.setdefault(part["group"]["id"], []).append(info)
    return {**parts, **groups}


def _asserted(pattern: re.Pattern, sentence: str) -> bool:
    """The pattern occurs without a negation in the few words before it."""
    return any(not NEGATION.search(sentence[max(0, m.start() - 25):m.start()]) for m in pattern.finditer(sentence))


def relation_problems(answer: str, rel: dict) -> list[str]:
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+", answer):
        cited = [info for i in set(ID.findall(sentence)) | set(GROUP_ID.findall(sentence)) for info in rel.get(i, [])]
        if not cited:
            continue
        cells = {info["cell"] for info in cited}
        for cell in sorted(set(CELL.findall(sentence)) - cells):
            found.append(f"cell {cell} is not where {', '.join(sorted({i['id'] for i in cited}))} is stored")
        for site in sorted(set(SITE.findall(sentence))):
            if not any(re.search(rf"\b{re.escape(site)}\b", info["source"]) for info in cited):
                found.append(f"site {site} is not a recorded source of {', '.join(sorted({i['id'] for i in cited}))}")
        if _asserted(OBSERVED, sentence):
            declared = sorted({i["id"] for i in cited if i["basis"] == "operator_declared"})
            if declared:
                found.append(f"operator-declared {', '.join(declared)} described as observed")
            concluded = sorted({i["id"] for i in cited if i["concluded"] and i["cell"] in CELL.findall(sentence)})
            if concluded:
                found.append(f"the link of {', '.join(concluded)} to its cell was concluded by a rule, not observed")
        if _asserted(BY_OPERATOR, sentence) and all(i["basis"] == "automatic" for i in cited):
            found.append(f"automatic {', '.join(sorted({i['id'] for i in cited}))} attributed to the operator")
    return found


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
    package = json.loads(args.session.read_text(encoding="utf-8"))
    facts, rel = known(package), relations(package)
    rejected = 0
    for question, answer in split_answers(args.answers.read_text(encoding="utf-8")):
        found = problems(answer, facts) + relation_problems(answer, rel)
        rejected += bool(found)
        print(("REJECT " if found else "OK     ") + question[:70])
        for problem in found:
            print("    - " + problem)
    sys.exit(1 if rejected else 0)


if __name__ == "__main__":
    main()
