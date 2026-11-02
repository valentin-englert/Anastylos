"""Narrow adapter: the session UI's only access to the product modules.

Every behavior below is owned elsewhere and reused unchanged:

- session file and status shape: ``tools/agent/state.py`` + ``tools/agent/anastylos.py::summary``
  (``complete`` included: open or unresolved questions still report ``complete: false``)
- question order, wording, interpretation, read-back, commit: ``tools/live/coordinator.py``
  driven through ``tools/agent/ask_loop.py`` (``open_coordinator``, ``next_question``,
  ``operator_input``, ``MAX_ASKS``/``MAX_UNCLEAR``). The UI answers one step per request; the
  retry transitions mirror ``ask_loop.ask_one``/``_confirm`` (same coordinator calls, same
  defer reasons, same operator messages) because the CLI loop is a blocking console loop.
- guide package, L0 claims, verifier, renderer, answer cache: ``tools/agent/guide.py``,
  ``tools/guide/claims.py``, ``tools/agent/guide_render.py``. The UI never wires a proposer
  into unselected guide answers: sources are L0, cache or refusal only.
- install steps and assembly progress: ``tools/agent/assemble.py`` (``status``, ``mark_done``).
- drawing selection and selected origin/storage: ``tools/agent/selection.py`` and
  ``tools/agent/selected_guide.py``. Bindings come from explicit files; nothing here searches
  labels or recomputes hashes. Stored-claims replay uses ``tools/live/providers/fake.py``
  exactly like the CLI ``--claims`` path and is reported as ``offline_claims``, never Muse.
- session report: ``tools/agent/report.py::export`` (fail-closed freshness check inside;
  only the server-fixed ``report.html`` is written, session inputs are never touched).
- capture needs: ``tools/agent/capture_gaps/planner.py::evaluate`` (pure, no I/O).
  Gap files and evidence bytes are server-side prepared data; the current binding is
  derived fresh from the actual session bytes on every evaluation. There is no
  technical image check in this build, so closure stays locked: no endpoint submits
  reviews and none is synthesized.

No function here reimplements guide, selection, budget, verifier or progress logic.
No HTTP call is possible: no provider module is imported besides the offline fake.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parent
REPO = UI_ROOT.parents[1]
AGENT = REPO / "tools" / "agent"
LIVE = REPO / "tools" / "live"
for _path in (AGENT, LIVE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import anastylos  # noqa: E402  (product orchestrator, reused unchanged)
import ask_loop  # noqa: E402
import assemble  # noqa: E402
import capture_gaps.planner as capture_planner  # noqa: E402  (pure evaluator, no I/O)
import guide  # noqa: E402
import report  # noqa: E402  (standalone HTML export, fail-closed)
import selected_guide  # noqa: E402
import selection  # noqa: E402
import states as st  # noqa: E402  (tools/live)
from lock import LockHeld  # noqa: E402
from state import SessionError, SessionState  # noqa: E402

SELECTIONS_DIR = "selections"
STORED_CLAIMS_DIR = "selected_claims"
SELECTED_CACHE_NAME = "selected_answers.jsonl"
REPORT_NAME = "report.html"
GAPS_DIR = "capture_gaps"
GAPS_SUBDIR = "gaps"
EVIDENCE_SUBDIR = "evidence"
EVIDENCE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def check_workspace_root(root: Path) -> Path:
    """The configured workspace must be outside the repo and outside active memories."""
    resolved = root.resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise AdapterError(f"Refusing {resolved}: the workspace lives outside the repository.", 500)
    pointer = anastylos.ACTIVE_MEMORY_POINTER
    if pointer.is_file():
        entries = json.loads(pointer.read_text(encoding="utf-8"))
        for entry in entries.values():
            if not isinstance(entry, dict) or "active" not in entry:
                continue
            protected = (pointer.parent / entry["active"]).resolve()
            if resolved == protected or protected in resolved.parents:
                raise AdapterError(f"Refusing {resolved}: inside the active memory {protected}.", 500)
    return resolved

# Journal notes written by Coordinator._note for unusable replies (tools/live/coordinator.py).
_ANSWER_UNCLEAR_NOTES = ("not understood; asked again", "answers.plan rejected: ")
_CONFIRM_UNCLEAR_NOTES = ("read-back answer not understood",)
# Notes that keep the attempt counter (ask_loop.ask_one continues after a rejected read-back).
_PRESERVING_NOTES = ("operator rejected the read-back", "answers.plan rejected at commit")


class AdapterError(Exception):
    """An operator-facing failure with the HTTP status the server should answer."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ id resolution (no free paths)

