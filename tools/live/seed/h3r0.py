"""H3-R0: deterministic L0 clarification templates for the FULL-01 v21 questions, plus an automatic H3 rubric.

Offline, read-only. Replays the questions in live order (phase, then deposit time) and lets each template use
only what a live session could have known at that moment:
  - automatic parts of the same phase (the segment run produced them together with the question),
  - operator answers to *earlier* questions of the same phase (deposit window ended before this one started),
  - never the question's own answer, never EXTRA parts (they were declared only at the end of the offline round).
Phase words are TL-supplied from docs/FULL-01.md section 2 (a live session would ask the operator for them).

    python h3r0.py --v21 C:/ANASTYLOS/data/session-runs-v21/FULL-01 --out DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

PHASE_WORDS = {  # TL-supplied, docs/FULL-01.md §2 and §7; NOT operator words
    "P1": "beim Abschrauben der Rückseite",
    "P2": "beim Öffnen des Gehäuses",
    "P3": "beim Ausbau aus der Vorderschale",
}
MAX_SPOKEN_CANDIDATES = 3
NEAR_MM = 12.0
NUMBER_WORDS = ["eins", "zwei", "drei", "vier", "fünf"]


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _jsonl(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def centroid(points):
    pts = [p for p in points if p]
    return [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)] if pts else None


def direction(frm, to) -> str:
    """Camera-image direction of `to` seen from `frm` (sheet mm, +x right, +y down). Orientation to the operator is unknown."""
    dx, dy = to[0] - frm[0], to[1] - frm[1]
    if (dx * dx + dy * dy) ** 0.5 <= NEAR_MM:
        return "direkt an"
    horiz = "rechts" if dx > 0 else "links"
    vert = "unterhalb" if dy > 0 else "oberhalb"
    if abs(dx) > 2 * abs(dy):
        return f"{horiz} von"
    if abs(dy) > 2 * abs(dx):
        return f"{vert}"
    return f"{vert} und {horiz} von"


def part_words(anchor) -> str:
    if anchor["basis"] == "automatic":
        return f"dem Teil aus Zelle {anchor['cell']}"
    return f"dem Teil „{anchor['label']}“"


def place_words(anchor) -> str:
    if anchor["basis"] == "automatic":
        return f"der Stelle, aus der das Teil in Zelle {anchor['cell']} kam"
    return f"der Stelle von „{anchor['label']}“"


def describe_candidate(cand, anchors_geo) -> str:
    c = centroid([s.get("centroid_mm") for s in cand["sources"]])
    if c is None or not anchors_geo:
        return None  # not describable in words: the option goes to the screen, never into speech
    best = min(anchors_geo, key=lambda a: (a["xy"][0] - c[0]) ** 2 + (a["xy"][1] - c[1]) ** 2)
    rel = direction(best["xy"], c)
    if rel == "direkt an":
        return f"direkt an {place_words(best)}"
    return f"{rel} {place_words(best)}, im Kamerabild"


def build(v21: Path):
    memory = _jsonl(v21 / "memory" / "memory.jsonl")
    # answers.py prints the result list followed by a summary line; read only the JSON value
    applied, _ = json.JSONDecoder().raw_decode((v21 / "audit" / "apply_results.json").read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in memory}
    phase_tag = {e["id"]: e["label"] for e in memory if e["type"] == "phase"}
    answer_of = {r["question"]: r.get("declared_removal") for r in applied if r.get("question") not in (None, "EXTRA")}
    dismissed = {r["question"] for r in applied if r.get("dismissed")}
    storage = {e["target_id"]: e["location"] for e in memory if e["type"] == "storage_link"}
    links = {e["observation_id"]: f"cell-{e['tray']['number']}" for e in memory if e["type"] == "tray_link"}

    questions = []
    for tag in ("P1", "P2", "P3"):
        parts = _json(v21 / tag / "parts.json")
        for q in _json(v21 / tag / "questions.json")["questions"]:
            questions.append({**q, "phase_tag": tag, "parts_run": parts})
    questions.sort(key=lambda q: (q["phase_tag"], (q.get("deposit_window_s") or q.get("window_s"))[0]))

    results = []
    for q in questions:
        tag = q["phase_tag"]
        window = q.get("deposit_window_s") or q.get("window_s")
        # automatic parts of this phase (known when the segment's questions appear)
        anchors = []
        for p in q["parts_run"]["parts"]:
            mem = (p.get("memory") or {})
            if mem.get("status") == "written":
                obs = by_id[mem["observation"]]
                anchors.append({"ref": obs["id"], "basis": "automatic", "cell": links[obs["id"]].split("-")[1],
                                "t": obs["t_s"], "xy": obs["centroid_mm"]})
        # operator answers to earlier questions of this phase only
        for earlier in questions:
            if earlier is q or earlier["phase_tag"] != tag:
                continue
            ew = earlier.get("deposit_window_s") or earlier.get("window_s")
            dec_id = answer_of.get(earlier["id"])
            if ew[1] < window[0] and dec_id:
                dec = by_id[dec_id]
                chosen = next((c for c in earlier.get("candidates", []) if c["removal_event"] in (dec.get("source") or "")), None)
                anchors.append({"ref": dec_id, "basis": "operator_declared", "label": dec["operator_label"],
                                "t": dec.get("t_s"), "cell": None,
                                "xy": centroid([s.get("centroid_mm") for s in chosen["sources"]]) if chosen else None})
        before = sorted([a for a in anchors if a["t"] is not None and a["t"] < window[0]], key=lambda a: a["t"])
        prev = before[-1] if before else None
        # geometric anchors only from the past: a live question at t must not refer to later removals
        geo = [a for a in anchors if a.get("xy") and a["t"] is not None and a["t"] < window[0]]
        phase = PHASE_WORDS[tag]
        cands = q.get("candidates", [])
        if q["kind"] == "source_of_part":
            cell = q["cell"].split("-")[1]
            same_cell = any(p["cell"] == q["cell"] and p["deposit_window_s"][0] < window[0] for p in q["parts_run"]["parts"])
            what = ("ebenfalls " if same_cell else "") + f"in Zelle {cell}"
            lead = (f"{cap(phase)}: Was hast du nach {part_words(prev)} herausgenommen und {what} gelegt?"
                    if prev else f"{cap(phase)}: Woher kam das Teil, das du als Erstes {what} gelegt hast?")
            described = [describe_candidate(c, geo) for c in cands]
            speakable = (0 < len(cands) <= MAX_SPOKEN_CANDIDATES and all(d is not None for d in described)
                         and len(set(described)) == len(described))
            if not cands:
                options, mode = [], "open"
            elif speakable:
                options = [f"{NUMBER_WORDS[i]}: {d}" for i, d in enumerate(described)]
                mode = "spoken_options"
            else:
                options, mode = [], "open_with_screen_options"
            tail = (" Möglich wäre: " + "; ".join(options) + ". Oder sag es mit eigenen Worten." if options
                    else (" Die möglichen Stellen sind auf dem Bildschirm markiert." if mode == "open_with_screen_options" else ""))
            text = lead + tail + f" (abgelegt bei etwa {mmss(window[0])})"
        else:
            where = describe_candidate({"sources": q["sources"]}, geo)
            at = f" {where}" if where else ""
            lead = (f"{cap(phase)}: Nach {part_words(prev)} hast du etwas{at} herausgenommen, "
                    "aber in keine Zelle gelegt. Wohin hast du es gelegt?") if prev else \
                   (f"{cap(phase)}: Du hast etwas{at} herausgenommen, aber in keine Zelle gelegt. Wohin?")
            text, mode = lead + f" (bei etwa {mmss(window[0])})", "destination"
        results.append({"id": q["id"], "phase": tag, "kind": q["kind"], "mode": mode, "old_text": q["ask"],
                        "l0_text": text, "anchor": prev["ref"] if prev else None,
                        "anchor_basis": prev["basis"] if prev else None, "candidates": len(cands),
                        "outcome_in_v21": "dismissed (not a part)" if q["id"] in dismissed else
                                          ("answered" if answer_of.get(q["id"]) else "open")})
    return results


MM = re.compile(r"\d+(?:[.,]\d+)?\s?mm\b|\b\d{2,3}/\d{2,3}\b")
MACHINE_ID = re.compile(r"\b(?:cell-\d+|[ELQ]-\d{4}|SRC-\d+|obs-\d+|dec-\d+|Q-P\d)\b")
TIME = re.compile(r"\d+[.,]\d+\s?s\b|\d+:\d{2}")
PHASE = re.compile(r"\bbeim\b", re.IGNORECASE)
YESNO_SINGLE = re.compile(r"\bKam es (?:aus|von)\b|\bWar es\b", re.IGNORECASE)


def rubric(text: str, anchor_possible: bool, has_anchor: bool) -> dict:
    main, _, support = text.partition("(")
    checks = {
        "R1_no_mm": not MM.search(text),
        "R2_phase_in_words": bool(PHASE.search(text)),
        "R3_anchor_when_possible": has_anchor or not anchor_possible,
        "R4_time_only_supporting": not TIME.search(main),
        "R5_no_single_candidate_suggestion": not YESNO_SINGLE.search(text),
        "R6_no_machine_ids": not MACHINE_ID.search(text),
        "R7_spoken_part_at_most_40_words": len(main.split()) <= 40,
    }
    checks["pass"] = all(checks.values())
    return checks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v21", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261030)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = build(args.v21)
    for r in rows:
        r["rubric_old"] = rubric(r["old_text"], anchor_possible=r["anchor"] is not None, has_anchor=False)
        r["rubric_l0"] = rubric(r["l0_text"], anchor_possible=r["anchor"] is not None, has_anchor=r["anchor"] is not None)
        r["words_l0"] = len(r["l0_text"].split())
    summary = {"questions": len(rows),
               "old_pass": sum(r["rubric_old"]["pass"] for r in rows),
               "l0_pass": sum(r["rubric_l0"]["pass"] for r in rows),
               "with_anchor": sum(r["anchor"] is not None for r in rows),
               "modes": {m: sum(r["mode"] == m for r in rows) for m in sorted({r["mode"] for r in rows})},
               "max_words_l0": max(r["words_l0"] for r in rows)}
    (args.out / "h3r0.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    # blind rating sheet for the owner: order of the two wordings randomised per question; key sealed by hash
    rng = random.Random(args.seed)
    sheet, key = [], {}
    for r in rows:
        pair = [("old", r["old_text"]), ("l0", r["l0_text"])]
        rng.shuffle(pair)
        key[r["id"]] = [p[0] for p in pair]
        sheet.append(f"## {r['id']}\nA: {pair[0][1]}\nB: {pair[1][1]}\nBewertung (A/B je 1-5: verständlich ohne Video / klingt wie ein Mensch / nennt nichts vor): \n")
    key_bytes = (json.dumps({"seed": args.seed, "key": key}, sort_keys=True) + "\n").encode("utf-8")
    (args.out / "rating-key.SEALED.json").write_bytes(key_bytes)
    (args.out / "rating-sheet.md").write_text("# H3-R0 Bewertungsbogen (blind)\n\n" + "\n".join(sheet), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print("rating key sha256", hashlib.sha256(key_bytes).hexdigest())


if __name__ == "__main__":
    main()
