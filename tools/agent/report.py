"""Standalone, evidence-bound HTML session report for ANASTYLOS.

Reads one agent session directory (``agent-session.json``, Assembly Memory,
the recorded guide package, question journals, assembly progress) and writes
a single self-contained English HTML file: embedded CSS, no network, no
scripts, no model calls. Open it in a browser, print it, share it.

Everything shown is read-only. The guide package and its hash bindings are
checked with the existing loaders before anything is rendered: a stale or
tampered guide refuses the export with an English instruction instead of a
report. Inputs are never modified or repaired.

Only file content already bound by a recorded SHA-256 is trusted, and only
as stored: evidence references are cited by pair, region and hash, never
opened, and no frame-to-record confirmation is claimed. Operator statements,
automatic observations and inferred links are labelled as such throughout.
The install order is the reverse of the recorded removal order unless a
dependency was confirmed; it is never presented as a mechanically verified
assembly sequence.

Stable entry points for the UI agent (see ``REPORT.md``)::

    from report import export
    result = export(session_dir, out_html)

    python tools/agent/report_cli.py --session <dir> --out <report.html>
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import anastylos
import ask_loop
import assemble
import guide
from contracts import CorruptLogError, answers, load_events
from state import SessionState

REPORT_SCHEMA = "anastylos-session-report@1"
EXPORT_SCHEMA = "anastylos-session-report-export@1"
GENERATOR = "anastylos-session-report@1"
# States that keep a session incomplete (same set as anastylos.summary).
OPEN_STATES = {"OPEN", "ASKED", "ANSWERED", "READ_BACK", "CONFIRMED", "DEFERRED", "FAILED"}


class ReportError(Exception):
    """The report cannot be built; the message is the English text for the operator."""


# ------------------------------------------------------------------ small helpers

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _short(digest: str | None) -> str | None:
    return digest[:12] if digest else None


def _basename(value: str | None) -> str | None:
    """A stored path reduced to its file name; absolute paths never reach the report."""
    if value is None or str(value) == "":
        return None
    return str(value).replace("\\", "/").rsplit("/", 1)[-1] or str(value)


def _read_json(path: Path, what: str) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReportError(f"The {what} cannot be read as UTF-8 JSON ({path.name}); nothing was exported.") from exc
    if not isinstance(document, dict):
        raise ReportError(f"The {what} is not a JSON object ({path.name}); nothing was exported.")
    return document


def _read_jsonl(path: Path, what: str) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReportError(f"The {what} cannot be read ({path.name}); nothing was exported.") from exc
    records = []
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ReportError(f"The {what} has invalid JSON on line {lineno}; nothing was exported.") from exc
        if not isinstance(record, dict):
            raise ReportError(f"The {what} has a non-object on line {lineno}; nothing was exported.")
        records.append(record)
    return records


def esc(value) -> str:
    """Escape one input value for HTML text. None becomes an empty string."""
    return html.escape("" if value is None else str(value), quote=True)


# ------------------------------------------------------------------ loading (read-only)

def _load_memory(state: SessionState) -> tuple[str, list[dict], list[dict]]:
    path = state.memory_dir / "memory.jsonl"
    if not path.is_file():
        raise ReportError("This session has no memory yet (memory.jsonl is missing); nothing was exported.")
    try:
        first = path.read_bytes()
        events = load_events(path)
        if path.read_bytes() != first:
            raise ReportError("The session memory changed while reading it; run the export again.")
    except CorruptLogError as exc:
        raise ReportError(f"The session memory cannot be trusted ({exc}); nothing was exported.") from exc
    except OSError as exc:
        raise ReportError(f"The session memory cannot be read ({exc}); nothing was exported.") from exc
    superseded = {event["supersedes"] for event in events if event.get("supersedes")}
    return _sha256(first), events, [event for event in events if event["id"] not in superseded]


def _load_guide(state: SessionState) -> tuple[str, dict | None, dict | None]:
    """``("fresh", raw package, build record)`` or ``("none", None, None)``.

    A guide that exists but is stale or tampered with refuses the export;
    only a session whose guide was never built exports without one.
    """
    builds = guide.guide_dir(state) / "builds.jsonl"
    package = guide.guide_dir(state) / "session.json"
    if not builds.is_file() and not package.is_file():
        return "none", None, None
    try:
        _, raw = guide.load(state)
    except guide.GuideError as exc:
        raise ReportError(f"{exc} Nothing was exported.") from exc
    records = _read_jsonl(builds, "guide build record")
    if not records:
        raise ReportError("The guide has no build record; run `guide build` first. Nothing was exported.")
    return "fresh", raw, records[-1]


def _load_questions(state: SessionState) -> tuple[dict, dict, list[str]]:
    """Per-question states with leave reasons, run details, and excluded phases."""
    try:
        states = ask_loop.question_states(state)
    except ValueError as exc:
        raise ReportError(f"The question journal cannot be read ({exc}); nothing was exported.") from exc
    reasons: dict[str, str] = {}
    journal = state.root / "live" / "journal.jsonl"
    if journal.is_file():
        latest: dict[str, tuple[int, str | None]] = {}
        for record in _read_jsonl(journal, "question journal"):
            if record.get("kind") != "question_state" or not record.get("question_id"):
                continue
            qid, revision = record["question_id"], record.get("revision", 0)
            if qid not in latest or revision >= latest[qid][0]:
                latest[qid] = (revision, record.get("reason"))
        reasons = {qid: reason for qid, (_, reason) in latest.items() if reason}
    detail: dict[str, dict] = {}
    runs = state.done_runs()
    if runs:
        try:
            detail, _ = answers.load_runs(runs)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ReportError(f"A phase run folder cannot be read ({type(exc).__name__}); nothing was exported.") from exc
        detail = {qid: question for qid, question in detail.items() if question.get("kind") != "extra"}
    excluded = [phase["tag"] for phase in state.doc["phases"] if phase["status"] != "done"]
    return states, reasons, excluded, detail


def _load_declarations(state: SessionState) -> dict[str, str]:
    try:
        return anastylos.declare_states(state)
    except ValueError as exc:
        raise ReportError(f"The declaration journal cannot be read ({exc}); nothing was exported.") from exc


def _load_progress(state: SessionState, guide_mode: str) -> dict | None:
    if guide_mode != "fresh":
        return None
    try:
        return assemble.status(state)
    except guide.GuideError as exc:
        raise ReportError(f"{exc} Nothing was exported.") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ReportError(f"The assembly progress log cannot be read ({type(exc).__name__}); nothing was exported.") from exc

# ------------------------------------------------------------------ collect

def _memory_join(live: list[dict]) -> dict[str, dict]:
    return {event["id"]: event for event in live}


def _part_name(part: dict) -> str:
    label = part.get("operator_label")
    return label if label else f"automatic part {part['id']} (no operator label)"


def _origin_text(part: dict) -> tuple[str | None, str]:
    """The recorded original location (never the storage place) and how it was established."""
    source = part.get("source") or {}
    if part.get("basis") == "operator_declared":
        described = source.get("described_as")
        if described:
            return described, "operator statement"
        return None, "the operator did not state where it came from"
    site = source.get("site")
    if site:
        return f"site {site}", "observed source region"
    centroid = source.get("centroid_mm")
    if centroid:
        return f"anonymous spot at {centroid[0]:.0f}/{centroid[1]:.0f} mm", "observed source region"
    return None, "no source region recorded"


def _storage_text(part: dict) -> tuple[str | None, str]:
    """Where the part is stored (never its origin) and the basis of that placement."""
    stored = part.get("stored_in") or {}
    location = stored.get("location")
    if not location:
        return None, "no storage place recorded"
    if stored.get("inference"):
        return location, f"link concluded by {stored['inference'].get('inferred_by')}, not observed"
    if stored.get("basis") == "human":
        return location, "operator statement"
    return location, "recorded change in the cell, link conclusion not recorded"


def _part_evidence(part: dict, memory: dict[str, dict]) -> dict:
    """Evidence references as stored. Files are cited, never opened."""
    source, stored = part.get("source") or {}, part.get("stored_in") or {}
    record = memory.get(part["id"], {})
    evidence: dict[str, object] = {
        "record_ids": [part["id"]] + ([stored["record"]] if stored.get("record") else []),
        "removal_pair": source.get("evidence_pair"),
        "removal_region": None,
        "removal_result": _short(source.get("evidence_result_sha256")),
        "deposit_pair": None,
        "deposit_region": None,
        "deposit_result": None,
        "declared_by": record.get("declared_by"),
        "confirmed_by": stored.get("confirmed_by"),
        "recorded_at": record.get("timestamp"),
        "inference": None,
    }
    event = memory.get(part["id"])
    if event is not None and event.get("type") == "observation":
        ref = event.get("evidence") or {}
        evidence["removal_region"] = ref.get("region")
    link = memory.get(stored.get("record") or "")
    if isinstance(link, dict) and link.get("type") == "tray_link":
        basis = link.get("basis") or {}
        if basis.get("kind") == "evidence":
            evidence["deposit_pair"] = basis.get("pair_id")
            evidence["deposit_region"] = basis.get("region")
            evidence["deposit_result"] = _short(basis.get("result_sha256"))
        if link.get("inference"):
            inference = link["inference"]
            evidence["inference"] = {"inferred_by": inference.get("inferred_by"),
                                     "producer": inference.get("producer"),
                                     "assumptions": list(inference.get("assumptions") or [])}
    return evidence


def _part_missing(part: dict, memory: dict[str, dict]) -> list[str]:
    missing = []
    if not (part.get("stored_in") or {}).get("location"):
        missing.append("storage place")
    source = part.get("source") or {}
    if part.get("basis") == "operator_declared":
        if source.get("described_as") is None:
            missing.append("original location")
    elif not source.get("site") and not source.get("centroid_mm"):
        missing.append("original location")
    if part.get("t_s") is None:
        missing.append("removal time")
    event = memory.get(part["id"], {})
    observation = memory.get(event.get("observation_id") or "")
    if not event.get("evidence") and not (isinstance(observation, dict) and observation.get("evidence")):
        if part.get("basis") == "operator_declared":
            missing.append("direct image evidence")
    return missing


def _collect_parts(raw: dict, memory: dict[str, dict]) -> list[dict]:
    parts = []
    for item in raw.get("parts", []):
        origin, origin_basis = _origin_text(item)
        storage, storage_basis = _storage_text(item)
        group = item.get("group") or {}
        parts.append({
            "id": item["id"], "removal_index": item.get("removal_index"), "basis": item.get("basis"),
            "name": _part_name(item), "operator_label": item.get("operator_label"),
            "identity": item.get("identity"), "phase": item.get("phase_label") or item.get("phase"),
            "t_s": item.get("t_s"), "origin": origin, "origin_basis": origin_basis,
            "storage": storage, "storage_basis": storage_basis,
            "storage_record": (item.get("stored_in") or {}).get("record"),
            "source_note": (item.get("source") or {}).get("note") or (item.get("source") or {}).get("evidence_note"),
            "storage_note": (item.get("stored_in") or {}).get("note"),
            "group": {"id": group.get("id"), "label": group.get("label"), "members": list(group.get("members") or []),
                      "basis": (group.get("basis") or ""), "note": group.get("note")} if group.get("id") else None,
            "custody": [{"proposal": entry.get("proposal"), "status": entry.get("status"),
                         "removal_site": entry.get("removal_site")} for entry in item.get("custody_history") or []],
            "evidence": _part_evidence(item, memory), "missing": _part_missing(item, memory),
        })
    return parts


def _collect_memory_only(live: list[dict]) -> list[dict]:
    """Removal records without a guide package: log order, no derived order or plan."""
    storage: dict[str, dict] = {}
    for event in live:
        if event["type"] == "tray_link":
            storage[event["observation_id"]] = {"location": f"cell-{event['tray']['number']}",
                                               "record": event["id"], "basis": event["basis"]["kind"]}
        elif event["type"] == "storage_link":
            storage[event["target_id"]] = {"location": event["location"], "record": event["id"], "basis": "human"}
    phase_label = {event["id"]: event["label"] for event in live if event["type"] == "phase"}
    parts = []
    for event in live:
        if event["type"] == "observation":
            ref = event.get("evidence") or {}
            parts.append({"id": event["id"], "removal_index": None, "basis": "automatic",
                          "name": f"automatic part {event['id']} (no operator label)", "operator_label": None,
                          "identity": "evidence of a removal, not a verified part",
                          "phase": None, "t_s": event.get("t_s"),
                          "origin": f"anonymous spot at {event['centroid_mm'][0]:.0f}/{event['centroid_mm'][1]:.0f} mm",
                          "origin_basis": "observed source region",
                          "storage": (storage.get(event["id"]) or {}).get("location"),
                          "storage_basis": "see memory record", "storage_record": (storage.get(event["id"]) or {}).get("record"),
                          "source_note": event.get("note"), "storage_note": None, "group": None, "custody": [],
                          "evidence": {"record_ids": [event["id"]], "removal_pair": ref.get("pair_id"),
                                      "removal_region": ref.get("region"), "removal_result": _short(ref.get("result_sha256")),
                                      "deposit_pair": None, "deposit_region": None, "deposit_result": None,
                                      "declared_by": None, "confirmed_by": None, "recorded_at": event.get("timestamp"),
                                      "inference": (event.get("inference") if event.get("inference") else None)},
                          "missing": ([]
                                      if (storage.get(event["id"]) or {}).get("location") else ["storage place"])
                          + ([] if event.get("t_s") is not None else ["removal time"])})
        elif event["type"] == "declared_removal":
            stored = storage.get(event["id"]) or {}
            parts.append({"id": event["id"], "removal_index": None, "basis": "operator_declared",
                          "name": event["operator_label"], "operator_label": event["operator_label"],
                          "identity": "operator's label, not verified by the system",
                          "phase": phase_label.get(event.get("phase")), "t_s": event.get("t_s"),
                          "origin": event.get("source"),
                          "origin_basis": ("operator statement" if event.get("source") is not None
                                           else "the operator did not state where it came from"),
                          "storage": stored.get("location"),
                          "storage_basis": ("operator statement" if stored.get("location") else "no storage place recorded"),
                          "storage_record": stored.get("record"), "source_note": event.get("evidence_note"),
                          "storage_note": None, "group": None, "custody": [],
                          "evidence": {"record_ids": [event["id"]] + ([stored["record"]] if stored.get("record") else []),
                                      "removal_pair": None, "removal_region": None, "removal_result": None,
                                      "deposit_pair": None, "deposit_region": None, "deposit_result": None,
                                      "declared_by": event.get("declared_by"), "confirmed_by": None,
                                      "recorded_at": event.get("timestamp"), "inference": None},
                          "missing": (([] if event.get("source") is not None else ["original location"])
                                      + ([] if stored.get("location") else ["storage place"])
                                      + ([] if event.get("t_s") is not None else ["removal time"])
                                      + ["direct image evidence"])})
    return parts


def collect(session_dir: str | Path) -> dict:
    """Read the session once, read-only, into a plain data model for the renderer.

    Raises :class:`ReportError` when the memory or the recorded guide cannot
    be trusted, and the session loader's ``SessionError`` when the session
    file itself is missing or invalid. Missing optional logs (runs, journals,
    progress) become visible notices, never invented content.
    """
    state = SessionState.load(Path(session_dir))
    memory_sha, events, live = _load_memory(state)
    memory = _memory_join(live)
    guide_mode, raw, build = _load_guide(state)
    question_states, question_reasons, excluded, run_questions = _load_questions(state)
    declarations = _load_declarations(state)
    progress = _load_progress(state, guide_mode)

    try:
        status = anastylos.summary(state)
    except (ValueError, KeyError, TypeError) as exc:
        raise ReportError(f"The session state cannot be summarised ({type(exc).__name__}); nothing was exported.") from exc

    by_type: dict[str, int] = {}
    for event in live:
        by_type[event["type"]] = by_type.get(event["type"], 0) + 1

    parts = _collect_parts(raw, memory) if raw is not None else _collect_memory_only(live)
    open_questions = sorted(qid for qid, value in question_states.items() if value in OPEN_STATES)
    unresolved = sorted(qid for qid, value in question_states.items() if value == "LEFT_UNRESOLVED")

    install = None
    if raw is not None:
        order = raw.get("install_order") or {}
        install = {"basis": order.get("basis"),
                   "dependencies_note": (raw.get("dependencies") or {}).get("note"),
                   "confirmed": list((raw.get("dependencies") or {}).get("confirmed") or []),
                   "steps": [{"step": step["step"], "parts": list(step.get("parts") or [])} for step in order.get("steps", [])]}

    videos = [{"tag": phase["tag"], "file": _basename(phase.get("video")), "sha256": phase.get("video_sha256"),
               "status": phase.get("status"), "label": phase.get("label"), "counts": phase.get("counts")}
              for phase in state.doc.get("phases", [])]
    inputs = {}
    if raw is not None:
        for name, entry in (raw.get("inputs") or {}).items():
            inputs[name] = None if not entry else {"file": _basename(entry.get("path")), "sha256": entry.get("sha256")}

    assembly = None
    if progress is not None:
        per_step = []
        for step in progress["steps"]:
            record = (progress["latest"] or {}).get(step["step"]) or {}
            per_step.append({"step": step["step"], "parts": list(step.get("parts") or []),
                             "citable": step.get("citable"), "accepted": bool(step.get("accepted")),
                             "text": step.get("text"), "event": record.get("event"),
                             "basis": record.get("basis"), "return_id": record.get("return_id"),
                             "return_status": record.get("status"),
                             "returns_sha256": record.get("returns_sha256")})
        done = sum(1 for row in per_step if row["event"] in (assemble.DONE, assemble.VERIFIED))
        assembly = {"complete": bool(progress["complete"]), "steps_total": len(per_step), "steps_done": done,
                    "current_step": (progress["current"] or {}).get("step"), "per_step": per_step,
                    "other_packages": sorted(progress.get("progress_for_other_packages") or [])}

    questions = {}
    for qid in sorted(set(question_states) | set(run_questions)):
        detail = run_questions.get(qid) or {}
        window = detail.get("deposit_window_s") or detail.get("window_s")
        questions[qid] = {"state": question_states.get(qid, "not asked yet"),
                          "reason": question_reasons.get(qid), "kind": detail.get("kind"),
                          "part": detail.get("part"), "cell": detail.get("cell"),
                          "removal_event": detail.get("removal_event"), "window_s": window,
                          "why": list(detail.get("why") or []),
                          "candidates": [{"removal_event": c.get("removal_event"), "window_s": c.get("window_s")}
                                         for c in detail.get("candidates") or []]}
    return {
        "schema": REPORT_SCHEMA, "generator": GENERATOR,
        "session": {"folder": Path(state.root).name, "object": state.doc.get("object"),
                    "operator": state.doc.get("operator"), "created": state.doc.get("created")},
        "status": {"complete": bool(status["complete"]), "incomplete": list(status.get("incomplete") or []),
                   "questions_by_state": dict(status.get("questions") or {}), "declarations": declarations},
        "memory": {"sha256": memory_sha, "events": len(events), "live": len(live),
                   "superseded": len(events) - len(live), "by_type": by_type},
        "guide": {"mode": guide_mode,
                  "package_sha256": (build or {}).get("package_sha256") if build else None,
                  "memory_sha256": (build or {}).get("memory_sha256") if build else None,
                  "builder": (build or {}).get("builder") if build else None,
                  "builder_sha256": (build or {}).get("builder_sha256") if build else None,
                  "built_at": (build or {}).get("t_wall") if build else None, "inputs": inputs},
        "videos": videos, "parts": parts, "install": install, "assembly": assembly,
        "unknowns": list(raw.get("unknowns") or []) if raw is not None else [],
        "questions": questions, "open_questions": open_questions, "unresolved": unresolved,
        "excluded_phases": excluded,
        "notices": ([{"kind": "no-guide",
                       "text": "No guide package has been built for this session, so there is no recorded install order, "
                               "no dependency statement and no unknowns list. Removal records below come from the memory log "
                               "in log order. Run `guide build`, then export again for the full report."}]
                    if guide_mode == "none" else [])
        + ([{"kind": "no-runs", "text": "No finished phase run is recorded; question details are unavailable."}]
           if not state.done_runs() else []),
    }

# ------------------------------------------------------------------ render

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: #1c1c1e; font: 16px/1.55 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 1000px; margin: 0 auto; padding: 32px 24px 64px; }
h1 { font-size: 1.7rem; line-height: 1.25; margin: 0 0 4px; }
.subtitle { font-size: 1.05rem; margin: 0 0 20px; }
h2 { font-size: 1.25rem; margin: 40px 0 12px; padding-bottom: 6px; border-bottom: 2px solid #1c1c1e; }
h3 { font-size: 1.05rem; margin: 24px 0 8px; }
p, li { overflow-wrap: break-word; }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px 24px; margin: 16px 0; }
.meta div { border-top: 1px solid #d9d9d6; padding: 6px 0; }
.meta dt { font-size: 0.8rem; color: #444; margin: 0; }
.meta dd { margin: 2px 0 0; overflow-wrap: break-word; }
.box { border: 1px solid #8e8e93; padding: 12px 16px; margin: 16px 0; background: #f4f4f2; }
.box.warning { border-color: #8a5a00; background: #faf3e3; }
.box p { margin: 6px 0; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 0.92rem; }
caption { text-align: left; font-weight: bold; padding: 0 0 6px; }
th, td { border: 1px solid #c7c7cc; padding: 6px 8px; text-align: left; vertical-align: top; overflow-wrap: break-word; }
td.tight { white-space: nowrap; }
th { background: #ececea; }
tbody tr:nth-child(even) td { background: #f7f7f5; }
ol.sequence li, ul.plain li { margin: 3px 0; }
code, .mono { font-family: Consolas, "SF Mono", Menlo, monospace; font-size: 0.85em; overflow-wrap: break-word; }
.badge { display: inline-block; font-size: 0.75rem; font-weight: bold; padding: 1px 8px; margin: 1px 2px 1px 0;
         border: 1px solid #555; border-radius: 4px; background: #fff; color: #222; white-space: nowrap; }
.badge.op { border-color: #1a5fb4; color: #1a5fb4; }
.badge.auto { border-color: #3a3a3c; color: #3a3a3c; }
.badge.inf { border-color: #8a5a00; color: #8a5a00; }
.badge.grp { border-color: #20603c; color: #20603c; }
.badge.done { border-color: #20603c; color: #20603c; }
.badge.warn { border-color: #8a5a00; color: #8a5a00; }
.badge.bad { border-color: #a4262c; color: #a4262c; }
.missing { color: #6e6e72; font-style: italic; }
.small { font-size: 0.85rem; color: #333; }
footer { margin-top: 48px; padding-top: 12px; border-top: 1px solid #c7c7cc; font-size: 0.85rem; color: #444; }
@media print {
  body { font-size: 11pt; }
  main { max-width: none; padding: 0; }
  table { font-size: 9.5pt; }
  thead { display: table-header-group; }
  tr, li { break-inside: avoid; }
  h2 { break-after: avoid; }
  .box { background: #fff; }
  tbody tr:nth-child(even) td { background: #fff; }
}
"""