def workspace_sessions(root: Path) -> list[Path]:
    """Session dirs under the configured workspace root (sorted by name)."""
    if not root.is_dir():
        raise AdapterError(f"Workspace root {root} does not exist.", 500)
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and (p / "agent-session.json").is_file())


def resolve_session(root: Path, session_id: str) -> Path:
    """A session id is a plain directory name; anything else is rejected."""
    if not session_id or session_id in (".", "..") or "/" in session_id or "\\" in session_id:
        raise AdapterError(f"Unknown session {session_id!r}.", 404)
    candidate = (root / session_id).resolve()
    if candidate.parent != root.resolve() or not (candidate / "agent-session.json").is_file():
        raise AdapterError(f"Unknown session {session_id!r}.", 404)
    return candidate


def resolve_named(root: Path, session_id: str, subdir: str, name: str) -> Path:
    """An allowlisted ``<subdir>/<name>.json`` file inside the session (no separators)."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or not name.endswith(".json"):
        raise AdapterError(f"Unknown {subdir} entry {name!r}.", 404)
    session_dir = resolve_session(root, session_id)
    candidate = (session_dir / subdir / name).resolve()
    if candidate.parent != (session_dir / subdir).resolve() or not candidate.is_file():
        raise AdapterError(f"Unknown {subdir} entry {name!r}.", 404)
    return candidate


def _load_state(root: Path, session_id: str) -> SessionState:
    try:
        return SessionState.load(resolve_session(root, session_id))
    except SessionError as exc:
        raise AdapterError(str(exc), 409) from exc


def _stopped(func, *args, **kwargs):
    """Run a product call, translating its STOPPED-style errors to AdapterError."""
    try:
        return func(*args, **kwargs)
    except LockHeld as exc:
        raise AdapterError(f"Another writer holds this session ({exc}); retry in a moment.", 409) from exc
    except (SessionError, selection.SelectionError, guide.GuideError, ask_loop.Stopped) as exc:
        raise AdapterError(str(exc), 409) from exc


# ------------------------------------------------------------------ status (read-only)

def list_sessions(root: Path) -> list[dict]:
    out = []
    for path in workspace_sessions(root):
        try:
            summary = anastylos.summary(SessionState.load(path))
            out.append({"id": path.name, "object": summary["object"], "operator": summary["operator"],
                        "complete": summary["complete"], "incomplete": summary["incomplete"],
                        "questions": summary["questions"], "guide": summary["guide"]})
        except SessionError as exc:
            out.append({"id": path.name, "error": str(exc)})
    return out


def status(root: Path, session_id: str) -> dict:
    state = _load_state(root, session_id)
    return _stopped(anastylos.summary, state)


def question_overview(root: Path, session_id: str) -> dict:
    """Per-question id, kind, phase and latest state; read-only (no prompt is opened)."""
    state = _load_state(root, session_id)
    states = ask_loop.question_states(state)
    questions: dict[str, dict] = {}
    for phase in state.doc["phases"]:
        if phase["status"] != "done":
            continue
        path = Path(phase["run_dir"]) / "questions.json"
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for question in document.get("questions", []):
            qid = question.get("id")
            if qid:
                questions[qid] = {"id": qid, "kind": question.get("kind"),
                                  "phase": phase["tag"], "cell": question.get("cell"),
                                  "state": states.get(qid, "OPEN")}
    return {"questions": [questions[qid] for qid in sorted(questions)], "states": states}


# ------------------------------------------------------------------ question round (one step per request)

def _unclear_count(records: list[dict], op_key: str, stage: str) -> int:
    """Consecutive unusable replies since the question was last (re-)asked.

    Mirrors the ``unclear`` counters of ``ask_loop.ask_one``/``_confirm`` from the journal,
    so a page reload cannot reset or inflate them. A rejected read-back preserves the
    count, exactly as the CLI loop continues after ``asked_again``.
    """
    states = (st.ASKED,) if stage == "answer" else (st.READ_BACK,)
    unclear = _ANSWER_UNCLEAR_NOTES if stage == "answer" else _CONFIRM_UNCLEAR_NOTES
    count = 0
    for record in reversed(records):
        if record.get("kind") != "question_state" or record.get("op_key") != op_key:
            continue
        if record.get("state") not in states or "note" not in record:
            break
        note = record["note"]
        if note.startswith(unclear):
            count += 1
        elif not note.startswith(_PRESERVING_NOTES):
            break
    return count


def _prompt_payload(prompt: dict, qid: str) -> dict:
    return {"stage": "answer", "question_id": qid, "op_key": prompt["op_key"], "text": prompt["text"],
            "wording": prompt.get("wording"), "screen_options": prompt.get("screen_options", [])}


def question_next(root: Path, session_id: str) -> dict:
    """The next question in P0 order (``ask_loop.next_question``), or the finished round."""
    state = _load_state(root, session_id)

    def step() -> dict:
        report = ask_loop.LoopReport()
        coordinator = ask_loop.open_coordinator(state)
        try:
            if coordinator.stopped:
                raise ask_loop.Stopped(coordinator.stopped)
            qid = ask_loop.next_question(coordinator, report)
            if qid is None:
                return {"done": True, "committed": report.committed, "deferred": report.deferred,
                        "left_unresolved": report.unresolved,
                        "phases_not_included": [p["tag"] for p in state.doc["phases"]
                                                if p["status"] != "done"]}
            if coordinator.resume(qid) == st.READ_BACK:
                current = coordinator.state_of(qid)
                return {"done": False, "stage": "confirm", "question_id": qid,
                        "op_key": current.op_key, "read_back": current.read_back}
            return {"done": False, **_prompt_payload(coordinator.ask(qid), qid)}
        finally:
            coordinator.close()

    return _stopped(step)


def question_answer(root: Path, session_id: str, qid: str, text: str | None,
                    choice: str | None) -> dict:
    """One answer to the currently asked question; a repeated POST is a no-op, never a copy."""
    state = _load_state(root, session_id)

    def step() -> dict:
        coordinator = ask_loop.open_coordinator(state)
        try:
            if coordinator.stopped:
                raise ask_loop.Stopped(coordinator.stopped)
            if qid not in coordinator.book.questions:
                raise AdapterError(f"Unknown question {qid!r}.", 404)
            current = coordinator.state_of(qid)
            if current.state == st.READ_BACK:
                return {"status": "already_answered", "stage": "confirm", "question_id": qid,
                        "op_key": current.op_key, "read_back": current.read_back}
            if current.state != st.ASKED:
                raise AdapterError(f"Question {qid} is {current.state}; ask for the next question first.", 409)
            reply: dict = {"choice": choice.strip().upper()} if choice and choice.strip() else {"text": text or ""}
            result = coordinator.receive(ask_loop.operator_input(current.op_key, reply))
            if result["status"] == "unclear" and \
                    _unclear_count(coordinator.journal.records, current.op_key, "answer") >= ask_loop.MAX_UNCLEAR:
                coordinator.defer(qid, reason="no usable answer")
                return {"status": "deferred", "stage": "deferred", "question_id": qid,
                        "message": "I will come back to this question later."}
            if result["status"] == "read_back":
                return {"status": "read_back", "stage": "confirm", "question_id": qid,
                        "op_key": current.op_key, "read_back": result["read_back"]}
            if result["status"] == "deferred":
                return {"status": "deferred", "stage": "deferred", "question_id": qid,
                        "message": result["message"]}
            return {"status": result["status"], "stage": "answer", "question_id": qid,
                    "message": result.get("message", "")}
        finally:
            coordinator.close()

    return _stopped(step)


def question_confirm(root: Path, session_id: str, qid: str, text: str) -> dict:
    """One yes/no reply to the open read-back; a repeated POST is a no-op, never a copy."""
    state = _load_state(root, session_id)

    def step() -> dict:
        coordinator = ask_loop.open_coordinator(state)
        try:
            if coordinator.stopped:
                raise ask_loop.Stopped(coordinator.stopped)
            if qid not in coordinator.book.questions:
                raise AdapterError(f"Unknown question {qid!r}.", 404)
            current = coordinator.state_of(qid)
            if current.state == st.COMMITTED:
                return {"status": "already_committed", "stage": "done", "question_id": qid}
            if current.state != st.READ_BACK:
                raise AdapterError(f"Question {qid} is {current.state}; nothing is waiting for yes or no.", 409)
            result = coordinator.receive(ask_loop.operator_input(current.op_key, {"text": text or ""}))
            if result["status"] == "read_back" and \
                    _unclear_count(coordinator.journal.records, current.op_key, "confirm") >= ask_loop.MAX_UNCLEAR:
                coordinator.defer(qid, reason="read-back not confirmed")
                return {"status": "deferred", "stage": "deferred", "question_id": qid,
                        "message": "Nothing was recorded. I will come back to this question later."}
            if result["status"] == "committed":
                return {"status": "committed", "stage": "done", "question_id": qid,
                        "events": result.get("events", {})}
            if result["status"] == "asked_again":
                prompt = coordinator.prompts[current.op_key]
                return {"status": "asked_again", **_prompt_payload(prompt, qid)}
            return {"status": result["status"], "stage": "confirm", "question_id": qid,
                    "read_back": result.get("read_back", "")}
        finally:
            coordinator.close()

    return _stopped(step)


def question_siblings(root: Path, session_id: str, qid: str) -> dict:
    """The questions that share ``qid``'s cell and are still waiting for an answer (``qid`` first, live order)."""
    state = _load_state(root, session_id)

    def step() -> dict:
        coordinator = ask_loop.open_coordinator(state)
        try:
            if qid not in coordinator.book.questions:
                raise AdapterError(f"Unknown question {qid!r}.", 404)
            cell = coordinator.book.questions[qid].get("cell")
            waiting = (st.OPEN, st.FAILED, st.ASKED)
            order = [q for q in coordinator.book.live_order()
                     if cell is not None and coordinator.book.questions[q].get("cell") == cell
                     and coordinator.book.questions[q]["kind"] == "source_of_part"
                     and coordinator.state_of(q).state in waiting]
            return {"cell": cell, "qids": [qid] + [q for q in order if q != qid]}
        finally:
            coordinator.close()

    return _stopped(step)


