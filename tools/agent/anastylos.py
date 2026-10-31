"""ANASTYLOS agent: one entry point that takes a disassembly from phase videos to a confirmed Assembly Memory.

    python tools/agent/anastylos.py new      --session C:\\ANASTYLOS\\data\\agent\\<id> --object "SNES controller" --operator "<name>"
    python tools/agent/anastylos.py phase    --session <dir> --tag P1 --label "while unscrewing the back cover" --video P1.mp4
    python tools/agent/anastylos.py prepare  --session <dir> [--muse --max-calls 12]
    python tools/agent/anastylos.py ask      --session <dir> [--script answers.json]
    python tools/agent/anastylos.py declare  --session <dir> --phase P2 --label "<your name>" --to "<place or cell N>" [--from ...] [--at 68]
    python tools/agent/anastylos.py declare  --session <dir> --group dec-0001,dec-0002 --label "<your name>"
    python tools/agent/anastylos.py guide    --session <dir> build | ask --question "where is cell 8" [--muse]
    python tools/agent/anastylos.py assemble --session <dir> [--returns returns.json] [--script replies.json]
    python tools/agent/anastylos.py status   --session <dir>

Scope (M2): session file, per-phase processing, the question loop (P0 order, English L0 or prefetched L1
wording, read-back, explicit yes before any memory write), operator declarations (extra parts, groups), the
guide package bound to its SHA-256 with English answers from verified claims, and step-by-step reassembly.
Sessions live outside the repository; the agent never touches the active FULL-01 memory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ask_loop
import assemble
import declare
import guide
from phases import REPO, run_phase, subprocess_runner, vision_python
from state import SessionError, SessionState

ACTIVE_MEMORY_POINTER = Path(r"C:\ANASTYLOS\data\active-memory.json")
DEFAULT_MUSE_CALLS = 12  # absolute session HTTP-attempt cap, shared by preparation and guide


def _protected_dirs() -> list[Path]:
    """Folders the agent must never write into: the active memories named by the pointer file."""
    if not ACTIVE_MEMORY_POINTER.is_file():
        return []
    pointer = json.loads(ACTIVE_MEMORY_POINTER.read_text(encoding="utf-8"))
    return [(ACTIVE_MEMORY_POINTER.parent / entry["active"]).resolve()
            for entry in pointer.values() if isinstance(entry, dict) and "active" in entry]


def _session_root(path: Path) -> Path:
    root = path.resolve()
    if root == REPO or REPO in root.parents:
        raise SessionError(f"sessions live outside the repository (CONTRACT §2): {root}")
    for protected in _protected_dirs():
        if root == protected or protected in root.parents:
            raise SessionError(f"{root} is inside the active memory {protected}; use a copy elsewhere")
    return root


def summary(state: SessionState) -> dict:
    memory = state.memory_dir / "memory.jsonl"
    events = [json.loads(line) for line in memory.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if memory.is_file() else []
    by_type: dict[str, int] = {}
    for event in events:
        by_type[event["type"]] = by_type.get(event["type"], 0) + 1
    questions = ask_loop.question_states(state)
    asked_ids = set(questions)
    pending = sum((p["counts"] or {}).get("questions", 0) for p in state.doc["phases"] if p["status"] == "done")
    by_state: dict[str, int] = {}
    for value in questions.values():
        by_state[value] = by_state.get(value, 0) + 1
    incomplete = [f"phase {p['tag']} is {p['status']}" + (f": {p['error'][-200:]}" if p.get("error") else "")
                  for p in state.doc["phases"] if p["status"] != "done"]
    open_states = {"OPEN", "ASKED", "ANSWERED", "READ_BACK", "CONFIRMED", "DEFERRED", "FAILED"}
    still_open = sum(n for s, n in by_state.items() if s in open_states) + max(0, pending - len(asked_ids))
    if still_open:
        incomplete.append(f"{still_open} question(s) still open")
    unresolved = by_state.get("LEFT_UNRESOLVED", 0)
    if unresolved:
        incomplete.append(f"{unresolved} question(s) left unresolved; the question round ended without confirmed answers")
    if not state.doc["phases"]:
        incomplete.append("no phase processed yet")
    declarations = declare_states(state)
    if "READ_BACK" in declarations.values():
        incomplete.append("a declaration is waiting for yes or no (declare --resume)")
    return {"session": str(state.root), "object": state.doc["object"], "operator": state.doc["operator"],
            "phases": [{k: p[k] for k in ("tag", "label", "status", "counts", "video_sha256")}
                       for p in state.doc["phases"]],
            "memory_events": by_type, "questions": by_state, "declarations": declarations,
            "complete": not incomplete, "incomplete": incomplete, "guide": guide_summary(state)}


def declare_states(state: SessionState) -> dict[str, str]:
    """Latest state per declaration, read without the session lock."""
    path = state.root / "declare" / "journal.jsonl"
    latest: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line) if line.strip() else {}
            if record.get("kind") == "question_state":
                latest[record["question_id"]] = record["state"]
            elif record.get("kind") == "done":
                latest[record["question_id"]] = "COMMITTED"
    return latest


def guide_summary(state: SessionState) -> dict | None:
    if guide.recorded_sha256(state) is None:
        return None
    try:
        now = assemble.status(state)
    except guide.GuideError as error:
        return {"package_sha256": guide.recorded_sha256(state), "usable": False, "reason": str(error)}
    done = sum(1 for r in now["latest"].values() if r["event"] in (assemble.DONE, assemble.VERIFIED))
    return {"package_sha256": now["package_sha256"], "usable": True, "steps": len(now["steps"]), "steps_done": done,
            "current_step": (now["current"] or {}).get("step"), "assembly_complete": now["complete"]}


def cmd_new(args) -> dict:
    return summary(SessionState.create(_session_root(args.session), args.object, args.operator))


def cmd_phase(args) -> dict:
    state = SessionState.load(_session_root(args.session))
    print(f"Processing {args.tag} ({args.label}); this takes about three times the video length ...", file=sys.stderr)
    run_phase(state, args.tag, args.label, args.video, subprocess_runner, vision_python(args.python))
    return summary(state)


def cmd_prepare(args) -> dict:
    state = SessionState.load(_session_root(args.session))
    if not args.muse:
        return {**summary(state), "prepared": "nothing: questions use the built-in English wording (pass --muse for L1)"}
    proposer, limit = session_proposer(state, args.max_calls)
    verdicts = ask_loop.prefetch(state, proposer, limit)
    return {**summary(state), "prepared": verdicts, "muse_calls_used_in_this_session": proposer.calls_used,
            "muse_budget": muse_budget(proposer, limit)}


def session_proposer(state: SessionState, requested_limit: int | None):
    """An explicit limit replaces the session total; omission keeps it, never adds an allowance."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "live" / "providers"))
    from meta import MetaProposer  # wiring only here
    limit = requested_limit if requested_limit is not None else state.doc.get("muse_max_calls", DEFAULT_MUSE_CALLS)
    if limit < 0:
        raise SessionError("--max-calls must be >= 0 (the total session HTTP-attempt limit).")
    if state.doc.get("muse_max_calls") != limit:
        state.doc["muse_max_calls"] = limit
        state.save()
    return MetaProposer(state.root / "provider", max_calls=limit), limit