def _badge(label: str, kind: str) -> str:
    kinds = {"op": "op", "auto": "auto", "inf": "inf", "grp": "grp", "done": "done",
             "warn": "warn", "bad": "bad", "": ""}
    return f'<span class="badge {kinds.get(kind, "")}">{esc(label)}</span>'


def _missing(label: str = "not recorded") -> str:
    return f'<span class="missing">{esc(label)}</span>'


def _value(text) -> str:
    return _missing() if text is None or str(text) == "" else esc(text)


def _mono(text) -> str:
    return _missing() if text is None or str(text) == "" else f"<code>{esc(text)}</code>"


def _fmt_t(t_s) -> str:
    return _missing("no time") if t_s is None else esc(f"{t_s:.1f} s")


def _fmt_window(window) -> str:
    if not window or len(window) != 2:
        return _missing("no time window")
    return esc(f"{window[0]:.1f}–{window[1]:.1f} s")


def _basis_badges(part: dict) -> str:
    if part.get("basis") == "operator_declared":
        out = _badge("operator statement", "op")
    else:
        out = _badge("system observation", "auto")
    if (part.get("evidence") or {}).get("inference"):
        out += _badge("link inferred, not observed", "inf")
    elif part.get("basis") == "automatic" and part.get("storage"):
        out += _badge("link conclusion unrecorded", "warn")
    if part.get("group"):
        out += _badge("interchangeable group", "grp")
    return out