def question_open(root: Path, session_id: str, qid: str) -> dict:
    """Ask a question without speaking it (a bundled answer addresses several at once); repeating is a no-op."""
    state = _load_state(root, session_id)

    def step() -> dict:
        coordinator = ask_loop.open_coordinator(state)
        try:
            if coordinator.stopped:
                raise ask_loop.Stopped(coordinator.stopped)
            if qid not in coordinator.book.questions:
                raise AdapterError(f"Unknown question {qid!r}.", 404)
            return _prompt_payload(coordinator.ask(qid), qid)
        finally:
            coordinator.close()

    return _stopped(step)


def question_defer(root: Path, session_id: str, qid: str, reason: str) -> dict:
    """Leave a question for the desktop: it is not asked again by voice and nothing is written."""
    state = _load_state(root, session_id)

    def step() -> dict:
        coordinator = ask_loop.open_coordinator(state)
        try:
            if coordinator.stopped:
                raise ask_loop.Stopped(coordinator.stopped)
            if qid not in coordinator.book.questions:
                raise AdapterError(f"Unknown question {qid!r}.", 404)
            coordinator.defer(qid, leave_unresolved=True, reason=reason)
            return {"status": "left_unresolved", "question_id": qid}
        finally:
            coordinator.close()

    return _stopped(step)