def muse_budget(proposer, limit: int) -> dict:
    return {"limit": limit, "used": proposer.calls_used, "remaining": max(0, limit - proposer.calls_used)}


def cmd_ask(args) -> dict:
    state = SessionState.load(_session_root(args.session))
    operator = ask_loop.ScriptedOperator(json.loads(args.script.read_text(encoding="utf-8"))) if args.script \
        else ask_loop.ConsoleOperator()
    report = ask_loop.run(state, operator)
    return {**summary(state), "this_run": {"committed": report.committed, "deferred": report.deferred,
                                           "left_unresolved": report.unresolved,
                                           "phases_not_included": report.excluded_phases}}


def _yes_or_no(read_back: str, given: str | None) -> dict:
    print(read_back, file=sys.stderr, flush=True)
    text = given if given is not None else input("Yes or no> ")
    return {"source": "typed", "text": text, "choice": None, "received_at": ask_loop.utc_now(), "transcript": None}


def cmd_declare(args) -> dict:
    state = SessionState.load(_session_root(args.session))
    book = declare.Declarations(state).open()
    try:
        if args.resume:
            pending = book.pending()
            if pending is None:
                return {"declaration": "nothing is waiting for a reply"}
            proposal = {"op_key": pending["op_key"], "read_back": book.details(pending["op_key"])["read_back"]}
        elif args.group:
            proposal = book.propose_group(args.group.split(","), args.label)
        else:
            if not (args.phase and args.label and args.to):
                raise SessionError("A declaration needs --phase, --label and --to (or --group).")
            proposal = book.propose_extra(args.phase, args.label, args.to, args.source, args.at)
        result = book.confirm(proposal["op_key"], _yes_or_no(proposal["read_back"], args.confirm))
        return {"declaration": proposal["op_key"], **result}
    finally:
        book.close()


def cmd_guide(args) -> dict:
    state = SessionState.load(_session_root(args.session))
    if args.action == "build":
        return {"guide_build": guide.build(state)}
    if not args.question:
        raise SessionError("guide ask needs --question.")
    proposer = None
    if args.muse:
        proposer, limit = session_proposer(state, args.max_calls)
    result = guide.answer(state, args.question, proposer=proposer, timeout_s=args.timeout)
    if proposer is not None:
        if result.get("source") == "refusal":
            print(result["text"], file=sys.stderr, flush=True)
        guide.wait_for_provider_work()
    output = {k: result.get(k) for k in ("question", "text", "source", "accepted", "package_sha256", "findings")}
    if proposer is not None:
        output["muse_budget"] = muse_budget(proposer, limit)
    return output


