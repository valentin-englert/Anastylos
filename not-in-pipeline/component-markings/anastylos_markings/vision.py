"""Component-marking image pipeline: pcb-ocr words read as markings.

Reuses the pcb-ocr backend, preprocessing, and orientation search through
its public `analyze` entry point, groups observed words into text lines,
and parses each line with deterministic notation rules. Raw OCR text is
an observation; parsed values and transcribed identities built on it
never become verified specifications.

`assistant` is an optional provider-agnostic advisor: a local callable
taking the observed words and returning JSON-serializable notes. Its
output is recorded verbatim and never changes a verdict. No cloud
service is called; there is no built-in assistant.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pcb-ocr"))

from anastylos_pcb.pipeline import analyze as ocr_analyze

from anastylos_markings.parse import interpret_crop

COMPONENT_NOTE = ("Readings describe observed text only. A parsed value is a "
                  "notation reading, a candidate identity is a transcription, "
                  "and neither is a verified specification or a datasheet match.")


def group_lines(words: list[dict]) -> list[list[dict]]:
    """Cluster words into text lines by vertical overlap. Deterministic."""
    ordered = sorted(words, key=lambda w: ((w["bbox_xywh"][1] * 2 + w["bbox_xywh"][3]) / 2,
                                           w["bbox_xywh"][0]))
    lines: list[list[dict]] = []
    for word in ordered:
        placed = False
        cy = (word["bbox_xywh"][1] * 2 + word["bbox_xywh"][3]) / 2
        for line in lines:
            tops = [item["bbox_xywh"][1] for item in line]
            bottoms = [item["bbox_xywh"][1] + item["bbox_xywh"][3] for item in line]
            height = min([item["bbox_xywh"][3] for item in line] + [word["bbox_xywh"][3]])
            if min(tops) - height / 2 <= cy <= max(bottoms) + height / 2:
                line.append(word)
                placed = True
                break
        if not placed:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: w["bbox_xywh"][0])
    lines.sort(key=lambda line: min(item["bbox_xywh"][1] for item in line))
    return lines


def _union(line: list[dict]) -> list[int]:
    x0 = min(item["bbox_xywh"][0] for item in line)
    y0 = min(item["bbox_xywh"][1] for item in line)
    x1 = max(item["bbox_xywh"][0] + item["bbox_xywh"][2] for item in line)
    y1 = max(item["bbox_xywh"][1] + item["bbox_xywh"][3] for item in line)
    return [x0, y0, x1 - x0, y1 - y0]


def interpret_words(words: list[dict], image_name: str, mapping=None,
                    assistant=None) -> dict:
    """Parse OCR word observations as component markings. No OpenCV needed."""
    lines = group_lines(words)
    observations = []
    for number, line in enumerate(lines, 1):
        for word in line:
            observations.append({**word, "line": number})
    texts = [[word["raw_text"] for word in line] for line in lines]
    confs = [min((word.get("ocr_confidence") or 0) for word in line) if line else None
             for line in lines]
    verdict = interpret_crop(texts, confs, mapping)
    for line in verdict["candidates"]:
        source = lines[line["line"] - 1]
        line["bbox_xywh"] = _union(source)
        line["orientations_seen"] = sorted({angle for word in source
                                            for angle in word.get("orientations_seen", [0])})
        line["evidence"] = {"image": image_name}
    notes = None
    if assistant is not None:
        try:
            notes = assistant(words)
        except Exception as exc:  # noqa: BLE001 - advisor must not break readings
            notes = {"error": f"assistant failed: {exc}"}
    return {**verdict, "observations": observations,
            "component_note": COMPONENT_NOTE, "assistant_notes": notes}


def analyze(image, backend, roi: tuple[int, int, int, int] | None = None,
            mapping=None, assistant=None, image_name: str = "") -> dict:
    """Run OCR through pcb-ocr and interpret the words as markings."""
    ocr = ocr_analyze(image, backend, roi)
    base = {"backend": ocr["backend"], "preprocessing": ocr.get("preprocessing")}
    if ocr["status"] == "unavailable":
        return {**base, "status": "unavailable", "reason": ocr.get("reason"),
                "candidates": [], "observations": [],
                "component_note": COMPONENT_NOTE, "assistant_notes": None}
    words = [{"raw_text": item["raw_text"], "bbox_xywh": item["bbox_xywh"],
              "ocr_confidence": item["ocr_confidence"],
              "orientations_seen": item.get("orientations_seen", [0])}
             for item in ([*ocr.get("candidates", []), *ocr.get("ignored_text", [])])]
    if not words:
        return {**base, "status": "unknown", "reason": "OCR returned no text",
                "candidates": [], "observations": [],
                "component_note": COMPONENT_NOTE, "assistant_notes": None}
    return {**base, **interpret_words(words, image_name, mapping, assistant)}