# ------------------------------------------------------------------ guide (offline: L0 / cache / refusal only)

def guide_build(root: Path, session_id: str) -> dict:
    state = _load_state(root, session_id)
    return {"guide_build": _stopped(guide.build, state)}


def _install_steps(raw: dict) -> dict[int, int]:
    return {part_id: step["step"] for step in raw["install_order"]["steps"] for part_id in step["parts"]}


def guide_view(root: Path, session_id: str) -> dict:
    """Freshness, parts and verified install steps; a stale package answers with its reason."""
    state = _load_state(root, session_id)
    try:
        package, raw = guide.load(state)
    except guide.GuideError as exc:
        builds = guide._records(guide.guide_dir(state) / "builds.jsonl")
        return {"usable": False, "reason": str(exc),
                "last_build": builds[-1] if builds else None}
    steps_of = _install_steps(raw)
    parts = [{"id": part.id, "operator_label": part.label, "basis": part.basis,
              "location": part.location, "record": part.record, "step": steps_of.get(part.id),
              "group": part.group} for part in package.parts.values()]
    steps = [{"step": entry["step"], "parts": entry["parts"], "citable": entry["citable"],
              "accepted": entry["accepted"], "text": entry["text"]}
             for entry in _stopped(assemble.steps, state)[0]]
    builds = guide._records(guide.guide_dir(state) / "builds.jsonl")
    return {"usable": True, "package_sha256": package.sha256, "parts": parts, "steps": steps,
            "last_build": builds[-1] if builds else None}