def _question_badge(state_name: str) -> str:
    if state_name == "COMMITTED":
        return _badge("answered", "done")
    if state_name == "LEFT_UNRESOLVED":
        return _badge("left unresolved", "bad")
    if state_name in OPEN_STATES or state_name == "not asked yet":
        return _badge("open", "warn")
    return _badge(state_name.lower().replace("_", " "), "")


def _table(headers: list[str], rows: list[str], caption: str | None = None) -> str:
    head = "".join(f"<th>{esc(item)}</th>" for item in headers)
    body = "".join(f"<tr>{row}</tr>" for row in rows)
    cap = f"<caption>{esc(caption)}</caption>" if caption else ""
    return f"<table>{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _section(anchor: str, title: str, body: str) -> str:
    return f'<section id="{esc(anchor)}"><h2>{esc(title)}</h2>{body}</section>'


ABOUT_BOX = """
<div class="box">
<p><strong>How to read this report.</strong> Each part row keeps its <strong>original location</strong>
(where it was removed from) separate from its <strong>storage place</strong> (where it is kept now).
Operator words are quoted verbatim and stay operator statements; they are never upgraded to observed
facts. Automatic parts are removals the system recorded; the link from a source to a cell was
concluded by a named rule and is labelled as such.</p>
<p><strong>Limits.</strong> Part identity is never verified. The install order below is the reverse of
the recorded removal order unless a dependency was confirmed, which none was; it is a proposal for the
operator, not a mechanically checked assembly sequence. No image is embedded and no video frame is
claimed to confirm any record. Missing information is shown as missing.</p>
</div>
"""

