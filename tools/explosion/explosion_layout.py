"""The operator-confirmed layout spec ``drawings/explosion.json`` (anastylos-explosion-layout@1).

Layers are listed top to bottom. Every layer starts unconfirmed; only ``confirm_layer`` (an explicit operator
decision) sets ``confirmed_by_operator``. Writes are atomic (temp file + replace) so the page and a CLI never see
a half-written spec. Nothing here touches job folders or memory.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import explosion_jobs as jobs

SCHEMA = "anastylos-explosion-layout@1"
FILE = "explosion.json"
LAYER_KEYS = {"job", "label", "view_side", "flip_horizontal", "confirmed_by_operator", "confirmed_at"}
MAX_TEXT = 120

_FRONT = re.compile(r"\b(front|top|upper)\b", re.I)
_BACK = re.compile(r"\b(back|rear|bottom|lower)\b", re.I)
_STAMP = re.compile(r"-\d{8}-\d{6}$")


class LayoutError(ValueError):
    """The layout spec or a requested change to it is invalid; the message is shown to the operator."""


def layout_path(session_dir: Path) -> Path:
    return Path(session_dir) / "drawings" / FILE


def _text(value, field: str, *, allow_none: bool) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_TEXT:
        raise LayoutError(f"{field} must be a non-empty text of at most {MAX_TEXT} characters.")
    return value.strip()


def validate(doc) -> dict:
    """A normalised copy of ``doc``; raises LayoutError for anything the composer could not draw honestly."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise LayoutError(f"The layout spec must be a {SCHEMA} object.")
    layers = doc.get("layers")
    if not isinstance(layers, list):
        raise LayoutError("layers must be a list.")
    seen, clean = set(), []
    for position, layer in enumerate(layers):
        if not isinstance(layer, dict) or set(layer) - LAYER_KEYS:
            raise LayoutError(f"Layer {position} must be an object with only {sorted(LAYER_KEYS)}.")
        try:
            job = jobs.plain_job(layer.get("job"))
        except jobs.JobError as exc:
            raise LayoutError(str(exc)) from exc
        if job in seen:
            raise LayoutError(f"Drawing job {job!r} is listed twice.")
        seen.add(job)
        for flag in ("flip_horizontal", "confirmed_by_operator"):
            if not isinstance(layer.get(flag), bool):
                raise LayoutError(f"Layer {job}: {flag} must be true or false.")
        entry = {"job": job, "label": _text(layer.get("label"), f"Layer {job}: label", allow_none=False),
                 "view_side": _text(layer.get("view_side"), f"Layer {job}: view_side", allow_none=True),
                 "flip_horizontal": layer["flip_horizontal"], "confirmed_by_operator": layer["confirmed_by_operator"]}
        if layer.get("confirmed_at") is not None:
            entry["confirmed_at"] = _text(layer["confirmed_at"], f"Layer {job}: confirmed_at", allow_none=False)
        clean.append(entry)
    out = {"schema": SCHEMA, "layers": clean}
    if doc.get("note") is not None:
        out["note"] = _text(doc["note"], "note", allow_none=False)
    return out


def load(session_dir: Path) -> dict | None:
    path = layout_path(session_dir)
    if not path.is_file():
        return None
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:  # JSON errors; LayoutError is a ValueError too and keeps its own message
        if isinstance(exc, LayoutError):
            raise
        raise LayoutError(f"{FILE} is not valid JSON ({exc}).") from exc