def guide_ask(root: Path, session_id: str, question: str) -> dict:
    """An offline guide answer (no proposer wired): ``source`` is L0, cache or refusal."""
    state = _load_state(root, session_id)
    if not question or not question.strip():
        raise AdapterError("The guide needs a question.", 400)
    result = _stopped(guide.answer, state, question.strip())
    return {key: result.get(key) for key in
            ("question", "text", "source", "accepted", "package_sha256", "findings")}


# ------------------------------------------------------------------ selection (explicit bindings only)

def list_selections(root: Path, session_id: str) -> dict:
    session_dir = resolve_session(root, session_id)
    folder = session_dir / SELECTIONS_DIR
    bindings = []
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            try:
                binding = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if binding.get("schema") == selection.SCHEMA:
                bindings.append({"file": path.name, "drawing_element_id": binding["drawing_element_id"],
                                 "event_id": binding["event_id"]})
    claims = []
    folder = session_dir / STORED_CLAIMS_DIR
    if folder.is_dir():
        claims = sorted(path.name for path in folder.glob("*.json"))
    return {"bindings": bindings, "stored_claims": claims}


def _selection_inputs(root: Path, session_id: str, binding_file: str):
    state = _load_state(root, session_id)
    binding_path = resolve_named(root, session_id, SELECTIONS_DIR, binding_file)
    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdapterError("Selection file cannot be read as UTF-8 JSON.", 409) from exc
    return state.memory_dir / "memory.jsonl", guide.guide_dir(state) / "session.json", binding


def select(root: Path, session_id: str, binding_file: str) -> dict:
    memory, package_path, binding = _selection_inputs(root, session_id, binding_file)
    return _stopped(selection.resolve, memory, package_path, binding)