FIGURES_NOTE = """
<div class="box">
<p><strong>No images are embedded.</strong> The agent session binds evidence by pair, region and
file hash, not by confirmed frame pictures, so a faithful report cites those references instead of
guessing pictures from file names, labels or times. What is listed under evidence below is reported
as stored; files were not opened at export and no frame-to-record confirmation is established by
this report.</p>
</div>
"""


def _header(data: dict) -> str:
    session, memory, gr = data["session"], data["memory"], data["guide"]
    package = _mono(_short(gr["package_sha256"])) if gr["package_sha256"] else _missing("no guide package")
    state = _badge("complete", "done") if data["status"]["complete"] else _badge("incomplete", "warn")
    return f"""
<h1>Session report</h1>
<p class="subtitle">{esc(session['object'])} {state}</p>
<dl class="meta">
<div><dt>Session folder</dt><dd>{esc(session['folder'])}</dd></div>
<div><dt>Operator</dt><dd>{esc(session['operator'])}</dd></div>
<div><dt>Session created</dt><dd>{esc(session['created'])}</dd></div>
<div><dt>Memory</dt><dd>{_mono(_short(memory['sha256']))} ({esc(memory['live'])} live events)</dd></div>
<div><dt>Guide package</dt><dd>{package}</dd></div>
<div><dt>Documented parts</dt><dd>{esc(len(data['parts']))}</dd></div>
</dl>
"""


