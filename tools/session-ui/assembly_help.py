"""Assembly help for a selected drawing part: operator-confirmed part link and "where does this part belong?".

A drawing job and a memory part are different things. A drawing never proves which memory part it
shows, so the operator links them explicitly (``part-link.json`` in the job folder); candidates are
only proposed (job ``spec.part`` text against memory part labels) and never linked automatically.

Origin and storage stay strictly separate:

- ``origin``  = where the part came from / belongs: the ``declared_removal`` source site, with its phase,
  timestamps and evidence references as stored (install origin);
- ``storage`` = where the part lies now: the active ``storage_link`` location.

Answer text is never written here. It is rendered by ``tools/agent/selected_guide.py`` from the
claim-checked guide package (``tools/guide/claims.py``), bound to the package and memory hashes; every
result cites ``package_sha256`` and ``memory_sha256``. If the stored origin is too coarse for a concrete
install point (only a place such as "the back shell" or a sheet region, no screw boss or connector),
the result says so, gives a targeted follow-up question and the evidence to show, and never a guessed
install point. Nothing in this module writes to memory, the guide, or the session's answer logs; the
only write is ``part-link.json``.

HTTP routes for ``server.py`` (owner: TL; this module only provides the functions)
------------------------------------------------------------------------------
All bodies are JSON objects, all names are plain ids (no paths); errors are ``AdapterError`` with the
status shown. ``Handler.workspace_root`` is the workspace root, as for the other routes.

``GET /api/parts/link-candidates?session=<id>&job=<job>`` -> ``link_candidates(root, session, job)``
    200 ``{"schema": "anastylos-drawing-part-candidates@1", "job", "spec_part", "drawing_sha256",
    "memory_sha256", "link": <part-link record> | null, "link_state": "none" | "linked" |
    "stale_drawing" | "stale_part" | "stale_memory", "auto_link": false, "candidates": [{"part_id", "label",
    "origin_description", "storage_location", "score", "matched_tokens", "suggested"}]}``.
    ``candidates`` lists every current declared part (best match first); ``suggested`` only marks a
    label that shares words with ``spec_part``. 404 unknown session/job.

``POST /api/parts/link`` body ``{"session", "job", "part", "supersede"?: bool, "note"?: str}``
    -> ``link_part(root, session, job, part, supersede, note)``; run it under ``_mutate`` (writes
    ``part-link.json``). 200 ``anastylos-drawing-part-link@1`` record (``already_recorded: true`` when
    the identical decision exists); 409 when the job is linked to a different part or a changed drawing
    and ``supersede`` is not true, when the drawing is not linkable, was rejected, or ``part`` is not a
    current declared part; 400 for a missing ``part``.

``POST /api/parts/ask`` body ``{"session", "target", "question"}``
    -> ``ask_about_part(root, session, target, question)``; read-only (no ``_mutate`` needed).
    ``target`` is the drawing job id (the ``job`` of ``anastylos:part-selected``) or a memory part id
    such as ``dec-0001``. 200 ``anastylos-part-help@1`` (see ``ask_about_part``); the HTTP status is 200
    for every ``status`` value, ``status`` is the outcome. 404 unknown session/target, 400 empty question.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import adapter
import drawings
import part_intent
from adapter import AdapterError

import guide  # noqa: E402  (tools/agent, on sys.path through adapter)
import selected_guide  # noqa: E402
import selection  # noqa: E402
from contracts import CorruptLogError, load_events  # noqa: E402  (tools/live)

LINK_SCHEMA = "anastylos-drawing-part-link@1"
CANDIDATES_SCHEMA = "anastylos-drawing-part-candidates@1"
HELP_SCHEMA = "anastylos-part-help@1"
FOLLOW_UP_SCHEMA = "anastylos-part-followup@1"
LINK_FILE = "part-link.json"

# ------------------------------------------------------------------ fixed operator-facing wording

NEEDS_LINK = ("This drawing is not linked to a current recorded part. Which current part, if any, does it show? "
              "Leave it unlinked when no current record matches.")
LINK_STALE = ("The link between this drawing and its recorded part no longer holds ({reason}). "
              "Link the drawing to a part again.")
COARSE_ORIGIN = ("The recorded origin is only a description of a place, not a specific screw boss, connector or "
                 "mounting point, so I cannot name an install point.")
UNKNOWN_ORIGIN = "No origin was recorded for this part, so I cannot say where it belongs."
UNKNOWN_STORAGE = "No storage place was recorded for this part."
UNSUPPORTED = "I can answer only where a selected part belongs or where it lies now. Ask, for example: “{examples}”."

# ------------------------------------------------------------------ origin precision (closed vocabulary)

_INSTALL_POINT = re.compile(r"\b(?:screw boss|boss|standoff|stand-off|screw hole|hole|post|connector|header|socket|"
                            r"jack|port|slot|clip|hook|latch|terminal|ribbon|ffc)\b", re.IGNORECASE)
_UNSURE = re.compile(r"\b(?:or|either|maybe|probably|perhaps|somewhere|not sure)\b|\?", re.IGNORECASE)
_EVIDENCE_KEYS = ("pair_id", "region", "result_sha256", "image_sha256", "frame_sha256", "photo_sha256", "frame", "sheet_id")
_EVIDENCE_FILE_KEYS = ("result_json", "path", "image")
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({"a", "an", "the", "of", "with", "and", "or", "to", "for", "on", "in", "at", "from", "as",
                        "its", "it", "is", "by"})


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tokens(text: str) -> set[str]:
    """Lower-case words without stop words; a plural ``s`` is dropped so "screws" meets "screw"."""
    words = (w for w in _WORD.findall((text or "").lower()) if w not in _STOPWORDS)
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words}


# ------------------------------------------------------------------ memory (read-only)

def read_memory(state) -> dict:
    path = state.memory_dir / "memory.jsonl"
    if not path.is_file():
        raise AdapterError("This session has no memory yet; no part can be linked or asked about.", 409)
    data = path.read_bytes()
    try:
        events = load_events(path)
    except CorruptLogError as exc:
        raise AdapterError("The session memory has malformed or duplicate records; no part can be linked.", 409) from exc
    superseded = {e["supersedes"] for e in events if e.get("supersedes")}
    prefix = hashlib.sha256()
    history_sha256 = set()
    for line in data.splitlines(keepends=True):
        prefix.update(line)
        history_sha256.add(prefix.hexdigest())
    return {"sha256": _sha256(data), "history_sha256": history_sha256,
            "by_id": {e["id"]: e for e in events}, "superseded": superseded,
            "parts": [e for e in events if e.get("type") == "declared_removal" and e["id"] not in superseded],
            "storage": {e["target_id"]: e for e in events if e.get("type") == "storage_link"
                        and e["id"] not in superseded}}


def _part_summary(event: dict, memory: dict) -> dict:
    stored = memory["storage"].get(event["id"])
    return {"part_id": event["id"], "label": event.get("operator_label"), "origin_description": event.get("source"),
            "storage_location": stored.get("location") if stored else None}


# ------------------------------------------------------------------ part link (operator decision)

def _job_context(root: Path, session_id: str, job: str) -> dict:
    summary = drawings.drawing_status(root, session_id, job)  # validates the plain job name (404)
    job_dir = adapter.resolve_session(root, session_id) / drawings.DRAWINGS / "jobs" / job
    svg = job_dir / "drawing.svg"
    return {"dir": job_dir, "summary": summary,
            "drawing_sha256": _sha256(svg.read_bytes()) if svg.is_file() else None}


def _read_link(job_dir: Path) -> dict | None:
    path = job_dir / LINK_FILE
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdapterError(f"{LINK_FILE} cannot be read as UTF-8 JSON; the link needs the operator's attention.", 409) from exc
    if not isinstance(record, dict) or record.get("schema") != LINK_SCHEMA:
        raise AdapterError(f"{LINK_FILE} has an unsupported schema.", 409)
    return record


def _write_atomic(path: Path, document: dict) -> None:
    partial = path.with_name(path.name + ".partial")
    with open(partial, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(document, ensure_ascii=False, indent=1) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _link_state(link: dict | None, context: dict, memory: dict) -> tuple[str, str | None]:
    if link is None:
        return "none", None
    if link["drawing_sha256"] != context["drawing_sha256"]:
        return "stale_drawing", "the drawing changed after it was linked"
    if link["part"]["id"] not in {e["id"] for e in memory["parts"]}:
        return "stale_part", "the recorded part was replaced or removed in memory"
    if link.get("memory_sha256") not in memory["history_sha256"]:
        return "stale_memory", "the session memory does not extend the snapshot used for this link"
    return "linked", None


def _compact(text: str | None) -> str:
    return "".join(_WORD.findall((text or "").lower()))


def _candidates(spec_part: str | None, memory: dict) -> list[dict]:
    """Every current part, best label match first. Words are compared singular and, ignoring spacing, as one run
    ("frontshell" meets "front shell", and "front shell of the controller" contains "frontshell")."""
    wanted, wanted_key = _tokens(spec_part or ""), _compact(spec_part)
    out = []
    for event in memory["parts"]:
        label_words = _tokens(event.get("operator_label") or "")
        label_key = _compact(event.get("operator_label"))
        shared = sorted(wanted & label_words)
        score = round(len(shared) / len(label_words), 3) if label_words else 0.0
        if label_key and wanted_key:
            short, long_ = sorted((label_key, wanted_key), key=len)
            if short == long_:
                shared, score = sorted(label_words), 1.0
            elif len(short) >= 4 and short in long_:
                shared, score = shared or sorted(label_words), max(score, 0.8)
        out.append({**_part_summary(event, memory), "score": score, "matched_tokens": shared, "suggested": bool(shared)})
    return sorted(out, key=lambda c: (-c["score"], -len(c["matched_tokens"]), c["part_id"]))


def wanted_label(context: dict) -> str | None:
    """The words to match against memory labels: the operator-confirmed layer name, else the drawing job's part text."""
    layer = layer_of(context["dir"])
    return layer["label"] if layer else context["summary"]["part"]


