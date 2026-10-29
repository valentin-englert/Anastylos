"""L0 clarification wording (no model) and the question rubric R1–R7.

Ported from ``seed/h3r0.py`` (measured 11/12 on FULL-01 v21) for one question at a time in a live
session. A template may only use what was known when the question is raised:

- automatic parts of the same phase that are in memory with a tray link,
- operator answers already *committed* in this live session to earlier questions of the same phase
  (their deposit window ended before this one started); never later removals,
- phase words supplied by the operator setup (never invented here).

Candidates that an earlier committed answer already chose are marked as claimed (the Q-P2-02
finding) and are not spoken as options; they stay on the screen with their mark.
"""

from __future__ import annotations

import re

MAX_SPOKEN_CANDIDATES = 3
NEAR_MM = 12.0
NUMBER_WORDS = ["eins", "zwei", "drei", "vier", "fünf"]


class MissingPhaseWords(ValueError):
    """The operator setup did not supply words for a phase; the template does not invent them."""


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
    return f"dem Teil „{anchor['label']}“" if anchor["label"] else "dem zuvor genannten Teil"


def place_words(anchor) -> str:
    if anchor["basis"] == "automatic":
        return f"der Stelle, aus der das Teil in Zelle {anchor['cell']} kam"
    return f"der Stelle von „{anchor['label']}“" if anchor["label"] else "der zuvor genannten Stelle"


def describe_candidate(cand, anchors_geo) -> str | None:
    c = centroid([s.get("centroid_mm") for s in cand["sources"]])
    if c is None or not anchors_geo:
        return None  # not describable in words: the option goes to the screen, never into speech
    best = min(anchors_geo, key=lambda a: (a["xy"][0] - c[0]) ** 2 + (a["xy"][1] - c[1]) ** 2)
    rel = direction(best["xy"], c)
    if rel == "direkt an":
        return f"direkt an {place_words(best)}"
    return f"{rel} {place_words(best)}, im Kamerabild"


def _window(question: dict) -> list[float]:
    return question.get("deposit_window_s") or question.get("window_s")


def automatic_anchors(parts_report: dict, memory_events: list[dict]) -> list[dict]:
    """Parts of a phase that ``session.py`` wrote with an observation and a tray link."""
    by_id = {e["id"]: e for e in memory_events}
    cells = {e["observation_id"]: e["tray"]["number"] for e in memory_events if e["type"] == "tray_link"}
    anchors = []
    for part in parts_report["parts"]:
        mem = part.get("memory") or {}
        obs = by_id.get(mem.get("observation"))
        if mem.get("status") == "written" and obs is not None and obs["id"] in cells:
            anchors.append({"ref": obs["id"], "basis": "automatic", "cell": str(cells[obs["id"]]),
                            "t": obs.get("t_s"), "xy": obs.get("centroid_mm")})
    return anchors


def operator_anchors(question: dict, questions: dict, committed: list[dict]) -> list[dict]:
    """Committed answers to earlier questions of the same phase (their window ended before this one began)."""
    window = _window(question)
    anchors = []
    for c in committed:
        earlier = questions.get(c["question_id"])
        if earlier is None or earlier["id"] == question["id"] or earlier["phase_tag"] != question["phase_tag"]:
            continue
        if _window(earlier)[1] >= window[0]:
            continue
        chosen = next((k for k in earlier.get("candidates", []) if k["removal_event"] == c.get("choice")), None)
        anchors.append({"ref": c["declared_removal"], "basis": "operator_declared", "label": c["label"], "t": c.get("t_s"),
                        "cell": None, "xy": centroid([s.get("centroid_mm") for s in chosen["sources"]]) if chosen else None})
    return anchors


def build_prompt(question: dict, parts_report: dict, phase_words: dict, anchors: list[dict], claimed: dict[str, str]) -> dict:
    """L0 wording of one question. ``claimed``: removal event -> label of the committed answer that chose it."""
    tag = question["phase_tag"]
    if not str(phase_words.get(tag) or "").strip():
        raise MissingPhaseWords(f"no phase words for {tag}; the operator setup must supply them")
    phase = phase_words[tag]
    window = _window(question)
    past = sorted([a for a in anchors if a["t"] is not None and a["t"] < window[0]], key=lambda a: a["t"])
    prev = past[-1] if past else None
    # geometric anchors only from the past: a live question at t must not refer to later removals
    geo = [a for a in past if a.get("xy")]
    cands = question.get("candidates", [])
    screen = []
    if question["kind"] == "source_of_part":
        cell = question["cell"].split("-")[1]
        same_cell = any(p["cell"] == question["cell"] and p["deposit_window_s"][0] < window[0] for p in parts_report["parts"])
        what = ("ebenfalls " if same_cell else "") + f"in Zelle {cell}"
        lead = (f"{cap(phase)}: Was hast du nach {part_words(prev)} herausgenommen und {what} gelegt?"
                if prev else f"{cap(phase)}: Woher kam das Teil, das du als Erstes {what} gelegt hast?")
        described = [describe_candidate(c, geo) for c in cands]
        open_idx = [i for i, c in enumerate(cands) if c["removal_event"] not in claimed]
        spoken = [described[i] for i in open_idx]
        speakable = (0 < len(open_idx) <= MAX_SPOKEN_CANDIDATES and all(d is not None for d in spoken)
                     and len(set(spoken)) == len(spoken))
        number_of = {i: NUMBER_WORDS[n] for n, i in enumerate(open_idx)} if speakable else {}
        for i, c in enumerate(cands):
            screen.append({"token": chr(ord("A") + i), "removal_event": c["removal_event"], "description": described[i],
                           "spoken_number": number_of.get(i), "claimed_by": claimed.get(c["removal_event"])})
        if not cands:
            mode, tail = "open", ""
        elif speakable:
            mode = "spoken_options"
            tail = " Möglich wäre: " + "; ".join(f"{number_of[i]}: {described[i]}" for i in open_idx) + ". Oder sag es mit eigenen Worten."
        else:
            mode, tail = "open_with_screen_options", " Die möglichen Stellen sind auf dem Bildschirm markiert."
        text = lead + tail + f" (abgelegt bei etwa {mmss(window[0])})"
        facts = {"phase_words": phase, "cell": cell, "time": mmss(window[0]),
                 "options": [s["description"] for s in screen if s["spoken_number"]]}
    else:
        where = describe_candidate({"sources": question["sources"]}, geo)
        at = f" {where}" if where else ""
        lead = (f"{cap(phase)}: Nach {part_words(prev)} hast du etwas{at} herausgenommen, "
                "aber in keine Zelle gelegt. Wohin hast du es gelegt?") if prev else \
               (f"{cap(phase)}: Du hast etwas{at} herausgenommen, aber in keine Zelle gelegt. Wohin?")
        text, mode = lead + f" (bei etwa {mmss(window[0])})", "destination"
        facts = {"phase_words": phase, "time": mmss(window[0]), "where": where}
    if prev:
        facts["anchor"] = part_words(prev)
    facts["labels"] = sorted({a["label"] for a in anchors if a.get("label")})
    facts["claimed_labels"] = sorted(set(claimed.values()))
    return {"question_id": question["id"], "kind": question["kind"], "mode": mode, "text": text, "screen_options": screen,
            "anchor": prev["ref"] if prev else None, "anchor_basis": prev["basis"] if prev else None,
            "anchor_possible": prev is not None, "facts": facts}


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