def _notices(data: dict) -> str:
    out = ""
    for notice in data["notices"]:
        out += f'<div class="box warning"><p><strong>Notice.</strong> {esc(notice["text"])}</p></div>'
    return out


def _data_state(data: dict) -> str:
    rows = []
    for video in data["videos"]:
        counts = video["counts"] or {}
        count_text = ", ".join(f"{key}: {counts[key]}" for key in sorted(counts)) if counts else "not recorded"
        rows.append(f"<td>{esc(video['tag'])}</td><td>{_value(video['label'])}</td>"
                    f"<td>{esc(video['status'])}</td><td>{_value(video['file'])}</td>"
                    f"<td>{_mono(_short(video['sha256']))}</td><td>{esc(count_text)}</td>")
    phases = _table(["Phase", "Operator label", "Status", "Video file", "Video SHA-256", "Counts"], rows,
                    "Phases and bound videos (file names only; paths are not shared)")
    memory, gr = data["memory"], data["guide"]
    by_type = ", ".join(f"{key}: {memory['by_type'][key]}" for key in sorted(memory["by_type"])) or "no events"
    if gr["mode"] == "fresh":
        guide_rows = (f"<tr><td>Package SHA-256</td><td>{_mono(gr['package_sha256'])}</td></tr>"
                      f"<tr><td>Built from memory</td><td>{_mono(gr['memory_sha256'])} (matches current memory)</td></tr>"
                      f"<tr><td>Built at</td><td>{esc(gr['built_at'])}</td></tr>"
                      f"<tr><td>Builder</td><td>{esc(gr['builder'])} ({_mono(_short(gr['builder_sha256']))})</td></tr>")
        for name in sorted(gr["inputs"]):
            entry = gr["inputs"][name]
            cell = _missing("not used") if not entry else f"{esc(entry['file'])} ({_mono(_short(entry['sha256']))})"
            guide_rows += f"<tr><td>Input {esc(name)}</td><td>{cell}</td></tr>"
        guide_block = f"<h3>Recorded guide</h3><table><tbody>{guide_rows}</tbody></table>"
    else:
        guide_block = '<div class="box"><p><strong>No guide package.</strong> The install order, dependency statement and unknowns list are unavailable.</p></div>'
    incomplete = "".join(f"<li>{esc(item)}</li>" for item in data["status"]["incomplete"]) or "<li>None.</li>"
    by_state = ", ".join(f"{key}: {data['status']['questions_by_state'][key]}"
                         for key in sorted(data["status"]["questions_by_state"])) or "no question records"
    declarations = "".join(f"<li>{_mono(qid)}: {esc(value)}</li>" for qid, value in sorted(data["status"]["declarations"].items()))
    return f"""{phases}
<h3>Memory and build</h3>
<p>Memory SHA-256 {_mono(memory['sha256'])}; {esc(memory['live'])} live of {esc(memory['events'])} events
({esc(memory['superseded'])} superseded). Live events by type: {esc(by_type)}.</p>
{guide_block}
<h3>Session state</h3>
<p>Question records by state: {esc(by_state)}.</p>
<p>What keeps this session incomplete:</p><ul class="plain">{incomplete}</ul>
<h3>Operator declarations</h3>
""" + (f"<ul class=\"plain\">{declarations}</ul>" if declarations else "<p>No declarations recorded.</p>")


