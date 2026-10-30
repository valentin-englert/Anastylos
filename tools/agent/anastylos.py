"""ANASTYLOS agent: one entry point that takes a disassembly from phase videos to Assembly Memory.

    python tools/agent/anastylos.py new    --session C:\\ANASTYLOS\\data\\agent\\<id> --object "SNES-Controller" --operator "<name>"
    python tools/agent/anastylos.py phase  --session <dir> --tag P1 --label "beim Abschrauben der Rückseite" --video P1.mp4
    python tools/agent/anastylos.py status --session <dir>

M0 scope: session file and per-phase processing. Questions, guide and reassembly steps follow (roadmap M1–M2).
Sessions live outside the repository; the agent never touches the active FULL-01 memory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from phases import REPO, run_phase, subprocess_runner, vision_python
from state import SessionError, SessionState

ACTIVE_MEMORY_POINTER = Path(r"C:\ANASTYLOS\data\active-memory.json")


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
    return {"session": str(state.root), "object": state.doc["object"], "operator": state.doc["operator"],
            "phases": [{k: p[k] for k in ("tag", "label", "status", "counts", "video_sha256")}
                       for p in state.doc["phases"]],
            "memory_events": by_type,
            "open_questions": sum((p["counts"] or {}).get("questions", 0) for p in state.doc["phases"]
                                  if p["status"] == "done")}


def cmd_new(args) -> dict:
    return summary(SessionState.create(_session_root(args.session), args.object, args.operator))


def cmd_phase(args) -> dict:
    state = SessionState.load(_session_root(args.session))
    print(f"Verarbeite {args.tag} ({args.label}); das dauert etwa das Dreifache der Videolänge …", file=sys.stderr)
    run_phase(state, args.tag, args.label, args.video, subprocess_runner, vision_python(args.python))
    return summary(state)


def cmd_status(args) -> dict:
    return summary(SessionState.load(_session_root(args.session)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="create a session folder")
    new.add_argument("--session", required=True, type=Path)
    new.add_argument("--object", required=True, help="the operator's word for the object")
    new.add_argument("--operator", required=True, help="declared_by / confirmed_by for this session")
    phase = sub.add_parser("phase", help="process one phase video into memory, parts and questions")
    phase.add_argument("--session", required=True, type=Path)
    phase.add_argument("--tag", required=True, help="P1, P2, … in order")
    phase.add_argument("--label", required=True, help="the operator's words for the phase")
    phase.add_argument("--video", required=True, type=Path)
    phase.add_argument("--python", type=Path, help="OpenCV venv interpreter (default: <repo>/.venv)")
    status = sub.add_parser("status", help="phases, memory events and open questions")
    status.add_argument("--session", required=True, type=Path)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    handlers = {"new": cmd_new, "phase": cmd_phase, "status": cmd_status}
    try:
        result = handlers[args.command](args)
    except SessionError as exc:
        print(f"ABGEBROCHEN: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