def cmd_assemble(args) -> dict:
    state = SessionState.load(_session_root(args.session))
    if args.returns:
        return {"return_check": assemble.apply_returns(state, args.returns)}
    operator = ask_loop.ScriptedOperator(json.loads(args.script.read_text(encoding="utf-8"))) if args.script \
        else ask_loop.ConsoleOperator()
    now = assemble.run(state, operator)
    return {"assembly_complete": now["complete"], "current_step": (now["current"] or {}).get("step"),
            "steps": len(now["steps"])}


def cmd_status(args) -> dict:
    return summary(SessionState.load(_session_root(args.session)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    budget_help = ("total session HTTP-attempt limit shared by prepare and guide, including retries; "
                   "saved across restarts (default: saved limit, initially 12); an explicit value replaces the total")
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="create a session folder")
    new.add_argument("--session", required=True, type=Path)
    new.add_argument("--object", required=True, help="your name for the object")
    new.add_argument("--operator", required=True, help="who declares and confirms in this session")
    phase = sub.add_parser("phase", help="process one phase video into memory, parts and questions")
    phase.add_argument("--session", required=True, type=Path)
    phase.add_argument("--tag", required=True, help="P1, P2, ... in order")
    phase.add_argument("--label", required=True, help="your words for the phase, e.g. 'while opening the housing'")
    phase.add_argument("--video", required=True, type=Path)
    phase.add_argument("--python", type=Path, help="OpenCV venv interpreter (default: <repo>/.venv)")
    prepare = sub.add_parser("prepare", help="optional: prefetch model wording for the open questions")
    prepare.add_argument("--session", required=True, type=Path)
    prepare.add_argument("--muse", action="store_true", help="ask Muse for wording (paid calls, text-only inputs)")
    prepare.add_argument("--max-calls", type=int, help=budget_help)
    ask = sub.add_parser("ask", help="ask the open questions; nothing is recorded without your yes")
    ask.add_argument("--session", required=True, type=Path)
    ask.add_argument("--script", type=Path, help="replies from a JSON file instead of the keyboard (tests, demos)")
    dec = sub.add_parser("declare", help="tell the agent about a part it did not ask about, or interchangeable parts")
    dec.add_argument("--session", required=True, type=Path)
    dec.add_argument("--phase", help="phase tag the part was removed in, e.g. P2")
    dec.add_argument("--label", help="your name for the part or group")
    dec.add_argument("--to", help="where the part is now: 'cell 4' or your words for the place")
    dec.add_argument("--from", dest="source", help="where it came from, in your words (optional)")
    dec.add_argument("--at", type=float, help="video time of the removal in seconds (optional)")
    dec.add_argument("--group", help="comma-separated part ids that are interchangeable")
    dec.add_argument("--resume", action="store_true", help="answer the declaration that is waiting for yes or no")
    dec.add_argument("--confirm", help="the reply to the read-back (default: ask at the keyboard)")
    gd = sub.add_parser("guide", help="build the guide package, or ask it a question")
    gd.add_argument("action", choices=("build", "ask"))
    gd.add_argument("--session", required=True, type=Path)
    gd.add_argument("--question")
    gd.add_argument("--muse", action="store_true", help="let Muse propose claims for other questions (paid call)")
    gd.add_argument("--timeout", type=float, default=guide.MUSE_TIMEOUT_S)
    gd.add_argument("--max-calls", type=int, help=budget_help)
    asm = sub.add_parser("assemble", help="guided reassembly, one step at a time")
    asm.add_argument("--session", required=True, type=Path)
    asm.add_argument("--returns", type=Path, help="apply a return check (anastylos-reassembly-returns@1)")
    asm.add_argument("--script", type=Path, help="replies from a JSON file instead of the keyboard")
    status = sub.add_parser("status", help="phases, questions, memory events and whether the session is complete")
    status.add_argument("--session", required=True, type=Path)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # operator labels may carry any characters
        sys.stdout.reconfigure(encoding="utf-8")
    handlers = {"new": cmd_new, "phase": cmd_phase, "prepare": cmd_prepare, "ask": cmd_ask, "declare": cmd_declare,
                "guide": cmd_guide, "assemble": cmd_assemble, "status": cmd_status}
    command_lock = None
    if getattr(args, "muse", False):
        from lock import SessionLock, LockHeld
        try:
            state = SessionState.load(_session_root(args.session))
        except SessionError as exc:
            print(f"STOPPED: {exc}", file=sys.stderr)
            return 1
        command_lock = SessionLock(state.root)
        try:
            command_lock.acquire({"operation": "model command"})
        except LockHeld:
            print("STOPPED: Another model command is still running for this session.", file=sys.stderr)
            return 1
    try:
        result = handlers[args.command](args)
    except (SessionError, ask_loop.Stopped, guide.GuideError) as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            guide.wait_for_provider_work()
        finally:
            if command_lock is not None:
                command_lock.release()
    print(json.dumps(result, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