def save(session_dir: Path, doc: dict) -> dict:
    clean = validate(doc)
    path = layout_path(session_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{FILE}.tmp-{os.getpid()}")
    temp.write_text(json.dumps(clean, indent=1) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return clean


def _rank(text: str) -> int:
    return 0 if _FRONT.search(text) else 2 if _BACK.search(text) else 1


def _candidates(session_dir: Path) -> list[jobs.JobEvidence]:
    """One drawn job per (part, scope): an operator-confirmed binding first, then the newest run."""
    best: dict[tuple, jobs.JobEvidence] = {}
    for name in sorted(jobs.list_job_names(session_dir)):  # ascending: a later name (newer timestamp) replaces an earlier one
        evidence = jobs.read_job(session_dir, name)
        if (evidence.state != "done" or evidence.status not in jobs.DRAWABLE_STATUSES
                or evidence.outline is None or evidence.binding_state == "rejected"):
            continue
        key = (evidence.part or _STAMP.sub("", name), evidence.scope)
        held = best.get(key)
        if not (held is not None and held.binding_state == "confirmed" and evidence.binding_state != "confirmed"):
            best[key] = evidence
    return sorted(best.values(), key=lambda e: (_rank(f"{e.part or ''} {e.job}"), e.job))


def propose(session_dir: Path) -> dict:
    """Write the skeleton from the existing drawn jobs; keep every layer (and confirmation) already in the spec.

    New parts are appended unconfirmed. The initial order is a front/back word heuristic and is only a proposal:
    the operator confirms or reorders it.
    """
    existing = load(session_dir)
    layers = list(existing["layers"]) if existing else []
    known = {layer["job"] for layer in layers}
    known_parts = set()
    for layer in layers:
        evidence = jobs.read_job(session_dir, layer["job"])
        known_parts.add((evidence.part or _STAMP.sub("", layer["job"]), evidence.scope))
    added = []
    for evidence in _candidates(session_dir):
        if evidence.job in known or (evidence.part or _STAMP.sub("", evidence.job), evidence.scope) in known_parts:
            continue
        layers.append({"job": evidence.job, "label": (evidence.part or evidence.job)[:MAX_TEXT], "view_side": evidence.view_side or None,
                       "flip_horizontal": False, "confirmed_by_operator": False})
        added.append(evidence.job)
    doc = {"schema": SCHEMA, "layers": layers,
           "note": (existing or {}).get("note") or "Proposed from the existing drawings; order, view side and flip need operator confirmation."}
    changed = existing is None or added
    return {"layout": save(session_dir, doc) if changed else existing, "added": added, "written": bool(changed)}


def _layer(doc: dict, job: str) -> dict:
    for layer in doc["layers"]:
        if layer["job"] == job:
            return layer
    raise LayoutError(f"Drawing job {job!r} is not in the layout.")


def _require_layout(session_dir: Path) -> dict:
    doc = load(session_dir)
    if doc is None:
        raise LayoutError("There is no layout yet: propose one first.")
    return doc


def confirm_layer(session_dir: Path, job: str, *, label: str | None = None, view_side: str | None = None,
                  flip_horizontal: bool | None = None, confirmed: bool = True, now: str | None = None) -> dict:
    """Record the operator's decision on one layer as shown (label, view side, flip, current position)."""
    doc = _require_layout(session_dir)
    layer = _layer(doc, job)
    if label is not None:
        layer["label"] = label
    if view_side is not None:
        layer["view_side"] = view_side
    if flip_horizontal is not None:
        layer["flip_horizontal"] = flip_horizontal
    if confirmed:
        if not layer.get("view_side"):
            raise LayoutError("Set the view side before confirming the layer.")
        layer["confirmed_by_operator"] = True
        layer["confirmed_at"] = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        layer["confirmed_by_operator"] = False
        layer.pop("confirmed_at", None)
    return save(session_dir, doc)


def reorder_layers(session_dir: Path, order: list[str]) -> dict:
    """New top-to-bottom order. A layer whose position changed loses its confirmation: it was confirmed at another place."""
    doc = _require_layout(session_dir)
    if not isinstance(order, list) or sorted(order) != sorted(layer["job"] for layer in doc["layers"]):
        raise LayoutError("The new order must list every layer exactly once.")
    by_job = {layer["job"]: layer for layer in doc["layers"]}
    old = [layer["job"] for layer in doc["layers"]]
    doc["layers"] = [by_job[job] for job in order]
    for position, job in enumerate(order):
        if old[position] != job:
            doc["layers"][position]["confirmed_by_operator"] = False
            doc["layers"][position].pop("confirmed_at", None)
    return save(session_dir, doc)