def _parts_table(data: dict) -> str:
    rows = []
    for part in data["parts"]:
        number = part["removal_index"] if part["removal_index"] is not None else "—"
        origin = _value(part["origin"]) + f'<br><span class="small">{esc(part["origin_basis"])}</span>'
        storage = (_value(part["storage"]) + f'<br><span class="small">{esc(part["storage_basis"])}</span>'
                   if part["storage"] else _missing("no storage place"))
        group = part.get("group") or {}
        group_text = _missing("—") if not group else f"{esc(group['id'])}: {esc(group.get('label') or 'unnamed group')}"
        missing = ", ".join(part["missing"]) if part["missing"] else "none"
        rows.append(f"<td class=\"tight\">{esc(number)}</td><td class=\"tight\">{_mono(part['id'])}</td>"
                    f"<td>{_value(part['name'])}</td><td>{_basis_badges(part)}</td>"
                    f"<td class=\"tight\">{_value(part['phase'])}</td><td class=\"tight\">{_fmt_t(part['t_s'])}</td>"
                    f"<td>{origin}</td><td>{storage}</td><td>{group_text}</td><td>{esc(missing)}</td>")
    if not rows:
        return "<p>No removal records.</p>"
    return _table(["#", "ID", "Part", "Basis", "Phase", "Removed at", "Original location",
                   "Storage place", "Group", "Missing"], rows,
                  "Documented parts in removal order (origin and storage are separate facts)")


def _removal_sequence(data: dict) -> str:
    items = []
    for number, part in enumerate(data["parts"], 1):
        label = part["operator_label"] or "no operator label"
        items.append(f"<li>{esc(number)}. {_mono(part['id'])} — {esc(label)} "
                     f"({_value(part['phase'])}, {_fmt_t(part['t_s'])})</li>")
    if not items:
        return "<p>No removal records.</p>"
    note = ("<p>Order within a phase follows the recorded removal time; parts without a time keep log order.</p>"
            if data["guide"]["mode"] == "fresh" else "<p>Log order; removal-time ordering needs a built guide.</p>")
    return note + f'<ol class="sequence">{"".join(items)}</ol>'


