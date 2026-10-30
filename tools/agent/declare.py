"""Operator-initiated declarations: a part removed without a question (``EXTRA``) or interchangeable parts (group).

Not a P0 question: the operator starts it. The declaration is validated (``answers.plan`` for an extra part,
member ids for a group), read back in English, and written only after an explicit yes. Writes go through the
live core's write-ahead pieces, used unchanged: ``Journal`` (own file ``declare/journal.jsonl``), the session
``SessionLock`` (so a declaration never runs beside the question loop), and ``MemoryWriter`` for an extra part
(the same two memory events ``answers.apply`` writes). A group is one ``record group`` call with the op_key marker
in its note, so recovery finds it in memory instead of writing it twice.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parents[1] / "live"
if str(LIVE) not in sys.path:
    sys.path.insert(0, str(LIVE))

import english  # noqa: E402  (tools/live, read-back interpretation)
from contracts import answers, load_events  # noqa: E402
from journal import Journal  # noqa: E402
from lock import SessionLock  # noqa: E402
from memory_writer import MARKER, MemoryWriter, memory_call  # noqa: E402

from state import SessionError, SessionState  # noqa: E402

PENDING, CONFIRMED, COMMITTED, WITHDRAWN = "READ_BACK", "CONFIRMED", "COMMITTED", "SUPERSEDED"
STATIC = {"slot": None, "question_kind": None, "cell": None, "deposit_window_s": None, "removal_window_s": None,
          "source_box_mm": None, "deposit_t_wall": None}


def _q(text: str) -> str:
    return f"“{text}”"


def _place(location: str) -> str:
    cell = re.fullmatch(r"cell-(\d+)", location)
    return f"in cell {cell.group(1)}" if cell else f"at {_q(location)}"


def _mmss(t_s) -> str:
    return f"{int(t_s) // 60}:{int(t_s) % 60:02d}"


class Declarations:
    def __init__(self, state: SessionState, fault=None, call=memory_call):
        self.state = state
        self.journal = Journal(state.root / "declare" / "journal.jsonl")
        self.lock = SessionLock(state.memory_dir)
        self.fault = fault or (lambda point: None)
        self.call = call
        self.writer = MemoryWriter(state.memory_dir, self.journal, state.doc["operator"], fault=self.fault, call=call)

    # ------------------------------------------------------------------ lifecycle

    def open(self) -> "Declarations":
        self.lock.acquire({"run_dir": str(self.journal.path.parent)})
        self.journal.load()
        self._recover()
        return self

    def close(self) -> None:
        self.lock.release()

    def _states(self) -> dict[str, dict]:
        latest = {}
        for record in self.journal.of_kind("question_state"):
            latest[record["op_key"]] = record
        for record in self.journal.of_kind("done"):
            latest[record["op_key"]] = {**latest.get(record["op_key"], {}), "state": COMMITTED}
        return latest

    def details(self, op_key: str) -> dict:
        """The read-back, answer and action of a declaration (from its latest READ_BACK record)."""
        return next(r for r in reversed(self.journal.of_kind("question_state")) if r["op_key"] == op_key and "read_back" in r)

    def pending(self) -> dict | None:
        return next((r for r in self._states().values() if r["state"] == PENDING), None)

    def committed_keys(self) -> set[str]:
        """Content hashes of committed declarations (the hash sits on their READ_BACK record)."""
        return {self.details(k)["content_sha256"] for k, r in self._states().items() if r["state"] == COMMITTED}

    def _record(self, op_key: str, declaration_id: str, state: str, **fields) -> dict:
        return self.journal.append("question_state", op_key=op_key, question_id=declaration_id, revision=1, state=state,
                                   **STATIC, **fields)

    def _recover(self) -> None:
        """Finish every confirmed declaration whose write did not finish; never write one twice."""
        done = {r["op_key"] for r in self.journal.of_kind("done")}
        for intent in self.journal.of_kind("intent"):
            if intent["op_key"] in done:
                continue
            if intent["action"].get("kind") == "group":
                self._write_group(intent, recovering=True)
            else:
                self.writer.recover(intent)
            self._record(intent["op_key"], intent["question_id"], COMMITTED, note="completed by recovery")
        intents = {r["op_key"] for r in self.journal.of_kind("intent")}
        for op_key, record in self._states().items():
            if record["state"] == CONFIRMED and op_key not in intents:  # confirmation never became durable
                details = self.details(op_key)
                self._record(op_key, record["question_id"], PENDING, note="confirmation not durable; read-back open again",
                             **{k: details[k] for k in ("read_back", "answer", "action", "content_sha256")})

    # ------------------------------------------------------------------ propose / confirm

    def _new_id(self) -> str:
        return f"D-{len({r['question_id'] for r in self.journal.of_kind('question_state')}) + 1:02d}"

    def _questions(self) -> dict:
        runs = self.state.done_runs()
        if not runs:
            raise SessionError("no processed phase yet; run `phase` first")
        return answers.load_runs(runs)[0]

    def propose_extra(self, phase: str, label: str, destination: str, source: str | None = None,
                      t_s: float | None = None) -> dict:
        """An extra part the operator removed without being asked. Returns the read-back."""
        destination = english.destination_value(destination)
        answer = {"question": "EXTRA", "phase": phase, "label": label, "source": source, "destination": destination,
                  "t_s": t_s}
        action = answers.plan(answer, self._questions())
        if "rejected" in action:
            raise SessionError(f"This declaration cannot be recorded: {action['rejected']}.")
        words = self.state.phase(phase)["label"] if self.state.phase(phase) else phase
        origin = f"from {_q(source)}" if source else "from a place you did not state"
        at = f" at about {_mmss(t_s)}" if t_s is not None else ""
        read_back = (f"I will record: {_q(label)} was removed {words} (phase {phase}){at}, {origin}, and is now "
                     f"{_place(destination)}. Is that correct? Answer yes or no.")
        return self._propose(answer, {**action, "kind": "extra"}, read_back)

    def propose_group(self, members: list[str], label: str | None) -> dict:
        """Interchangeable parts, by the operator's statement (not measured)."""
        ids = {e["id"] for e in load_events(self.state.memory_dir / "memory.jsonl")}
        unknown = [m for m in members if m not in ids]
        if len(set(members)) < 2 or unknown:
            raise SessionError(f"A group needs at least two recorded parts; unknown: {unknown or 'none'}.")
        answer = {"question": "GROUP", "members": list(members), "label": label}
        named = f" as {_q(label)}" if label else ""
        read_back = (f"I will record: {', '.join(members)} are interchangeable{named}, according to you; this is not "
                     "measured, and no part is matched to a particular hole. Is that correct? Answer yes or no.")
        return self._propose(answer, {"kind": "group", "members": list(members), "label": label}, read_back)

    def _propose(self, answer: dict, action: dict, read_back: str) -> dict:
        content = hashlib.sha256(json.dumps(answer, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if content in self.committed_keys():
            raise SessionError("This declaration is already recorded; nothing new to write.")
        previous = self.pending()
        if previous:  # a new declaration replaces the unconfirmed one; its confirmation becomes stale
            self._record(previous["op_key"], previous["question_id"], WITHDRAWN, note="replaced by a new declaration")
        declaration_id = self._new_id()
        op_key = f"declare-{self.state.root.name}:{declaration_id}:1".replace(" ", "_")
        answer = {**answer, "provenance": f"live op_key={op_key}; operator declaration"}
        if action["kind"] == "extra":  # the note carries the marker that lets recovery find the events
            action = {**answers.plan(answer, self._questions()), "kind": "extra"}
        else:
            action = {**action, "note": f"interchangeable per operator declaration; live op_key={op_key}"}
        self._record(op_key, declaration_id, PENDING, read_back=read_back, answer=answer, action=action,
                     content_sha256=content)
        return {"op_key": op_key, "declaration": declaration_id, "read_back": read_back}

    def confirm(self, op_key: str, reply: dict) -> dict:
        """The operator's yes / no to a read-back; only yes on the current pending declaration writes."""
        item = {"schema": "anastylos-live-operator-input@1", "op_key": op_key, **reply}
        digest = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if any(r.get("input_sha256") == digest for r in self.journal.records):
            return {"status": "duplicate", "message": "This reply was already handled; ignored."}
        current = self._states().get(op_key)
        if current is None or current["state"] != PENDING:
            state = current["state"] if current else "unknown"
            return {"status": "stale", "message": f"This declaration is no longer waiting for a reply ({state})."}
        current = {**current, **self.details(op_key)}
        intent = english.interpret_read_back({"source": "typed", "text": "", **reply})["intent"]
        if intent == "reject":
            self._record(op_key, current["question_id"], WITHDRAWN, input_sha256=digest, note="operator said no")
            return {"status": "withdrawn", "message": "Nothing was recorded."}
        if intent != "confirm":
            return {"status": "read_back", "message": "Please answer yes or no. " + current["read_back"]}
        self._record(op_key, current["question_id"], CONFIRMED, input_sha256=digest)
        self.fault("after_confirmed")
        if current["action"]["kind"] == "group":
            intent_record = self.journal.append("intent", op_key=op_key, question_id=current["question_id"],
                                                answer=current["answer"], action=current["action"],
                                                planned=["compatible_group"], input_sha256=digest)
            self.fault("after_intent")
            done = self._write_group(intent_record, recovering=False)
        else:  # the live core's write-ahead writer: intent, one memory call per step, one ok result, done
            done = self.writer.write(op_key, current["question_id"], current["answer"], current["action"],
                                     input_sha256=digest)
        self._record(op_key, current["question_id"], COMMITTED)
        return {"status": "committed", "events": done["events"], "message": "Recorded."}

    def _write_group(self, intent: dict, recovering: bool) -> dict:
        op_key, action = intent["op_key"], intent["action"]
        events = load_events(self.state.memory_dir / "memory.jsonl")
        found = [e for e in events if e["type"] == "compatible_group"
                 and (MARKER.search((e.get("basis") or {}).get("note") or "") or [None, None])[1] == op_key]
        if len(found) > 1:
            raise SessionError(f"{op_key}: memory holds {len(found)} groups for one declaration; recovery stops.")
        if found:
            event_id = found[0]["id"]
            if not any(r["op_key"] == op_key and r.get("step") == "compatible_group" for r in self.journal.of_kind("memory_result")):
                self.journal.append("memory_result", op_key=op_key, step="compatible_group", event_id=event_id,
                                    status="step", written_ids=[event_id], via="recovery_scan" if recovering else None)
        else:
            argv = ["record", "group", "--members", ",".join(action["members"]), "--declared-by", self.state.doc["operator"],
                    "--note", action["note"]] + (["--label", action["label"]] if action.get("label") else [])
            event = self.call(self.state.memory_dir, argv)
            self.fault("after_memory:compatible_group")
            event_id = event["id"]
            self.journal.append("memory_result", op_key=op_key, step="compatible_group", event_id=event_id,
                                status="step", written_ids=[event_id])
        if not any(r["op_key"] == op_key and r.get("status") == "ok" for r in self.journal.of_kind("memory_result")):
            self.journal.append("memory_result", op_key=op_key, status="ok", written_ids=[event_id])
        return self.journal.append("done", op_key=op_key, question_id=intent["question_id"],
                                   events={"compatible_group": event_id})
