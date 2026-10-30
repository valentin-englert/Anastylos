"""ANASTYLOS agent: one entry point that takes a disassembly from phase videos to a confirmed Assembly Memory.

    python tools/agent/anastylos.py new      --session C:\\ANASTYLOS\\data\\agent\\<id> --object "SNES controller" --operator "<name>"
    python tools/agent/anastylos.py phase    --session <dir> --tag P1 --label "while unscrewing the back cover" --video P1.mp4
    python tools/agent/anastylos.py prepare  --session <dir> [--muse --max-calls 12]
    python tools/agent/anastylos.py ask      --session <dir> [--script answers.json]
    python tools/agent/anastylos.py status   --session <dir>

M1 scope: session file, per-phase processing, and the question loop (P0 order, English L0 or prefetched L1
wording, read-back, explicit yes before any memory write). Guide and reassembly steps follow (roadmap M2).
Sessions live outside the repository; the agent never touches the active FULL-01 memory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ask_loop
from phases import REPO, run_phase, subprocess_runner, vision_python
from state import SessionError, SessionState

ACTIVE_MEMORY_POINTER = Path(r"C:\ANASTYLOS\data\active-memory.json")
DEFAULT_MUSE_CALLS = 12  # per `prepare`; the week's budget is 150 calls in total (owner decision E2)


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
    if not state.doc["phases"]:
        incomplete.append("no phase processed yet")
    return {"session": str(state.root), "object": state.doc["object"], "operator": state.doc["operator"],
            "phases": [{k: p[k] for k in ("tag", "label", "status", "counts", "video_sha256")}
                       for p in state.doc["phases"]],
            "memory_events": by_type, "questions": by_state,
            "complete": not incomplete, "incomplete": incomplete}


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
    live = Path(__file__).resolve().parents[1] / "live"
    sys.path.insert(0, str(live / "providers"))
    from meta import MetaProposer  # wiring only here: the core and the loop never import a provider
    proposer = MetaProposer(state.root / "provider", max_calls=args.max_calls)
    verdicts = ask_loop.prefetch(state, proposer, args.max_calls)
    return {**summary(state), "prepared": verdicts, "muse_calls_used_in_this_session": proposer.calls_used}


def cmd_ask(args) -> dict:
    state = SessionState.load(_session_root(args.session))
    operator = ask_loop.ScriptedOperator(json.loads(args.script.read_text(encoding="utf-8"))) if args.script \
        else ask_loop.ConsoleOperator()
    report = ask_loop.run(state, operator)
    return {**summary(state), "this_run": {"committed": report.committed, "deferred": report.deferred,
                                           "left_unresolved": report.unresolved,
                                           "phases_not_included": report.excluded_phases}}


def cmd_status(args) -> dict:
    return summary(SessionState.load(_session_root(args.session)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
    prepare.add_argument("--max-calls", type=int, default=DEFAULT_MUSE_CALLS)
    ask = sub.add_parser("ask", help="ask the open questions; nothing is recorded without your yes")
    ask.add_argument("--session", required=True, type=Path)
    ask.add_argument("--script", type=Path, help="replies from a JSON file instead of the keyboard (tests, demos)")
    status = sub.add_parser("status", help="phases, questions, memory events and whether the session is complete")
    status.add_argument("--session", required=True, type=Path)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # operator labels may carry any characters
        sys.stdout.reconfigure(encoding="utf-8")
    handlers = {"new": cmd_new, "phase": cmd_phase, "prepare": cmd_prepare, "ask": cmd_ask, "status": cmd_status}
    try:
        result = handlers[args.command](args)
    except (SessionError, ask_loop.Stopped) as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