def _install_plan(data: dict) -> str:
    install = data["install"]
    if install is None:
        return ('<div class="box"><p><strong>No recorded assembly plan.</strong> Install steps are written only by '
                "<code>guide build</code>; nothing is proposed here instead.</p></div>")
    steps = []
    assembly_text = {}
    if data["assembly"] is not None:
        assembly_text = {row["step"]: row for row in data["assembly"]["per_step"]}
    for step in install["steps"]:
        row = assembly_text.get(step["step"], {})
        instruction = row.get("text") or "Step instruction unavailable."
        accepted = "" if row.get("accepted", True) else f" {_badge('not verified', 'bad')}"
        parts = ", ".join(step["parts"])
        steps.append(f"<td class=\"tight\">{esc(step['step'])}</td><td class=\"tight\">{_mono(parts)}</td>"
                       f"<td>{esc(instruction)}{accepted}</td>")
    confirmed = "".join(f"<li>{esc(item)}</li>" for item in install["confirmed"])
    return f"""
<div class="box warning"><p><strong>Not a verified assembly sequence.</strong> The steps below are the reverse of the
recorded removal order. No mechanical dependency was observed or confirmed. Follow them only with the operator's own
judgement; the system approves nothing.</p></div>
<p>Basis as recorded: {esc(install['basis'])}</p>
{_table(["Step", "Parts", "Instruction"], steps, "Recorded install steps")}
<h3>Confirmed dependencies</h3>
""" + (f"<ul class=\"plain\">{confirmed}</ul>" if confirmed
       else f"<p>None. {esc(install['dependencies_note'])}</p>")


def _open_questions(data: dict) -> str:
    rows = []
    for qid, question in data["questions"].items():
        if question["state"] in ("COMMITTED", "SUPERSEDED"):
            continue
        about = question["part"] or question["cell"] or question["removal_event"] or "session"
        candidates = ([c["removal_event"] for c in question["candidates"] if c.get("removal_event")]
                      or [f"{len(question['candidates'])} offered"])
        detail = f"{esc(question['kind'] or 'question')} about {esc(about)}, {_fmt_window(question['window_s'])}"
        if question["candidates"]:
            detail += f"; candidates: {esc(', '.join(candidates))}"
        if question["why"]:
            detail += f"; {esc('; '.join(question['why']))}"
        if question["reason"]:
            detail += f" ({esc(question['reason'])})"
        rows.append(f"<td class=\"tight\">{_mono(qid)}</td><td>{_question_badge(question['state'])}</td><td>{detail}</td>")
    if rows:
        questions_block = _table(["Question", "State", "Detail"], rows, "Open questions (nothing is answered here)")
    else:
        questions_block = "<p>No open questions. Every recorded question reached an answer, was confirmed as not-a-part, or is listed below as unresolved.</p>"
    if data["excluded_phases"]:
        questions_block += ("<p>Phases not included because they did not finish processing: "
                            f"{esc(', '.join(data['excluded_phases']))}.</p>")
    unknowns = "".join(f"<li>{esc(item)}</li>" for item in data["unknowns"])
    if data["guide"]["mode"] == "fresh":
        unknowns_block = f"<ul class=\"plain\">{unknowns}</ul>" if unknowns else "<p>None recorded.</p>"
    else:
        unknowns_block = "<p>Unavailable without a built guide package.</p>"
    missing_rows = []
    for part in data["parts"]:
        if part["missing"]:
            missing_rows.append(f"<td>{_mono(part['id'])}</td><td>{esc(', '.join(part['missing']))}</td>")
    missing_block = (_table(["Part", "Missing fields"], missing_rows, "Missing information per part")
                   if missing_rows else "<p>Every part has its origin, storage place and removal time recorded.</p>")
    return f"{questions_block}<h3>What the session does not know</h3>{unknowns_block}<h3>Missing fields</h3>{missing_block}"


def _progress(data: dict) -> str:
    assembly = data["assembly"]
    if assembly is None:
        return "<p>No assembly plan, so no progress can be confirmed against one.</p>"
    rows = []
    for row in assembly["per_step"]:
        event = row["event"]
        if event == "verified":
            status = _badge("verified by return check", "done")
        elif event == "done":
            status = _badge("done, by operator", "done")
        elif event == "verification_failed":
            status = _badge("needs attention: return check mismatch", "bad")
        else:
            status = _badge("pending", "")
        ref = ""
        if row["return_id"]:
            ref = f"{esc(row['return_id'])} ({esc(row['return_status'] or 'no status')}, {_mono(_short(row['returns_sha256']))})"
        rows.append(f"<td class=\"tight\">{esc(row['step'])}</td><td class=\"tight\">{_mono(', '.join(row['parts']))}</td>"
                    f"<td>{status}</td><td>{ref or _missing('—')}</td>")
    summary = (f"{esc(assembly['steps_done'])} of {esc(assembly['steps_total'])} steps confirmed; "
               + ("reassembly complete according to the recorded steps." if assembly["complete"]
                  else f"current step: {esc(assembly['current_step'])}."))
    other = ""
    if assembly["other_packages"]:
        other = ("<p>Progress records for older guide builds are kept but not applied: "
                 f"{esc(', '.join(assembly['other_packages']))}.</p>")
    return f"<p>{summary}</p>" + _table(["Step", "Parts", "Confirmation", "Return check"], rows,
                                        "Progress confirmations (operator done vs return-checked)") + other


