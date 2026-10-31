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

No function here reimplements guide, selection, budget, verifier or progress logic.
No HTTP call is possible: no provider module is imported besides the offline fake.
"""

from __future__ import annotations

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
import guide  # noqa: E402
import selected_guide  # noqa: E402
import selection  # noqa: E402
import states as st  # noqa: E402  (tools/live)
from lock import LockHeld  # noqa: E402
from state import SessionError, SessionState  # noqa: E402

SELECTIONS_DIR = "selections"
STORED_CLAIMS_DIR = "selected_claims"
SELECTED_CACHE_NAME = "selected_answers.jsonl"


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