def fresh_selection_inputs(root: Path, session_id: str, event_id: str, drawing_element_id: str):
    """``(memory, package_path, binding)`` for one declared part, bound to the session's current bytes.

    The guide must be usable first (``guide.load``: recorded build hash, intact package, memory
    unchanged since the build), so a stale or tampered package is refused instead of silently
    re-bound. Unlike ``_selection_inputs`` the binding is not an operator file: it is derived
    from the verified session bytes, and ``selection.resolve`` still checks every hash.
    """
    state = _load_state(root, session_id)
    package, _raw = _stopped(guide.load, state)
    memory = state.memory_dir / "memory.jsonl"
    package_path = guide.guide_dir(state) / "session.json"
    binding = {"schema": selection.SCHEMA, "drawing_element_id": drawing_element_id, "event_id": event_id,
               "source": {"session": _memory_session(memory), "memory_path": str(memory.resolve())},
               "memory_sha256": hashlib.sha256(memory.read_bytes()).hexdigest(), "guide_sha256": package.sha256}
    return memory, package_path, binding


def _memory_session(memory: Path) -> str:
    sessions = {json.loads(line).get("session") for line in memory.read_text(encoding="utf-8").splitlines()
                if line.strip()}
    if len(sessions) != 1 or not next(iter(sessions)):
        raise AdapterError("The session memory carries no single session value; no part can be bound.", 409)
    return next(iter(sessions))


