"""Controlled memory writes for one confirmed answer: write-ahead intent, step results, recovery.

The memory events are exactly those ``answers.apply`` writes for one answer (``declared_removal``,
then ``storage_link`` to it), issued one ``memory.py`` subprocess call at a time so each step is
journaled. Memory has no op_key field (frozen), so the op_key travels in the existing free-text
note: ``answers.plan`` puts the answer's ``provenance`` (``live op_key=<op_key>; ...``) into the
declared removal's ``evidence_note`` and the storage link's ``basis.note``. Recovery finds an
intent's events by that marker and completes only the missing steps; it never re-declares.
"""

from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
from pathlib import Path

from contracts import MEMORY_CLI, answers, load_events

MARKER = re.compile(r"live op_key=([^;\s]+)")
STEPS = ("declared_removal", "storage_link")


class MemoryCallFailed(RuntimeError):
    """memory.py refused a write (non-zero exit); its message is kept."""


class RecoveryConflict(RuntimeError):
    """Memory and journal disagree in a way recovery must not resolve by itself."""


def memory_call(session_dir: Path, argv: list) -> dict:
    """One memory.py call, run exactly as ``answers._memory`` runs it, but failing with an exception."""
    done = subprocess.run([sys.executable, str(MEMORY_CLI), "--session-dir", str(session_dir), *map(str, argv)],
                          capture_output=True, text=True, encoding="utf-8", env=answers.CHILD_ENV)
    if done.returncode != 0:
        raise MemoryCallFailed((done.stderr or done.stdout).strip()[-500:])
    return json.loads(done.stdout)


def step_argv(step: str, action: dict, declared_by: str, declared_removal: str | None = None) -> list:
    """argv of one step, identical to the calls in ``answers.apply``."""
    if step == "declared_removal":
        return ["record", "declared-removal", "--label", action["label"],
                *([] if action["source"] is None else ["--source", action["source"]]), "--phase", action["phase"],
                "--declared-by", declared_by, "--note", action["note"],
                *([] if action.get("t_s") is None else ["--t-s", action["t_s"]])]
    return ["record", "storage-link", "--target", declared_removal, "--location", action["location"],
            "--confirmed-by", declared_by, "--note", action["note"]]


def marker_of(event: dict) -> str | None:
    if event["type"] == "declared_removal":
        note = event.get("evidence_note")
    elif event["type"] == "storage_link":
        note = (event.get("basis") or {}).get("note")
    else:
        return None
    match = MARKER.search(note or "")
    return match.group(1) if match else None


def find_steps(events: list[dict], op_key: str) -> dict[str, list[dict]]:
    found = {step: [e for e in events if e["type"] == step and marker_of(e) == op_key] for step in STEPS}
    return found


def census(events: list[dict]) -> dict:
    """What a clean memory looks like after live writes (cf. ``seed/probe.py`` census)."""
    decl = [e for e in events if e["type"] == "declared_removal"]
    stored = {e["target_id"] for e in events if e["type"] == "storage_link"}
    per_key = collections.Counter(marker_of(e) for e in decl if marker_of(e))
    superseded = {e["supersedes"] for e in events if e.get("supersedes")}
    linked = {e["observation_id"] for e in events if e["type"] == "tray_link"}
    return {"events": len(events), "declared_removal": len(decl), "storage_link": len(stored),
            "orphan_declared_without_storage": sorted(e["id"] for e in decl if e["id"] not in stored),
            "duplicate_op_keys": sorted(k for k, n in per_key.items() if n > 1),
            # X5: an imported observation whose tray link never got written (session.py retry loses it)
            "observations_without_tray_link": sorted(e["id"] for e in events if e["type"] == "observation"
                                                     and e["id"] not in linked and e["id"] not in superseded)}


class MemoryWriter:
    def __init__(self, session_dir: Path, journal, declared_by: str, fault=None, call=memory_call):
        self.session_dir = Path(session_dir)
        self.journal = journal
        self.declared_by = declared_by
        self.fault = fault or (lambda point: None)  # crash injection points for tests; no-op in production
        self.call = call

    def write(self, op_key: str, question_id: str, answer: dict, action: dict, **confirmation) -> dict:
        """Journal the intent (write-ahead; it also records the operator's confirmation), then run the steps.
        Returns the ``done`` record."""
        planned = [] if "dismissed" in action else list(STEPS)
        intent = self.journal.append("intent", op_key=op_key, question_id=question_id, answer=answer, action=action,
                                     planned=planned, **confirmation)
        self.fault("after_intent")
        return self._run(intent, {})

    def _run(self, intent: dict, have: dict[str, str]) -> dict:
        """Steps are journaled one by one (status ``step``); one final ``ok`` result per op_key names every
        written id, which is the record the evaluation counts as the commit (CONTRACT §3.4, 0.2)."""
        op_key, action = intent["op_key"], intent["action"]
        ids = dict(have)
        for step in intent["planned"]:
            if step in ids:
                continue
            event = self.call(self.session_dir, step_argv(step, action, self.declared_by, ids.get("declared_removal")))
            self.fault(f"after_memory:{step}")
            ids[step] = event["id"]
            self.journal.append("memory_result", op_key=op_key, step=step, event_id=event["id"], status="step",
                                written_ids=[event["id"]])
            self.fault(f"after_journal:{step}")
        if not any(r["op_key"] == op_key and r.get("status") == "ok" for r in self.journal.of_kind("memory_result")):
            self.journal.append("memory_result", op_key=op_key, status="ok",
                                written_ids=[ids[s] for s in intent["planned"]])
            self.fault("after_journal:result")
        return self.journal.append("done", op_key=op_key, question_id=intent["question_id"], events=ids)

    def recover(self, intent: dict) -> dict:
        """Complete an intent that has no ``done``: memory is the truth, the journal only a claim about it."""
        op_key = intent["op_key"]
        events = load_events(self.session_dir / "memory.jsonl")
        found = find_steps(events, op_key)
        if any(len(found[s]) > 1 for s in STEPS):
            raise RecoveryConflict(f"{op_key}: memory holds {[(s, [e['id'] for e in found[s]]) for s in STEPS]}; "
                                   "more than one event per step, recovery does not choose")
        have = {s: found[s][0]["id"] for s in STEPS if found[s]}
        if "storage_link" in have and found["storage_link"][0]["target_id"] != have.get("declared_removal"):
            raise RecoveryConflict(f"{op_key}: storage link {have['storage_link']} does not point at this intent's declared removal")
        for result in self.journal.of_kind("memory_result"):
            if result["op_key"] == op_key and "step" in result and have.get(result["step"]) != result["event_id"]:
                raise RecoveryConflict(f"{op_key}: journal acknowledges {result['event_id']} for {result['step']} "
                                       f"but memory holds {have.get(result['step'])!r}; memory lost or changed an acknowledged event")
        missing = [s for s in intent["planned"] if s not in have]
        self.journal.append("recovery", op_key=op_key, found=have, completing=missing)
        for step, event_id in have.items():
            if not any(r["op_key"] == op_key and r.get("step") == step for r in self.journal.of_kind("memory_result")):
                self.journal.append("memory_result", op_key=op_key, step=step, event_id=event_id, status="step",
                                    written_ids=[event_id], via="recovery_scan")
        return self._run(intent, have)
