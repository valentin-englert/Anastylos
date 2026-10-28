"""Prompt construction for SAM 3.1. Stdlib only (plus vendored parser).

Two prompt kinds, never mixed in one request (API contract):

- concept: one short noun phrase, e.g. ``"screw"``.
- objects: box/point prompts built with the official
  ``build_object_prompt`` over display-pixel coordinates.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor"))

from meta_sam_parser import (  # noqa: E402
    FramePrompt,
    ObjectPromptError,
    PromptBox,
    PromptObject,
    PromptPoint,
    build_object_prompt,
)

_COMMAND_OPENERS = (
    "segment ", "find ", "locate ", "detect ", "show ", "mark ",
    "where ", "what ", "which ", "how many ",
)
_LIST_SPLIT = re.compile(r"[,;+]|\band\b", re.IGNORECASE)


def validate_concept(text: str) -> list[str]:
    """Return human-readable warnings; the API stays authoritative.

    Hard failures raise ValueError (empty, multi-concept). Soft risks
    (commands, questions, negation, spatial phrasing) return warnings
    so the caller can record or abort them explicitly.
    """
    warnings: list[str] = []
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ValueError("concept prompt is empty")
    if "\n" in text or _LIST_SPLIT.search(cleaned):
        raise ValueError(
            f"concept prompt {cleaned!r} looks multi-concept; "
            "send one concept per request"
        )
    lowered = cleaned.lower()
    if lowered.endswith("?") or lowered.startswith(_COMMAND_OPENERS):
        warnings.append(
            "commands/questions are unreliable concept prompts; "
            "prefer a bare noun phrase"
        )
    if re.search(r"\bnot\b|\bno\b|\bor\b", lowered):
        warnings.append("negation/boolean-OR prompts return inconsistent results")
    if re.search(r"\b(left|right|top|bottom|front|back|near|next to)\b", lowered):
        warnings.append("spatial phrasing is only partially honored")
    if len(cleaned.split()) > 6:
        warnings.append("prompts longer than ~6 words degrade matching")
    return warnings


def normalize_concept(text: str) -> str:
    return " ".join(text.split())


def build_box_prompt(
    *,
    size: tuple[int, int],
    objects: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Build object-prompt text via the official builder.

    ``objects`` items: ``{"id": int|str, "frame": int = 0,
    "box": [left, top, right, bottom] (half-open) = None,
    "points": [{"x","y","label"}] = []}``. Returns (prompt_text, echo)
    where echo restates the normalized inputs for the evidence file.
    """
    specs: list[PromptObject] = []
    echo: list[dict[str, Any]] = []
    for item in objects:
        box_spec = item.get("box")
        box = PromptBox(*box_spec) if box_spec is not None else None
        points = tuple(
            PromptPoint(x=int(point["x"]), y=int(point["y"]),
                        label=point.get("label", "positive"))
            for point in item.get("points", [])
        )
        frame = int(item.get("frame", 0))
        specs.append(
            PromptObject(id=item["id"],
                         prompts=[FramePrompt(frame=frame, box=box,
                                              points=points)])
        )
        echo.append({
            "id": str(item["id"]),
            "frame": frame,
            "box": list(box_spec) if box_spec is not None else None,
            "points": [{"x": point.x, "y": point.y, "label": point.label}
                       for point in points],
        })
    try:
        text = build_object_prompt(size=size, objects=specs)
    except ObjectPromptError as exc:
        raise ValueError(f"invalid object prompt ({exc.code}): {exc}") from exc
    return text, echo
