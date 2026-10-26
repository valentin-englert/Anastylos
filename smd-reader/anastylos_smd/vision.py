"""SMD image pipeline: pcb-ocr observations interpreted as markings.

Reuses the pcb-ocr backend, preprocessing, and orientation search through
its public `analyze` entry point, then decodes every observed word as a
resistor marking with explicit OCR-confusion alternatives. The OCR text
is an observation; the resistor reading built on it is a hypothesis that
requires the object to actually be a resistor.
"""

from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pcb-ocr"))

from anastylos_pcb.pipeline import analyze as ocr_analyze

from anastylos_smd.decode import decode

CONFUSIONS = {"0": ("0", "O"), "O": ("O", "0"), "1": ("1", "I", "L"),
              "I": ("I", "1"), "L": ("L", "1"), "5": ("5", "S"),
              "S": ("S", "5"), "8": ("8", "B"), "B": ("B", "8")}
MAX_SUBSTITUTED_POSITIONS = 3
MAX_VARIANTS = 12

OBJECT_NOTE = ("A syntactically valid code does not prove this object is a resistor; "
               "the resistance reading is conditional on that operator-verified hypothesis.")


def variants(raw_text: str) -> list[dict]:
    """Raw code plus OCR-confusion alternatives, cheapest first."""
    cleaned = (raw_text or "").strip().upper().replace(" ", "")
    spots = [i for i, ch in enumerate(cleaned) if ch in CONFUSIONS][:MAX_SUBSTITUTED_POSITIONS]
    out: dict[str, list] = {}
    for combo in product(*[CONFUSIONS[cleaned[i]] for i in spots]):
        chars = list(cleaned)
        subs = []
        for i, ch in zip(spots, combo):
            chars[i] = ch
            if ch != cleaned[i]:
                subs.append({"position": i, "from": cleaned[i], "to": ch})
        fixed = "".join(chars)
        if fixed not in out or len(subs) < len(out[fixed]):
            out[fixed] = subs
    ordered = sorted(out.items(), key=lambda item: (len(item[1]), item[0]))
    return [{"code": code, "subs": subs, "repaired": bool(subs)} for code, subs in ordered[:MAX_VARIANTS]]


def interpret_words(words: list[dict], image_name: str, aliases=None) -> dict:
    """Decode OCR word observations as SMD markings. No OpenCV needed."""
    observations = []
    for word in words:
        readings = []
        for variant in variants(word["raw_text"]):
            decoded = decode(variant["code"], aliases=aliases)
            for candidate in decoded["candidates"]:
                readings.append({**candidate, "variant": variant["code"],
                                 "subs": variant["subs"], "repaired": variant["repaired"]})
        observations.append({
            "raw_text": word["raw_text"], "bbox_xywh": list(word["bbox_xywh"]),
            "ocr_confidence": word.get("ocr_confidence"),
            "orientations_seen": word.get("orientations_seen", [0]),
            "readings": readings,
        })
    combined: dict[tuple, dict] = {}
    for item in observations:
        for reading in item["readings"]:
            key = (reading["variant"], reading["resistance_ohms_exact"])
            if key not in combined:
                combined[key] = {
                    "code": reading["variant"], "schemes": reading["schemes"],
                    "resistance_ohms_exact": reading["resistance_ohms_exact"],
                    "resistance_ohms": reading["resistance_ohms"],
                    "explanation": reading["explanation"],
                    "tolerance_percent": None, "tolerance_context": reading["tolerance_context"],
                    "repaired": reading["repaired"], "subs": reading["subs"],
                    "ocr_confidence": item["ocr_confidence"],
                    "bbox_xywh": item["bbox_xywh"],
                    "orientations_seen": list(item["orientations_seen"]),
                    "occurrences": 1,
                    "evidence": {"image": image_name, "raw_text": item["raw_text"]},
                }
            else:
                kept = combined[key]
                kept["occurrences"] += 1
                kept["orientations_seen"] = sorted(set(kept["orientations_seen"]) | set(item["orientations_seen"]))
    candidates = sorted(combined.values(),
                        key=lambda c: (c["repaired"], -(c["ocr_confidence"] or 0)))
    ohms = {c["resistance_ohms_exact"] for c in candidates}
    if not candidates:
        status, reason = "unknown", "no observed text decodes as a marking"
    elif len(ohms) > 1:
        status, reason = "ambiguous", "several readings with different values"
    elif candidates[0]["repaired"]:
        status, reason = "ambiguous", "raw text does not decode; reading needs OCR repair (see subs)"
    else:
        status, reason = "ok", None
    return {"status": status, "reason": reason, "candidates": candidates,
            "observations": observations,
            "object_hypothesis": {"assumed_object": "resistor (operator assertion)",
                                  "note": OBJECT_NOTE}}


def analyze(image, backend, roi: tuple[int, int, int, int] | None = None,
            aliases=None, image_name: str = "") -> dict:
    """Run OCR through pcb-ocr and interpret the words as SMD markings."""
    ocr = ocr_analyze(image, backend, roi)
    base = {"backend": ocr["backend"], "preprocessing": ocr.get("preprocessing")}
    if ocr["status"] == "unavailable":
        return {**base, "status": "unavailable", "reason": ocr.get("reason"),
                "candidates": [], "observations": [],
                "object_hypothesis": {"assumed_object": "resistor (operator assertion)",
                                      "note": OBJECT_NOTE}}
    words = [{"raw_text": item["raw_text"], "bbox_xywh": item["bbox_xywh"],
              "ocr_confidence": item["ocr_confidence"],
              "orientations_seen": item.get("orientations_seen", [0])}
             for item in ([*ocr.get("candidates", []), *ocr.get("ignored_text", [])])]
    if not words:
        return {**base, "status": "unknown", "reason": "OCR returned no text",
                "candidates": [], "observations": [],
                "object_hypothesis": {"assumed_object": "resistor (operator assertion)",
                                      "note": OBJECT_NOTE}}
    return {**base, **interpret_words(words, image_name, aliases)}