def layer_of(job_dir: Path) -> dict | None:
    """The drawing's layer in the explosion layout, with its neighbours; only an operator-confirmed layer counts."""
    try:
        layers = json.loads((job_dir.parents[1] / "explosion.json").read_text(encoding="utf-8"))["layers"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    index = next((i for i, layer in enumerate(layers) if layer.get("job") == job_dir.name), None)
    if index is None or not layers[index].get("confirmed_by_operator"):
        return None
    return {"label": layers[index].get("label"), "position": index + 1, "of": len(layers),
            "above": layers[index - 1].get("label") if index else None,
            "below": layers[index + 1].get("label") if index + 1 < len(layers) else None}


def _parts_of(label: str | None, event_id: str, memory: dict) -> list[dict]:
    """Current parts whose stated origin is exactly this part's name, grouped by name and place. Near matches do not count."""
    wanted = _tokens(label or "")
    groups: dict[tuple, dict] = {}
    for event in memory["parts"]:
        if not wanted or event["id"] == event_id or _tokens(event.get("source") or "") != wanted:
            continue
        summary = _part_summary(event, memory)
        group = groups.setdefault((summary["label"], summary["storage_location"]), {
            "label": summary["label"], "storage_location": summary["storage_location"], "count": 0, "record_ids": []})
        group["count"] += 1
        group["record_ids"].append(event["id"])
    return list(groups.values())


def link_candidates(root: Path, session_id: str, job: str) -> dict:
    """Current link state plus every current memory part, ranked by label match; never links anything."""
    state = adapter._load_state(root, session_id)
    context = _job_context(root, session_id, job)
    memory = read_memory(state)
    link = _read_link(context["dir"])
    link_state, _reason = _link_state(link, context, memory)
    return {"schema": CANDIDATES_SCHEMA, "job": job, "spec_part": context["summary"]["part"],
            "drawing_sha256": context["drawing_sha256"], "memory_sha256": memory["sha256"], "link": link,
            "link_state": link_state, "auto_link": False,
            "candidates": _candidates(wanted_label(context), memory)}


def link_part(root: Path, session_id: str, job: str, part_id: str, supersede: bool = False, note: str = "") -> dict:
    """Record the operator's decision that this drawing shows this memory part (memory is never written).

    Idempotent for the identical decision; the first decision wins until the operator explicitly
    supersedes it, which keeps the replaced record in ``history``.
    """
    if not isinstance(part_id, str) or not part_id.strip():
        raise AdapterError("The part parameter is required.", 400)
    state = adapter._load_state(root, session_id)
    context = _job_context(root, session_id, job)
    summary = context["summary"]
    if summary["state"] != "done" or summary["status"] not in drawings.BINDABLE or context["drawing_sha256"] is None:
        raise AdapterError(f"This drawing cannot be linked (state {summary['state']}, status {summary['status']}).", 409)
    if (summary["binding"] or {}).get("decision") == "reject":
        raise AdapterError("The operator rejected this drawing; it cannot be linked to a part.", 409)
    memory = read_memory(state)
    event = next((e for e in memory["parts"] if e["id"] == part_id), None)
    if event is None:
        raise AdapterError(f"{part_id!r} is not a current declared part in the session memory.", 409)
    existing = _read_link(context["dir"])
    if existing and existing["part"]["id"] == part_id and _link_state(existing, context, memory)[0] == "linked":
        return {**existing, "already_recorded": True}
    if existing and not supersede:
        raise AdapterError(f"This drawing is already linked to “{existing['part']['label']}” ({existing['part']['id']}); "
                           "send supersede: true to replace that decision.", 409)
    history = ([{**{k: v for k, v in existing.items() if k != "history"}}] + existing.get("history", [])) if existing else []
    record = {"schema": LINK_SCHEMA, "seq": len(history) + 1, "job": job, "drawing_sha256": context["drawing_sha256"],
              "part": {"id": part_id, "label": event.get("operator_label")}, "memory_sha256": memory["sha256"],
              "decided_by": "operator", "operator": state.doc.get("operator"),
              "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": (note or "")[:300],
              "drawing_binding": (summary["binding"] or {}).get("decision", "unreviewed"),
              "supersedes": ({"seq": existing["seq"], "part_id": existing["part"]["id"],
                              "drawing_sha256": existing["drawing_sha256"]} if existing else None),
              "history": history, "memory_written": False}
    _write_atomic(context["dir"] / LINK_FILE, record)
    return record


# ------------------------------------------------------------------ the answer

def _result(status: str, message: str, matched: dict | None, target: dict, **fields) -> dict:
    return {"schema": HELP_SCHEMA, "status": status, "message": message,
            "intent": matched["intent"] if matched else None, "phrase": matched["phrase"] if matched else None,
            "target": target, "link": None, "package_sha256": None, "memory_sha256": None, "answer": None,
            "answers": None, "part_label": None, "origin": None, "storage": None, "follow_up": None, "evidence_to_show": [],
            "candidates": [], "memory_written": False, **fields}


def _evidence_ref(evidence) -> dict | None:
    if not isinstance(evidence, dict):
        return None
    ref = {key: evidence[key] for key in _EVIDENCE_KEYS if isinstance(evidence.get(key), (str, int, float))}
    for key in _EVIDENCE_FILE_KEYS:  # absolute server paths never reach the browser
        if isinstance(evidence.get(key), str):
            ref["file"] = Path(evidence[key].replace("\\", "/")).name
            break
    return {"kind": "memory_evidence", "as_stored": True, **ref} if ref else None


def origin_precision(description: str | None, observation: dict | None) -> str:
    """Closed classification of how concrete the stored origin is (not of whether it is true)."""
    if description:
        if _UNSURE.search(description):
            return "ambiguous_description"
        return "named_install_point" if _INSTALL_POINT.search(description) else "coarse_place"
    return "sheet_region" if observation else "unknown"


def _origin_view(resolved: dict, memory: dict) -> dict:
    location = resolved["original_location"]
    event = memory["by_id"][resolved["event_id"]]
    observation = memory["by_id"].get(location["observation_id"]) if location["observation_id"] else None
    phase = memory["by_id"].get(event.get("phase")) if event.get("phase") else None
    precision = origin_precision(location["value"], observation)
    return {"description": location["value"], "basis": location["basis"], "declared_by": location["declared_by"],
            "record_id": event["id"], "recorded_at": event.get("timestamp"), "t_s": event.get("t_s"),
            "phase": {"id": event.get("phase"), "label": phase.get("label") if phase else None},
            "evidence_note": location["evidence_note"],
            "observation": ({k: observation.get(k) for k in ("id", "source_view", "bbox_mm", "centroid_mm", "note", "t_s",
                                                             "timestamp")} if observation else None),
            "evidence_refs": [r for r in (_evidence_ref(location["evidence"]),) if r],
            "precision": precision, "concrete_install_point": precision == "named_install_point",
            "install_point_verified": False,
            "limits": ["Operator words as recorded; no image check of the install point."]}


def _storage_view(resolved: dict, memory: dict) -> dict:
    stored = resolved["stored_location"]
    record = memory["by_id"].get(stored["record_id"]) if stored["record_id"] else None
    return {"location": stored["value"], "record_id": stored["record_id"],
            "basis": (stored["basis"] or {}).get("kind"), "confirmed_by": (stored["basis"] or {}).get("confirmed_by"),
            "recorded_at": record.get("timestamp") if record else None}


def _checked_text(answer: dict) -> dict:
    return {"intent": answer["intent"], "text": answer["text"], "accepted": answer["accepted"],
            "outcome": answer["outcome"], "source": answer["source"], "claims": answer["claims"],
            "findings": answer["findings"], "package_sha256": answer["guide_sha256"],
            "memory_sha256": answer["memory_sha256"]}


def _follow_up(kind: str, label: str, event_id: str, job: str | None, reason: str, description: str | None) -> dict:
    """A targeted question for the operator; fixed wording, the operator's own words only quoted."""
    if kind == "install_point":
        said = f"You described its origin as “{description}”. " if description else ""
        text = (f"{said}Which screw boss, connector or mounting point did “{label}” come from? "
                "Describe it, or show it in a photo.")
    else:
        text = f"Where did you put “{label}” after removing it?"
    key = json.dumps([kind, event_id, job, reason], sort_keys=True)
    return {"schema": FOLLOW_UP_SCHEMA, "question_id": "fu-" + _sha256(key.encode())[:12], "kind": kind,
            "text": text, "reason": reason, "target": {"event_id": event_id, "job": job, "label": label},
            "expects": "operator_text_or_photo", "recorded": False,
            "answer_route": None}  # recording is not wired: memory is append-only and owned elsewhere


def _evidence_to_show(context: dict | None, origin: dict) -> list[dict]:
    shown = []
    if context and "overlay.jpg" in context["summary"]["files"]:
        data = (context["dir"] / "overlay.jpg").read_bytes()
        shown.append({"kind": "drawing_overlay", "job": context["summary"]["job"], "file": "overlay.jpg",
                      "sha256": _sha256(data), "as_stored": True})
    return shown + origin["evidence_refs"]


def _ask_status(intent: str, answer: dict, origin: dict, storage: dict) -> tuple[str, str]:
    if not answer["accepted"]:
        return "not_proven", answer["text"]
    if intent == part_intent.STORAGE:
        return ("answered", "") if storage["location"] else ("storage_unknown", UNKNOWN_STORAGE)
    if origin["precision"] == "unknown":
        return "origin_unknown", UNKNOWN_ORIGIN
    if not origin["concrete_install_point"]:
        return "coarse_origin", COARSE_ORIGIN
    return "answered", ""


def _target_for(root: Path, session_id: str, target: str, memory: dict) -> tuple[dict, dict | None]:
    """``({"kind": "job"|"part", ...}, job context | None)``; a job id wins over a part id."""
    try:
        context = _job_context(root, session_id, target)
        return {"kind": "job", "job": target, "part_id": None}, context
    except AdapterError as exc:
        if exc.status != 404:
            raise
    if any(e["id"] == target for e in memory["parts"]):
        return {"kind": "part", "job": None, "part_id": target}, None
    if target in memory["superseded"]:
        raise AdapterError(f"Part {target!r} was replaced in the session memory; select its current record.", 409)
    raise AdapterError(f"Unknown drawing job or recorded part {target!r}.", 404)


def ask_about_part(root: Path, session_id: str, job_or_part: str, question: str) -> dict:
    """Answer "where does this part belong / where did I put it" for a selected drawing part.

    Returns ``anastylos-part-help@1`` with ``status``:

    ``answered``            ``answer.text`` is the claim-checked sentence for the asked intent
    ``coarse_origin``       origin asked, but the stored origin names no install point: ``answer.text`` quotes the
                            stored place, ``message`` says it is too coarse, ``follow_up`` and ``evidence_to_show`` follow
    ``origin_unknown`` / ``storage_unknown``   nothing recorded: ``follow_up`` asks for it
    ``parts_belong_here``   origin asked of an assembly with no origin of its own: ``belongs_here`` lists the parts whose
                            stated origin is exactly its name, ``layer`` its operator-confirmed place in the layout
    ``needs_link`` / ``link_stale``   the drawing is not (validly) linked: ``candidates`` for the operator
    ``guide_unusable``      no fresh package bound to the memory hash: nothing is answered
    ``not_proven`` / ``selection_refused``   the verifier or the hash binding refused
    ``unsupported_question``   not in the closed phrase set of ``part_intent``

    ``origin`` (declared site, phase, timestamps, evidence refs) and ``storage`` (current place) are separate
    blocks, both always present once a part is resolved; ``answer`` is the text of the asked intent only (speak or
    show only that one); ``answers`` holds both checked texts by intent for a UI that shows origin and storage side by side.
    Every resolved result cites ``package_sha256`` and ``memory_sha256``. Nothing is written.
    """
    if not isinstance(question, str) or not question.strip():
        raise AdapterError("The question is required.", 400)
    state = adapter._load_state(root, session_id)
    memory = read_memory(state)
    target, context = _target_for(root, session_id, job_or_part, memory)
    matched = part_intent.match(question)
    if matched is None:
        return _result("unsupported_question", UNSUPPORTED.format(examples="”, “".join(part_intent.SUPPORTED)),
                       None, target)
    link = None
    if context is not None:
        link = _read_link(context["dir"])
        link_state, reason = _link_state(link, context, memory)
        if link_state != "linked":
            status, message = ("needs_link", NEEDS_LINK) if link_state == "none" else ("link_stale", LINK_STALE.format(reason=reason))
            return _result(status, message, matched, target, link=link, memory_sha256=memory["sha256"],
                           candidates=_candidates(wanted_label(context), memory))
        event_id, element = link["part"]["id"], f"drawing-job:{job_or_part}"
    else:
        event_id, element = job_or_part, f"memory-part:{job_or_part}"
    try:
        memory_path, package_path, binding = adapter.fresh_selection_inputs(root, session_id, event_id, element)
        resolved = selection.resolve(memory_path, package_path, binding)
        answers = {intent: selected_guide.answer(memory_path, package_path, binding, intent)
                   for intent in (part_intent.ORIGIN, part_intent.STORAGE)}
    except AdapterError as exc:
        return _result("guide_unusable", str(exc), matched, target, link=link, memory_sha256=memory["sha256"])
    except selection.SelectionError as exc:
        return _result("selection_refused", str(exc), matched, target, link=link, memory_sha256=memory["sha256"])
    origin, storage = _origin_view(resolved, memory), _storage_view(resolved, memory)
    asked = answers[matched["intent"]]
    status, message = _ask_status(matched["intent"], asked, origin, storage)
    label = resolved["operator_label"]
    follow_up = None
    if status == "storage_unknown":
        follow_up = _follow_up("storage_place", label, event_id, target["job"], "no storage record", None)
    belongs_here = _parts_of(label, event_id, memory)
    layer = layer_of(context["dir"]) if context is not None else None
    if status == "origin_unknown" and belongs_here:
        total = sum(group["count"] for group in belongs_here)
        status, message = "parts_belong_here", f"{total} recorded part(s) came from {label}; put them back here."
    elif status in ("coarse_origin", "origin_unknown"):
        follow_up = _follow_up("install_point", label, event_id, target["job"], origin["precision"], origin["description"])
    return _result(status, message, matched, target, link=link, package_sha256=asked["guide_sha256"],
                   memory_sha256=asked["memory_sha256"], answer=None if status == "parts_belong_here" else _checked_text(asked), origin=origin, storage=storage,
                   follow_up=follow_up, belongs_here=belongs_here, layer=layer, part_label=label,
                   evidence_to_show=_evidence_to_show(context, origin) if status in ("coarse_origin", "origin_unknown") else [],
                   answers={intent: _checked_text(answer) for intent, answer in answers.items()})