def selected_ask(root: Path, session_id: str, binding_file: str, intent: str,
                 claims_file: str | None = None) -> dict:
    """Recorded origin or storage of the bound part.

    Default is deterministic L0. ``claims_file`` replays a stored offline claims fixture
    (``source: offline_claims``, never evidence of a model call). Muse is not wired.
    """
    if intent not in ("origin", "storage"):
        raise AdapterError("Only recorded origin and storage questions are supported for a selected part.", 400)
    memory, package_path, binding = _selection_inputs(root, session_id, binding_file)
    proposer, source = None, "Muse"
    if claims_file:
        claims_path = resolve_named(root, session_id, STORED_CLAIMS_DIR, claims_file)
        sys.path.insert(0, str(LIVE / "providers"))
        from fake import FakeProposer
        try:
            output = json.loads(claims_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AdapterError("Stored claims file cannot be read as UTF-8 JSON.", 409) from exc
        proposer = FakeProposer(script={"guide_claims": {"mode": "output", "output": output}})
        source = "offline_claims"
    session_dir = resolve_session(root, session_id)
    try:
        return selected_guide.answer(memory, package_path, binding, intent, proposer,
                                     session_dir / SELECTED_CACHE_NAME, guide.MUSE_TIMEOUT_S, source)
    except (SessionError, selection.SelectionError, guide.GuideError) as exc:
        raise AdapterError(str(exc), 409) from exc


# ------------------------------------------------------------------ assembly progress

def assembly_view(root: Path, session_id: str) -> dict:
    state = _load_state(root, session_id)
    try:
        now = assemble.status(state)
    except guide.GuideError as exc:
        return {"usable": False, "reason": str(exc)}
    latest = {str(step): {"event": record["event"], "basis": record.get("basis")} for step, record in now["latest"].items()}
    done = sum(1 for record in now["latest"].values() if record["event"] in (assemble.DONE, assemble.VERIFIED))
    return {"usable": True, "package_sha256": now["package_sha256"],
            "steps": [{"step": entry["step"], "parts": entry["parts"], "text": entry["text"]}
                      for entry in now["steps"]], "latest": latest,
            "current_step": (now["current"] or {}).get("step"), "steps_done": done,
            "complete": now["complete"], "progress_for_other_packages": now["progress_for_other_packages"]}


def assembly_done(root: Path, session_id: str, step: int) -> dict:
    """Confirm the current install step; only the current step can be confirmed, once."""
    state = _load_state(root, session_id)
    if not isinstance(step, int):
        raise AdapterError("The assembly step must be a number.", 400)
    _stopped(assemble.mark_done, state, step)
    return assembly_view(root, session_id)


# ------------------------------------------------------------------ session report

def report_export(root: Path, session_id: str) -> dict:
    """Export the standalone HTML report; freshness is checked inside ``report.export``.

    The target is fixed server-side (``report.html`` in the session folder); a stale
    or tampered guide refuses the export and writes nothing. Absolute server paths
    never reach the browser.
    """
    session_dir = resolve_session(root, session_id)
    try:
        result = report.export(session_dir, session_dir / REPORT_NAME)
    except (report.ReportError, SessionError) as exc:
        raise AdapterError(str(exc), 409) from exc
    return {"file": REPORT_NAME, "report_sha256": result["report_sha256"],
            "guide": result["guide"], "parts": result["parts"],
            "install_steps": result["install_steps"], "open_questions": result["open_questions"],
            "left_unresolved": result["left_unresolved"], "assembly": result["assembly"]}


def report_status(root: Path, session_id: str) -> dict:
    """Whether a report was exported (read-only; the file itself stays server-side)."""
    path = resolve_session(root, session_id) / REPORT_NAME
    if not path.is_file():
        return {"exists": False}
    data = path.read_bytes()
    return {"exists": True, "file": REPORT_NAME, "bytes": len(data),
            "report_sha256": hashlib.sha256(data).hexdigest()}


def report_bytes(root: Path, session_id: str) -> bytes:
    path = resolve_session(root, session_id) / REPORT_NAME
    if not path.is_file():
        raise AdapterError("No report was exported for this session yet.", 404)
    return path.read_bytes()


# ------------------------------------------------------------------ capture needs

def _read_gap(session_dir: Path, name: str) -> dict:
    if not name or name in (".", "..") or "/" in name or "\\" in name or not name.endswith(".json"):
        raise AdapterError(f"Unknown capture gap {name!r}.", 404)
    candidate = (session_dir / GAPS_DIR / GAPS_SUBDIR / name).resolve()
    if candidate.parent != (session_dir / GAPS_DIR / GAPS_SUBDIR).resolve() or not candidate.is_file():
        raise AdapterError(f"Unknown capture gap {name!r}.", 404)
    try:
        gap = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdapterError(f"Capture gap {name} cannot be read as UTF-8 JSON.", 409) from exc
    if not isinstance(gap, dict):
        raise AdapterError(f"Capture gap {name} is not a JSON object.", 409)
    return gap


def _fresh_binding(state: SessionState, target: dict) -> dict:
    """The current binding, derived from the actual session bytes (never from the browser)."""
    memory = state.memory_dir / "memory.jsonl"
    if not memory.is_file():
        raise AdapterError("This session has no memory yet; no capture need can be bound.", 409)
    try:
        memory_bytes = memory.read_bytes()
        sessions = {json.loads(line).get("session") for line in memory_bytes.decode("utf-8").splitlines()
                    if line.strip()}
    except (OSError, ValueError) as exc:
        raise AdapterError("The session memory cannot be read; no capture need can be bound.", 409) from exc
    if len(sessions) != 1 or not next(iter(sessions)):
        raise AdapterError("The session memory carries no single session value; no capture need can be bound.", 409)
    memory_sha = hashlib.sha256(memory_bytes).hexdigest()
    hashes = {"memory": memory_sha}
    package = guide.guide_dir(state) / "session.json"
    if package.is_file():
        hashes["guide"] = hashlib.sha256(package.read_bytes()).hexdigest()
    return {"session": next(iter(sessions)), "target": target, "revision": "mem:" + memory_sha[:12],
            "hashes": hashes}


def _evidence_files(session_dir: Path) -> list[dict]:
    folder = session_dir / GAPS_DIR / EVIDENCE_SUBDIR
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in EVIDENCE_SUFFIXES or not path.is_file():
            continue
        data = path.read_bytes()
        out.append({"file": path.name, "bytes": len(data),
                    "image_sha256": hashlib.sha256(data).hexdigest()})
    return out


def _evaluate_gap(state: SessionState, gap: dict, candidate_file: str | None) -> dict:
    """One planner evaluation; image hashes always come from actual evidence bytes."""
    target = gap.get("binding", {}).get("target") if isinstance(gap.get("binding"), dict) else None
    if not isinstance(target, dict):
        raise AdapterError("Capture gap has no explicit binding target.", 409)
    document: dict = {"schema": "anastylos-capture-gap-input@1", "gap": gap,
                      "current_binding": _fresh_binding(state, target)}
    if candidate_file:
        if "/" in candidate_file or "\\" in candidate_file or candidate_file in (".", ".."):
            raise AdapterError(f"Unknown evidence {candidate_file!r}.", 404)
        path = (state.root / GAPS_DIR / EVIDENCE_SUBDIR / candidate_file).resolve()
        if path.parent != (state.root / GAPS_DIR / EVIDENCE_SUBDIR).resolve() or not path.is_file() \
                or path.suffix.lower() not in EVIDENCE_SUFFIXES:
            raise AdapterError(f"Unknown evidence {candidate_file!r}.", 404)
        image_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        # Prepared evidence belongs to its prepared gap revision; staleness is detected
        # against the freshly derived current binding, never repaired.
        document["candidate"] = {"evidence_id": path.name, "image_sha256": image_sha,
                                 "binding": gap["binding"]}
    try:
        return capture_planner.evaluate(document)
    except (ValueError, KeyError, TypeError) as exc:
        raise AdapterError(f"Capture gap cannot be evaluated: {exc}", 409) from exc


def gaps_view(root: Path, session_id: str) -> dict:
    """Prepared gaps with their planner state plus prepared evidence (read-only)."""
    state = _load_state(root, session_id)
    folder = state.root / GAPS_DIR / GAPS_SUBDIR
    gaps = []
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            try:
                gap = _read_gap(state.root, path.name)
                evaluation = _evaluate_gap(state, gap, None)
            except AdapterError as exc:
                gaps.append({"file": path.name, "error": str(exc)})
                continue
            gaps.append({"file": path.name, "gap_id": evaluation["gap_id"], "kind": evaluation["kind"],
                         "detail": evaluation["detail"],
                         "missing_information": evaluation["missing_information"],
                         "instruction": evaluation["instruction"],
                         "technical_criteria": evaluation["technical_criteria"],
                         "content_criterion": evaluation["content_criterion"],
                         "status": evaluation["status"], "reason": evaluation["reason"]})
    return {"gaps": gaps, "evidence": _evidence_files(state.root),
            "closure": "locked: this build has no technical image check, so no review can be "
                      "submitted and gaps stay open or needs_review"}


def gaps_evaluate(root: Path, session_id: str, gap_file: str, candidate_file: str | None) -> dict:
    """Evaluate one prepared gap, optionally with one prepared evidence candidate.

    Pure planner evaluation: nothing is written. Closure requires explicit
    technical and operator reviews, which this build cannot produce (see
    ``gaps_view`` closure note); the planner result says what is missing.
    """
    state = _load_state(root, session_id)
    if not gap_file:
        raise AdapterError("The gap parameter is required.", 400)
    gap = _read_gap(state.root, gap_file)
    return _evaluate_gap(state, gap, candidate_file or None)


def evidence_bytes(root: Path, session_id: str, name: str) -> tuple[bytes, str]:
    """Prepared evidence bytes for display (no analysis is performed or claimed)."""
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise AdapterError(f"Unknown evidence {name!r}.", 404)
    session_dir = resolve_session(root, session_id)
    path = (session_dir / GAPS_DIR / EVIDENCE_SUBDIR / name).resolve()
    if path.parent != (session_dir / GAPS_DIR / EVIDENCE_SUBDIR).resolve() or not path.is_file():
        raise AdapterError(f"Unknown evidence {name!r}.", 404)
    content_type = EVIDENCE_SUFFIXES.get(path.suffix.lower())
    if content_type is None:
        raise AdapterError(f"Unknown evidence {name!r}.", 404)
    return path.read_bytes(), content_type
