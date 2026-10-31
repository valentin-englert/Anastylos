"""Build the offline demo workspace: two fresh FULL-01 copies, no original touched.

- ``demo-open``: phases recorded, twelve questions open, no guide yet.
- ``demo-complete``: twelve answers committed (scripted, offline), guide built,
  two explicit selection bindings plus stored-claims fixtures for origin/storage,
  and three synthetic capture-gap fixtures (fresh, stale, duplicate) with one
  synthetic PNG evidence candidate.

The session recipe (fixtures, memory baseline, scripted English replies) is the
M1 recipe from ``tools/agent/tests/test_questions.py`` (``make_session``,
``full01_script``, ``english_reply``), duplicated here because test helpers are
test-only. The fixtures are lane A's verbatim FULL-01 v21 copies.

Selection bindings are explicit demo bindings drawn once at setup time from the
fresh workspace bytes and verified with ``selection.resolve``. The server never
recomputes them: if the workspace memory or guide changes afterwards, selection
refuses with a hash mismatch (no automatic rebinding).

    python tools/session-ui/demo/prepare_demo.py --root C:\\ANASTYLOS\\data\\session-ui-demo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
REPO = UI_ROOT.parents[1]
AGENT = REPO / "tools" / "agent"
LIVE = REPO / "tools" / "live"
FIXTURES = LIVE / "tests" / "fixtures"
for _path in (AGENT, LIVE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ask_loop  # noqa: E402
import capture_gaps.planner as capture_planner  # noqa: E402  (verify demo gaps, no I/O)
import guide  # noqa: E402
import selected_guide  # noqa: E402
import selection  # noqa: E402
from anastylos import ACTIVE_MEMORY_POINTER  # noqa: E402
from state import SessionState  # noqa: E402

sys.path.insert(0, str(LIVE / "providers"))
from fake import FakeProposer  # noqa: E402  (offline stored-claims replay, like CLI --claims)

OPERATOR = "operator (agent test)"
PHASES = {"P1": "while unscrewing the back cover", "P2": "while opening the housing",
          "P3": "while removing parts from the front shell"}
OPEN_ID = "demo-open"
COMPLETE_ID = "demo-complete"


def check_root(root: Path) -> Path:
    resolved = root.resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise SystemExit(f"Refusing {resolved}: demo sessions live outside the repository.")
    if ACTIVE_MEMORY_POINTER.is_file():
        pointer = json.loads(ACTIVE_MEMORY_POINTER.read_text(encoding="utf-8"))
        for entry in pointer.values():
            if not isinstance(entry, dict) or "active" not in entry:
                continue
            protected = (ACTIVE_MEMORY_POINTER.parent / entry["active"]).resolve()
            if resolved == protected or protected in resolved.parents:
                raise SystemExit(f"Refusing {resolved}: inside the active memory {protected}.")
    return resolved


def make_fresh_session(root: Path, phases: tuple[str, ...] = ("P1", "P2", "P3")) -> SessionState:
    """A session as after `phase` for each tag (M1 recipe: fixtures + memory baseline)."""
    state = SessionState.create(root, "SNES controller (FULL-01 copy)", OPERATOR)
    shutil.copyfile(FIXTURES / "audit" / "memory.before-answers.jsonl", state.memory_dir / "memory.jsonl")
    for tag in phases:
        label = PHASES[tag]
        state.begin_phase(tag, label, FIXTURES / tag / "parts.json", "0" * 64)
        state.run_dir(tag).mkdir(parents=True)
        for name in ("questions.json", "parts.json"):
            shutil.copyfile(FIXTURES / tag / name, state.run_dir(tag) / name)
        questions = json.loads((FIXTURES / tag / "questions.json").read_text(encoding="utf-8"))["questions"]
        state.finish_phase(tag, {"questions": len(questions)})
    return state


def _candidates() -> dict[str, dict[str, str]]:
    """Question id -> screen token -> removal event (M1 recipe: tokens in candidate order)."""
    tokens: dict[str, dict[str, str]] = {}
    for tag in PHASES:
        document = json.loads((FIXTURES / tag / "questions.json").read_text(encoding="utf-8"))
        for question in document["questions"]:
            tokens[question["id"]] = {chr(ord("A") + i): c["removal_event"]
                                      for i, c in enumerate(question.get("candidates", []))}
    return tokens


def english_reply(answer: dict, tokens: dict[str, str]) -> str:
    """How an English-speaking operator types an answers.py answer (M1 recipe)."""
    if answer.get("not_a_part"):
        return "not a part"
    if answer.get("choice"):
        token = next(t for t, event in tokens.items() if event == answer["choice"])
        return f"{answer['label']} from {token}" if answer.get("label") else token
    if answer.get("source") is not None:
        return f"{answer['label']} from {answer['source']}"
    return f"{answer['label']} to {answer['destination']}"


def full01_script() -> dict[str, dict[str, list[str]]]:
    """The twelve regular FULL-01 answers as an ask_loop script (M1 recipe)."""
    rows = [json.loads(line) for line in
            (FIXTURES / "audit" / "apply_input.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    tokens = _candidates()
    script = {}
    for row in rows:
        if row["question"] == "EXTRA" or row["question"] not in tokens:
            continue
        script[row["question"]] = {"answer": [english_reply(row, tokens[row["question"]])], "confirm": ["yes"]}
    return script


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_bindings(state: SessionState) -> list[Path]:
    """Two explicit demo bindings for declared parts, verified immediately."""
    memory = state.memory_dir / "memory.jsonl"
    package_path = guide.guide_dir(state) / "session.json"
    events = [json.loads(line) for line in memory.read_text(encoding="utf-8").splitlines() if line.strip()]
    sessions = {event.get("session") for event in events}
    if len(sessions) != 1:
        raise SystemExit(f"Demo memory carries {len(sessions)} session values; selection needs exactly one.")
    declared = sorted((event for event in events if event.get("type") == "declared_removal"),
                      key=lambda event: event["id"])
    if len(declared) < 2:
        raise SystemExit("Demo memory holds fewer than two declared parts; cannot bind selections.")
    folder = state.root / "selections"
    folder.mkdir(exist_ok=True)
    written = []
    for event in declared[:2]:
        binding = {"schema": selection.SCHEMA, "drawing_element_id": f"demo-binding-{event['id']}",
                   "source": {"session": next(iter(sessions)), "memory_path": str(memory.resolve())},
                   "memory_sha256": _sha(memory), "guide_sha256": _sha(package_path), "event_id": event["id"]}
        path = folder / f"{event['id']}.json"
        path.write_text(json.dumps(binding, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        resolved = selection.resolve(memory, package_path, binding)  # fail fast if unbindable
        print(f"bound {path.name}: {resolved['operator_label']!r} "
              f"(origin={'missing' if resolved['original_location']['value'] is None else 'recorded'}, "
              f"storage={'missing' if resolved['stored_location']['value'] is None else 'recorded'})")
        written.append(path)
    return written


def write_stored_claims(state: SessionState, binding_paths: list[Path]) -> list[Path]:
    """Stored offline claims fixtures, each verified before it is kept.

    Positive origin/storage replay on the first binding; the abstention replays on the
    second binding, because the selected-answer cache is keyed by (binding, intent) and
    two fixtures for one key would collapse into whichever was asked first.
    """
    memory = state.memory_dir / "memory.jsonl"
    package_path = guide.guide_dir(state) / "session.json"
    first = json.loads(binding_paths[0].read_text(encoding="utf-8"))
    stored_value = selection.resolve(memory, package_path, first)["stored_location"]["value"]
    event_id = first["event_id"]
    second_id = json.loads(binding_paths[1].read_text(encoding="utf-8"))["event_id"]
    fixtures = {
        f"{event_id}-origin-positive.json":
            (first, "origin", {"schema": guide.claims.CLAIMS_SCHEMA,
                               "claims": [{"relation": "removed_from", "subject": event_id, "object": None,
                                           "basis_word": guide.claims.BY_OPERATOR}],
                               "insufficient_evidence": False}),
        f"{event_id}-storage-positive.json":
            (first, "storage", {"schema": guide.claims.CLAIMS_SCHEMA,
                                "claims": [{"relation": "stored_in", "subject": event_id,
                                            "object": stored_value, "basis_word": guide.claims.BY_OPERATOR}],
                                "insufficient_evidence": False}),
        f"{second_id}-origin-abstention.json":
            (json.loads(binding_paths[1].read_text(encoding="utf-8")), "origin",
             {"schema": guide.claims.CLAIMS_SCHEMA,
              "claims": [{"relation": "unknown", "subject": second_id, "object": None,
                          "basis_word": None}],
              "insufficient_evidence": True}),
    }
    folder = state.root / "selected_claims"
    folder.mkdir(exist_ok=True)
    written = []
    for name, (binding, intent, output) in fixtures.items():
        proposer = FakeProposer(script={"guide_claims": {"mode": "output", "output": output}})
        result = selected_guide.answer(memory, package_path, binding, intent, proposer, None,
                                       guide.MUSE_TIMEOUT_S, "offline_claims")
        if not result["accepted"] or result["source"] != "offline_claims":
            raise SystemExit(f"Fixture {name} rejected ({result['outcome']}); not written.")
        path = folder / name
        path.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"stored claims {name}: outcome {result['outcome']}")
        written.append(path)
    return written


def build_demo(root: Path) -> dict[str, Path]:
    demo_open, demo_complete = root / OPEN_ID, root / COMPLETE_ID
    for path in (demo_open, demo_complete):
        if path.exists():
            raise SystemExit(f"{path} exists; remove it for a fresh demo copy (or pass --force).")
    print(f"building {demo_open} (open questions) …")
    make_fresh_session(demo_open)
    print(f"building {demo_complete} (answered, guided, bound) …")
    state = make_fresh_session(demo_complete)
    report = ask_loop.run(state, ask_loop.ScriptedOperator(full01_script()))
    print(f"committed {len(report.committed)}, deferred {len(report.deferred)}, "
          f"unresolved {len(report.unresolved)}")
    if report.deferred or report.unresolved:
        raise SystemExit("Demo script left questions open; the M1 recipe no longer replays cleanly.")
    build = guide.build(state)
    print(f"guide package {build['package_sha256'][:12]} from memory {build['memory_sha256'][:12]}")
    bindings = write_bindings(state)
    write_stored_claims(state, bindings)
    write_capture_gaps(state)
    return {"demo-open": demo_open, "demo-complete": demo_complete}


def _demo_png() -> bytes:
    """Deterministic synthetic 1x1 PNG (demo evidence only, not a real capture)."""
    import struct
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(
            ">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x80\x80\x80")) + chunk(b"IEND", b""))


def write_capture_gaps(state: SessionState) -> list[Path]:
    """Synthetic capture-gap fixtures for the demo: fresh, stale and duplicate.

    All bindings are drawn once from the fresh workspace bytes (same revision
    scheme the UI server derives: ``mem:<12hex>`` over memory, plus memory and
    guide hashes). The stale gap pins an outdated revision on purpose; the
    duplicate gap already cites the candidate bytes as a previous image. Each
    file is evaluated with the planner before it is kept.
    """
    memory = state.memory_dir / "memory.jsonl"
    package_path = guide.guide_dir(state) / "session.json"
    events = [json.loads(line) for line in memory.read_text(encoding="utf-8").splitlines() if line.strip()]
    sessions = {event.get("session") for event in events}
    if len(sessions) != 1:
        raise SystemExit("Demo memory carries no single session value; cannot bind gaps.")
    memory_sha, guide_sha = _sha(memory), _sha(package_path)
    fresh = {"session": next(iter(sessions)), "target": {"kind": "event", "id": "dec-0001"},
             "revision": "mem:" + memory_sha[:12], "hashes": {"memory": memory_sha, "guide": guide_sha}}
    evidence = _demo_png()
    evidence_sha = hashlib.sha256(evidence).hexdigest()
    gaps = {
        "gap-rim-outer.json":
            {"id": "gap-demo-rim-outer", "kind": "outer_boundary", "binding": dict(fresh),
             "source_image_sha256s": [],
             "detail": "Demo fixture: the full outer rim of dec-0001 is not confirmed visible."},
        "gap-rim-stale.json":
            {"id": "gap-demo-rim-stale", "kind": "outer_boundary",
             "binding": {**fresh, "revision": "mem:000000000000",
                         "hashes": {"memory": "0" * 64, "guide": "0" * 64}},
             "source_image_sha256s": [],
             "detail": "Demo fixture with an outdated binding: must stay needs_review."},
        "gap-rim-duplicate.json":
            {"id": "gap-demo-rim-duplicate", "kind": "missing_side", "binding": dict(fresh),
             "source_image_sha256s": [evidence_sha],
             "detail": "Demo fixture: the candidate bytes are already a previous image."},
    }
    gaps_dir = state.root / "capture_gaps" / "gaps"
    gaps_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = state.root / "capture_gaps" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "candidate-rim-1.png").write_bytes(evidence)
    written = []
    for name, gap in gaps.items():
        result = capture_planner.evaluate({"schema": "anastylos-capture-gap-input@1", "gap": gap,
                                           "current_binding": dict(fresh)})
        expected = "needs_review" if name == "gap-rim-stale.json" else "open"
        if result["status"] != expected:
            raise SystemExit(f"Demo gap {name} evaluates to {result['status']}, expected {expected}.")
        path = gaps_dir / name
        path.write_text(json.dumps(gap, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"capture gap {name}: {result['status']} ({result['reason']})")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the session-UI offline demo workspace.")
    parser.add_argument("--root", type=Path, default=Path(r"C:\ANASTYLOS\data\session-ui-demo"))
    parser.add_argument("--force", action="store_true", help="remove the demo root first for fresh copies")
    args = parser.parse_args(argv)
    root = check_root(args.root)
    if args.force and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    built = build_demo(root)
    for name, path in built.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