def _provenance(data: dict) -> str:
    memory, gr = data["memory"], data["guide"]
    hash_rows = f"<tr><td>memory.jsonl</td><td>{_mono(memory['sha256'])}</td></tr>"
    if gr["package_sha256"]:
        hash_rows += f"<tr><td>guide session.json</td><td>{_mono(gr['package_sha256'])}</td></tr>"
    for video in data["videos"]:
        name = video["file"] or f"phase {video['tag']} video"
        hash_rows += f"<tr><td>video {esc(video['tag'])} ({esc(name)})</td><td>{_mono(video['sha256'])}</td></tr>"
    hashes = f"<table><tbody>{hash_rows}</tbody></table>"
    rows = []
    for part in data["parts"]:
        evidence = part["evidence"]
        refs = []
        if evidence["removal_pair"]:
            refs.append(f"removal {esc(evidence['removal_pair'])}/{esc(evidence['removal_region'] or '?')} "
                        f"({_mono(evidence['removal_result'])})")
        if evidence["deposit_pair"]:
            refs.append(f"deposit {esc(evidence['deposit_pair'])}/{esc(evidence['deposit_region'] or '?')} "
                        f"({_mono(evidence['deposit_result'])})")
        inference = evidence.get("inference") or {}
        if inference:
            refs.append(f"concluded by {esc(inference.get('inferred_by'))} "
                        f"({esc(inference.get('producer'))}); assumes: {esc('; '.join(inference.get('assumptions') or []))}")
        who = [f"stated by {esc(evidence['declared_by'])}" if evidence["declared_by"] else "",
               f"storage confirmed by {esc(evidence['confirmed_by'])}" if evidence["confirmed_by"] else "",
               f"recorded {esc(evidence['recorded_at'])}" if evidence["recorded_at"] else ""]
        who = "; ".join(item for item in who if item)
        notes = "; ".join(item for item in [part.get("source_note"), part.get("storage_note")] if item)
        rows.append(f"<td class=\"tight\">{_mono(part['id'])}</td><td>{_mono(', '.join(evidence['record_ids']))}</td>"
                    f"<td>{'<br>'.join(refs) if refs else _missing('no evidence references')}</td>"
                    f"<td>{esc(who) if who else _missing('—')}</td><td>{esc(notes) if notes else _missing('—')}</td>")
    table = _table(["Part", "Records", "Evidence references", "Who and when", "Notes"], rows,
                   "Evidence and provenance per part") if rows else "<p>No removal records.</p>"
    return f"""<h3>Bound file hashes</h3>{hashes}
{table}
<div class="box"><p><strong>How to check.</strong> Recompute each SHA-256 over the file bytes and compare. Evidence
references above are cited as stored; result files were not opened at export, paths are reduced to file names, and
no reference here establishes that a picture shows a particular record.</p></div>
"""

# ------------------------------------------------------------------ document

def render_html(data: dict) -> str:
    """The standalone report document. Deterministic: the same data gives the same bytes.

    Every input string passes through :func:`esc`; the document carries no
    scripts, no external references and no export timestamp.
    """
    title = f"Session report — {data['session']['object']}"
    body = (
        _header(data)
        + ABOUT_BOX
        + _notices(data)
        + _section("data-state", "Session and bound data", _data_state(data))
        + _section("parts", "Documented parts and storage", _parts_table(data))
        + _section("removal-order", "Documented removal order", _removal_sequence(data))
        + _section("assembly-plan", "Recorded assembly plan", _install_plan(data))
        + _section("open-questions", "Open questions and missing information", _open_questions(data))
        + _section("progress", "Progress confirmations", _progress(data))
        + _section("provenance", "Evidence references and provenance", _provenance(data))
        + _section("figures", "Figures", FIGURES_NOTE)
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<main>
{body}
<footer>
<p>{esc(GENERATOR)} · schema {esc(REPORT_SCHEMA)} · standalone file: no external content, no scripts.
Regenerate with <code>report_cli.py --session &lt;dir&gt; --out &lt;report.html&gt;</code> after
<code>guide build</code>.</p>
</footer>
</main>
</body>
</html>
"""


# ------------------------------------------------------------------ export

def export(session_dir: str | Path, out_path: str | Path) -> dict:
    """Export the session at ``session_dir`` to standalone HTML at ``out_path``.

    Read-only toward the session: only ``out_path`` is written (atomically).
    Returns the stable result document (``REPORT.md``). Raises
    :class:`ReportError` (or the session loader's ``SessionError``) with an
    English message instead of writing anything untrustworthy.
    """
    data = collect(session_dir)
    document = render_html(data).encode("utf-8")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    partial.write_bytes(document)
    os.replace(partial, out)
    assembly = data["assembly"]
    return {
        "schema": EXPORT_SCHEMA,
        "report": str(out_path),
        "report_sha256": _sha256(document),
        "session": data["session"]["folder"],
        "object": data["session"]["object"],
        "memory_sha256": data["memory"]["sha256"],
        "package_sha256": data["guide"]["package_sha256"],
        "guide": data["guide"]["mode"],
        "parts": len(data["parts"]),
        "install_steps": len(data["install"]["steps"]) if data["install"] is not None else None,
        "open_questions": list(data["open_questions"]),
        "left_unresolved": list(data["unresolved"]),
        "assembly": None if assembly is None else {
            "complete": assembly["complete"], "steps_done": assembly["steps_done"],
            "steps_total": assembly["steps_total"], "current_step": assembly["current_step"]},
    }
