"""LIVE-CORE coordinator: deterministic owner of a live session's questions, answers and memory writes.

Architecture A2 (CONTRACT ``anastylos-live-contract@0.3``): the coordinator owns state, validation and
persistence; an optional ``Proposer`` (L1) may only propose wording or an interpretation, which is
gated, else the L0 result is used. Nothing reaches memory before the operator confirmed a read-back.
The operator language is a session setting (``languages.py``; English is the product language).
L1 wording can be computed ahead (``prefetch_wording``) so no model call sits between a question and
the operator; a prefetched text is bound to the question's op_key (revision) and to its L0 facts.

    python tools/live/coordinator.py demo --out C:\\ANASTYLOS\\data\\live-dev\\A\\<run>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path

import states as st
from contracts import REPO, CorruptLogError, answers, load_events
from gate import interpretation_gate, result_errors, wording_gate
from interpret import check_input, to_answer
from journal import Journal, JournalCorrupt
from languages import language as get_language
from lock import SessionLock
from memory_writer import MemoryCallFailed, MemoryWriter, RecoveryConflict, census
from questions import QuestionBook
from templates import automatic_anchors, operator_anchors

JOURNAL_NAME = "journal.jsonl"
DEFAULT_PROVIDER_CALL_CAP = 20


class Stopped(RuntimeError):
    """The coordinator refuses to act until a human resolved the reported condition."""


class Coordinator:
    def __init__(self, run_dir: Path, memory_dir: Path, run_dirs: list[Path], phase_words: dict, declared_by: str,
                 proposer=None, provider_call_cap: int = DEFAULT_PROVIDER_CALL_CAP, fault=None, memory_call=None,
                 language: str = "en", session_fields: dict | None = None):
        self.lang = get_language(language)
        self.session_fields = dict(session_fields or {})  # e.g. LIVE-01: round, die_sha256, delivery_delay_s
        self.run_dir, self.memory_dir = Path(run_dir), Path(memory_dir)
        self.phase_words, self.declared_by = phase_words, declared_by
        self.proposer, self.provider_call_cap, self.provider_calls = proposer, provider_call_cap, 0
        self.book = QuestionBook(run_dirs)
        self.journal = Journal(self.run_dir / JOURNAL_NAME)
        self.lock = SessionLock(self.memory_dir)
        writer_args = {"fault": fault} | ({"call": memory_call} if memory_call else {})
        self.writer = MemoryWriter(self.memory_dir, self.journal, declared_by, **writer_args)
        self.live_session_id: str | None = None
        self.states: dict[str, st.QuestionState] = {}
        self.prompts: dict[str, dict] = {}
        self.seen_inputs: set[str] = set()
        self.prefetched: dict[str, dict] = {}  # op_key -> {"facts_sha256", "verdict", "text"}
        self.stopped: str | None = None

    # ---------------------------------------------------------------- lifecycle

    def open(self) -> "Coordinator":
        """Lock, load the journal, check memory, recover unfinished intents, then sync question revisions."""
        self.lock.acquire({"run_dir": str(self.run_dir)})
        try:
            self.journal.load()
        except JournalCorrupt as exc:
            return self._stop(str(exc))
        starts = self.journal.of_kind("session_start")
        if starts:
            self.live_session_id = starts[0]["live_session_id"]
        else:
            self.live_session_id = "live-" + uuid.uuid4().hex[:12]
            self.journal.append("session_start", live_session_id=self.live_session_id, memory_dir=str(self.memory_dir),
                                runs=[str(r) for r in self.book.run_dirs], declared_by=self.declared_by,
                                phase_words=self.phase_words, language=self.lang.code, **self.session_fields)
        if starts and starts[0].get("language", "de") != self.lang.code:
            return self._stop(f"this live session was started in language {starts[0].get('language', 'de')!r}; "
                              f"reopen it with that language, not {self.lang.code!r}")
        self._replay()
        try:
            events = load_events(self.memory_dir / "memory.jsonl")
        except CorruptLogError as exc:
            return self._stop(f"memory is unreadable, nothing is written: {exc}")
        unlinked = census(events)["observations_without_tray_link"]
        if unlinked:
            return self._stop(f"observations {unlinked} are in memory without a tray link (import done, tray link missing). "
                              "Do not rerun session.py; record the tray link by hand after checking the evidence.")
        if not self._recover():
            return self
        for qid, revision, fp in self.book.sync():
            self._new_revision(qid, revision, fp)
        return self

    def close(self) -> None:
        self.lock.release()

    def _stop(self, reason: str) -> "Coordinator":
        self.stopped = reason
        return self

    def _require_running(self) -> None:
        if self.stopped:
            raise Stopped(self.stopped)

    def _replay(self) -> None:
        """Rebuild question states, prompts, revisions and seen inputs from the journal."""
        for record in self.journal.records:
            if record.get("input_sha256"):
                self.seen_inputs.add(record["input_sha256"])
            if record["kind"] == "question_state" and record["state"] is not None:
                self.book.restore(record["question_id"], record["revision"], record["fingerprint"])
                state = self.states.setdefault(record["op_key"], st.QuestionState(record["op_key"]))
                state.state = record["state"]
                if "prompt" in record:
                    self.prompts[record["op_key"]] = record["prompt"]
                if "answer" in record:
                    state.answer = record["answer"]
                if "read_back" in record:
                    state.read_back = record["read_back"]
            elif record["kind"] == "done":
                self.states[record["op_key"]].state = st.COMMITTED
        # the intent is the durable confirmation: without its done record the write is still in flight
        done = {r["op_key"] for r in self.journal.of_kind("done")}
        intents = {r["op_key"] for r in self.journal.of_kind("intent")}
        for intent in self.journal.of_kind("intent"):
            if intent["op_key"] not in done:
                self.states[intent["op_key"]].state = st.CONFIRMED
        # a CONFIRMED record without its intent is a confirmation that never became durable: nothing was
        # written, so the read-back is open again and the operator confirms once more
        for key, state in self.states.items():
            if state.state == st.CONFIRMED and key not in intents:
                state.state = st.READ_BACK
                self.journal.append("recovery", op_key=key, found={}, completing=[],
                                    note="confirmation not durable (no intent); read-back open again")
        for record in self.journal.of_kind("provider_call"):
            if record.get("prefetch"):
                self.prefetched[record["op_key"]] = record["prefetch"]

    def _recover(self) -> bool:
        done = {r["op_key"] for r in self.journal.of_kind("done")}
        for intent in self.journal.of_kind("intent"):
            if intent["op_key"] in done:
                continue
            try:
                self.writer.recover(intent)
            except (RecoveryConflict, MemoryCallFailed, CorruptLogError) as exc:
                self._stop(f"recovery of {intent['op_key']} stopped: {exc}")
                return False
            self._set(self.states[intent["op_key"]], st.COMMITTED, note="completed by recovery")
        return True

    # ---------------------------------------------------------------- state bookkeeping

    def op_key(self, question_id: str) -> str:
        return st.make_op_key(self.live_session_id, question_id, self.book.revision(question_id))

    def state_of(self, question_id: str) -> st.QuestionState:
        return self.states[self.op_key(question_id)]

    def _set(self, state: st.QuestionState, target: str, **fields) -> None:
        previous = state.state if state.state == target else state.move(target)
        _, qid, revision = st.parse_op_key(state.op_key)
        self._question_record(state.op_key, qid, revision, self.book.revisions[qid][1], previous, target, **fields)

    def _question_record(self, op_key: str, qid: str, revision: int, fp: str, previous, target, **fields) -> dict:
        """One ``question_state`` record with the static fields of CONTRACT §3.4 (0.2) on every record."""
        return self.journal.append("question_state", op_key=op_key, question_id=qid, revision=revision, fingerprint=fp,
                                   state=target, **{"from": previous}, **self._static_fields(qid), **fields)

    def _static_fields(self, qid: str) -> dict:
        question = self.book.questions.get(qid) or {}
        kind = question.get("kind")
        order = self.book.live_order() if qid in self.book.questions else []
        return {"slot": order.index(qid) + 1 if qid in order else None, "question_kind": kind,
                "cell": question.get("cell"),
                "deposit_window_s": question.get("deposit_window_s") if kind == "source_of_part" else None,
                "removal_window_s": question.get("window_s") if kind == "destination_of_removal" else None,
                "source_box_mm": _box(question.get("sources", [])), "deposit_t_wall": None}

    def _new_revision(self, qid: str, revision: int, fp: str) -> None:
        for key, old in self.states.items():
            _, oqid, orev = st.parse_op_key(key)
            if oqid == qid and orev < revision and old.can(st.SUPERSEDED):
                self._question_record(key, qid, orev, fp, old.move(st.SUPERSEDED), st.SUPERSEDED,
                                      note=f"revision {revision} replaces it")
        state = st.QuestionState(st.make_op_key(self.live_session_id, qid, revision))
        self.states[state.op_key] = state
        self._question_record(state.op_key, qid, revision, fp, None, st.OPEN)

    def reload_questions(self) -> list[str]:
        """Re-read the run folders after a re-link; changed questions get a new revision (older op_keys go stale)."""
        self._require_running()
        changes = self.book.reload()
        for qid, revision, fp in changes:
            self._new_revision(qid, revision, fp)
        return [qid for qid, _, _ in changes]

    def committed_answers(self) -> list[dict]:
        intents = {r["op_key"]: r for r in self.journal.of_kind("intent")}
        out = []
        for done in self.journal.of_kind("done"):
            intent = intents[done["op_key"]]
            if "declared_removal" in done["events"]:
                out.append({"question_id": intent["question_id"], "label": intent["answer"].get("label"),
                            "choice": intent["answer"].get("choice"), "t_s": intent["action"].get("t_s"),
                            "declared_removal": done["events"]["declared_removal"]})
        return out

    # ---------------------------------------------------------------- asking

    def ask(self, question_id: str) -> dict:
        """The question's prompt (L0, or a gated L1 wording). Asking again repeats the same prompt."""
        self._require_running()
        state = self.state_of(question_id)
        if state.state == st.ASKED:
            return self.prompts[state.op_key]
        if state.state not in (st.OPEN, st.DEFERRED, st.FAILED):
            raise st.TransitionError(f"{state.op_key} is {state.state}; it cannot be asked now")
        prompt = self._l0_prompt(question_id)
        prompt["op_key"] = state.op_key
        prompt["rubric"] = self._rubric(prompt, prompt["text"], prompt["anchor"] is not None)
        prompt["wording"] = "L0"
        l1_text = self._prefetched_text(state.op_key, prompt)
        if l1_text is None and self.proposer is not None:
            output = self._propose("question_wording", self._wording_payload(state.op_key, prompt),
                                   lambda out: wording_gate(out, prompt, self.lang))
            l1_text = output["text"] if output is not None else None
        if l1_text is not None:
            prompt["text"], prompt["wording"] = l1_text, "L1"
            prompt["rubric"] = self._rubric(prompt, l1_text, bool(prompt["facts"].get("anchor"))
                                            and prompt["facts"]["anchor"] in l1_text)
        self.prompts[state.op_key] = prompt
        self._set(state, st.ASKED, prompt=prompt, **self._asked_fields(prompt))
        return prompt

    @staticmethod
    def _asked_fields(prompt: dict) -> dict:
        """The ASKED fields of CONTRACT §3.4 (0.2); repeated whenever a question is put to the operator again."""
        return {"wording": prompt["wording"], "text": prompt["text"], "anchor_possible": prompt["anchor_possible"],
                "anchor_ref": prompt["anchor"], "die_ordinal": None}

    def _rubric(self, prompt: dict, text: str, has_anchor: bool) -> dict:
        return self.lang.rubric(text, prompt["anchor_possible"], has_anchor, prompt["facts"].get("phase_words"))

    @staticmethod
    def _wording_payload(op_key: str, prompt: dict) -> dict:
        return {"op_key": op_key, "kind": prompt["kind"], "mode": prompt["mode"], "facts": prompt["facts"],
                "l0_text": prompt["text"]}

    @staticmethod
    def _facts_sha256(prompt: dict) -> str:
        basis = {"text": prompt["text"], "facts": prompt["facts"], "mode": prompt["mode"]}
        return hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _prefetched_text(self, op_key: str, prompt: dict) -> str | None:
        """An accepted prefetched wording for exactly this op_key and these L0 facts, else None (journaled)."""
        _, qid, revision = st.parse_op_key(op_key)
        entry = self.prefetched.get(op_key)
        if entry is None or entry["verdict"] != "accepted":
            older = [k for k, v in self.prefetched.items() if v["verdict"] == "accepted"
                     and st.parse_op_key(k)[1] == qid and st.parse_op_key(k)[2] != revision]
            if older:
                self.journal.append("fallback", op_key=op_key, task="question_wording",
                                    reason=f"stale: prefetched wording is for {older[-1]}, the question is at revision {revision}")
            return None
        if entry["facts_sha256"] != self._facts_sha256(prompt):
            self.journal.append("fallback", op_key=op_key, task="question_wording",
                                reason="stale: the question's facts changed since the wording was prefetched")
            return None
        return entry["text"]

    def prefetch_wording(self, question_id: str, proposer) -> str:
        """Ask ``proposer`` for L1 wording now, ahead of the question (never while the operator waits).
        The verdict is journaled with the provider result; ``ask`` uses the text only for the same op_key and
        unchanged L0 facts. Returns the verdict (``accepted`` or the reason the question stays L0)."""
        self._require_running()
        state = self.state_of(question_id)
        if state.state not in (st.OPEN, st.DEFERRED):
            return f"not prefetched: {state.op_key} is {state.state}"
        if self.provider_calls >= self.provider_call_cap:
            self.journal.append("fallback", op_key=state.op_key, task="question_wording", reason="provider call cap reached")
            return "provider call cap reached"
        self.provider_calls += 1
        prompt = self._l0_prompt(question_id)
        prompt["op_key"] = state.op_key
        request_id = f"{state.op_key}:question_wording:prefetch:{len(self.journal.of_kind('provider_call')) + 1}"
        try:
            result = proposer.propose("question_wording", self._wording_payload(state.op_key, prompt), request_id)
        except Exception as exc:  # a provider bug must not take the session down: L0 stays
            reason = f"proposer raised {type(exc).__name__}: {str(exc)[:300]}"
            self.journal.append("fallback", op_key=state.op_key, task="question_wording", request_id=request_id,
                                reason=reason)
            return reason
        errors = result_errors(result, "question_wording")
        output, verdict = (None, "; ".join(errors[:5])) if errors else wording_gate(result["output"], prompt, self.lang)
        entry = {"facts_sha256": self._facts_sha256(prompt), "verdict": "accepted" if output else verdict,
                 "text": output["text"] if output else None}
        self.journal.append("provider_call", op_key=state.op_key, task="question_wording", request_id=request_id,
                            result=result, prefetch=entry)
        if output is None:
            self.journal.append("fallback", op_key=state.op_key, task="question_wording", request_id=request_id,
                                reason=verdict if errors else f"gate: {verdict}")
        self.prefetched[state.op_key] = entry
        return entry["verdict"]

    def _l0_prompt(self, question_id: str) -> dict:
        question = self.book.questions[question_id]
        events = load_events(self.memory_dir / "memory.jsonl")
        committed = self.committed_answers()
        anchors = automatic_anchors(self.book.parts_reports[question["phase_tag"]], events)
        anchors += operator_anchors(question, self.book.questions, committed)
        # removal event ids are per phase run (E-0049 in P1 is not E-0049 in P2): claims count within the phase only
        claimed = {c["choice"]: c["label"] or "ein früheres Teil" for c in committed
                   if c.get("choice") and self.book.questions[c["question_id"]]["phase_tag"] == question["phase_tag"]}
        return self.lang.build_prompt(question, self.book.parts_reports[question["phase_tag"]], self.phase_words,
                                      anchors, claimed)

    def _propose(self, task: str, payload: dict, gate) -> dict | None:
        """One gated L1 call; any failure, stale result or gate rejection journals a fallback and returns None."""
        op_key = payload["op_key"]
        if self.provider_calls >= self.provider_call_cap:
            self.journal.append("fallback", op_key=op_key, task=task, reason="provider call cap reached")
            return None
        self.provider_calls += 1
        request_id = f"{op_key}:{task}:{len(self.journal.of_kind('provider_call')) + 1}"
        try:
            result = self.proposer.propose(task, payload, request_id)
        except Exception as exc:  # a provider bug must not take the session down: L0 continues
            self.journal.append("fallback", op_key=op_key, task=task, request_id=request_id,
                                reason=f"proposer raised {type(exc).__name__}: {str(exc)[:300]}")
            return None
        self.journal.append("provider_call", op_key=op_key, task=task, request_id=request_id, result=result)
        errors = result_errors(result, task)
        if errors:
            self.journal.append("fallback", op_key=op_key, task=task, request_id=request_id, reason="; ".join(errors[:5]))
            return None
        _, qid, revision = st.parse_op_key(op_key)
        if revision != self.book.revision(qid):
            self.journal.append("fallback", op_key=op_key, task=task, request_id=request_id,
                                reason=f"stale: question is now at revision {self.book.revision(qid)}")
            return None
        output, reason = gate(result["output"])
        if output is None:
            self.journal.append("fallback", op_key=op_key, task=task, request_id=request_id, reason=f"gate: {reason}")
        return output

    # ---------------------------------------------------------------- answering

    def receive(self, item: dict) -> dict:
        """Handle one ``anastylos-live-operator-input@1``; returns what to show / say next."""
        self._require_running()
        check_input(item)
        digest = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        session, qid, revision = st.parse_op_key(item["op_key"])
        messages = self.lang.messages
        if session != self.live_session_id or qid not in self.book.questions:
            return {"status": "rejected", "message": messages["foreign"].format(op_key=item["op_key"])}
        if digest in self.seen_inputs:
            return {"status": "duplicate", "message": messages["duplicate"]}
        current = self.book.revision(qid)
        if revision != current:
            self._question_record(item["op_key"], qid, revision, self.book.revisions[qid][1], None, None,
                                  note=f"stale input ignored; question is at revision {current}", input_sha256=digest)
            self.seen_inputs.add(digest)
            return {"status": "stale", "message": messages["stale"].format(revision=current)}
        state = self.states[item["op_key"]]
        self.seen_inputs.add(digest)
        if state.state == st.ASKED:
            return self._answer(state, item, digest)
        if state.state == st.READ_BACK:
            return self._confirm(state, item, digest)
        self._question_record(state.op_key, qid, revision, self.book.revisions[qid][1], state.state, state.state,
                              note="input ignored in this state", input_sha256=digest)
        return {"status": "ignored", "state": state.state, "message": messages["ignored"].format(state=state.state)}

    def _interpret(self, kind: str, state: st.QuestionState, item: dict, l0: dict, allowed: list[str], question_text: str) -> dict:
        if self.proposer is None or item["source"] == "screen_choice":
            return l0
        payload = {"op_key": state.op_key, "kind": kind, "allowed_choices": allowed, "question_text": question_text,
                   "operator_text": item["text"]}
        output = self._propose("answer_interpretation", payload,
                               lambda out: interpretation_gate(out, allowed, item["text"], kind))
        if output is not None and output["intent"] == "not_a_part":  # contract 0.3 intent, carried in the L0 shape
            output = {**output, "intent": "describe_place", "not_a_part": True}
        return output if output is not None else l0

    def _answer(self, state: st.QuestionState, item: dict, digest: str) -> dict:
        qid = st.parse_op_key(state.op_key)[1]
        question, prompt = self.book.questions[qid], self.prompts[state.op_key]
        messages = self.lang.messages
        interp = self.lang.interpret_answer(item, question["kind"], prompt)
        interp = self._interpret(question["kind"], state, item, interp,
                                 [o["token"] for o in prompt["screen_options"]], prompt["text"])
        if interp["intent"] in ("skip", "unsure"):
            self._set(state, st.DEFERRED, input_sha256=digest, interpretation=interp, input=item,
                      outcome=interp["intent"])
            return {"status": "deferred", "outcome": interp["intent"], "message": messages["deferred"]}
        if interp["intent"] not in ("choose", "describe_place"):
            self._note(state, digest, interp, "not understood; asked again")
            return {"status": "unclear", "message": messages["unclear"] + prompt["text"]}
        answer = to_answer(question, interp, prompt, state.op_key, item["source"], destination=self.lang.destination)
        action = answers.plan(answer, self.book.questions)
        if "rejected" in action:
            self._note(state, digest, interp, f"answers.plan rejected: {action['rejected']}")
            return {"status": "unclear", "message": messages["plan_rejected"] + prompt["text"]}
        text = self.lang.read_back(question, answer, prompt)
        self._set(state, st.ANSWERED, input_sha256=digest, interpretation=interp, answer=answer, input=item)
        self.writer.fault("after_answered")
        self._set(state, st.READ_BACK, read_back=text)
        state.answer, state.read_back = answer, text
        return {"status": "read_back", "read_back": text, "answer": answer}

    def _note(self, state: st.QuestionState, digest: str, interp: dict, note: str) -> None:
        _, qid, revision = st.parse_op_key(state.op_key)
        self._question_record(state.op_key, qid, revision, self.book.revisions[qid][1], state.state, state.state,
                              note=note, input_sha256=digest, interpretation=interp)

    def _confirm(self, state: st.QuestionState, item: dict, digest: str) -> dict:
        # The confirmation authorizes a memory write, so it stays deterministic: no model reads a "yes" into an answer.
        messages = self.lang.messages
        interp = self.lang.interpret_read_back(item)
        if interp["intent"] == "reject":
            self._set(state, st.ASKED, input_sha256=digest, interpretation=interp, note="operator rejected the read-back",
                      **self._asked_fields(self.prompts[state.op_key]))
            return {"status": "asked_again", "message": messages["asked_again"] + self.prompts[state.op_key]["text"]}
        if interp["intent"] != "confirm":
            self._note(state, digest, interp, "read-back answer not understood")
            return {"status": "read_back", "read_back": messages["yes_or_no"] + state.read_back}
        qid = st.parse_op_key(state.op_key)[1]
        # The revision was checked in receive(); the plan is recomputed against the questions as they are now.
        action = answers.plan(state.answer, self.book.questions)
        if "rejected" in action:
            self._set(state, st.ASKED, input_sha256=digest, note=f"answers.plan rejected at commit: {action['rejected']}",
                      **self._asked_fields(self.prompts[state.op_key]))
            return {"status": "asked_again", "message": self.prompts[state.op_key]["text"]}
        # CONFIRMED is journaled before the intent; the intent (write-ahead, before any memory call) makes it durable
        self._set(state, st.CONFIRMED, resolved=self._resolved(qid, state.answer))
        self.writer.fault("after_confirmed")
        try:
            done = self.writer.write(state.op_key, qid, state.answer, action, input_sha256=digest, interpretation=interp)
        except (MemoryCallFailed, CorruptLogError) as exc:
            self._set(state, st.FAILED, note=f"memory write failed: {exc}")
            self._stop(f"memory write for {state.op_key} failed; reopen to recover: {exc}")
            return {"status": "failed", "message": str(exc)}
        self._set(state, st.COMMITTED)
        return {"status": "committed", "events": done["events"]}

    def _resolved(self, qid: str, answer: dict) -> dict:
        """What the confirmed answer resolves (CONTRACT §3.4 ``resolved``), for the evaluation."""
        question = self.book.questions[qid]
        if answer.get("not_a_part"):
            return {"outcome": "no_part", "source_box_mm": None, "destination": None, "place_text": None}
        if question["kind"] == "source_of_part":
            chosen = next((c for c in question.get("candidates", []) if c["removal_event"] == answer.get("choice")), None)
            return {"outcome": "source", "source_box_mm": _box(chosen["sources"]) if chosen else None,
                    "destination": question.get("cell"), "place_text": answer.get("source")}
        return {"outcome": "destination", "source_box_mm": None, "destination": answer.get("destination"),
                "place_text": answer.get("destination")}

    def defer(self, question_id: str, leave_unresolved: bool = False, reason: str | None = None) -> None:
        self._require_running()
        self._set(self.state_of(question_id), st.LEFT_UNRESOLVED if leave_unresolved else st.DEFERRED,
                  **({"reason": reason} if reason else {}))

    def resume(self, question_id: str) -> str:
        """Where a question stands after a restart. An answer that was journaled but never read back
        (crash between ANSWERED and READ_BACK) is dropped: the question is asked again, nothing was written."""
        self._require_running()
        state = self.state_of(question_id)
        if state.state == st.ANSWERED:
            self._set(state, st.ASKED, note="restart before the read-back; asked again",
                      **self._asked_fields(self.prompts[state.op_key]))
        return state.state

    def last_outcome(self, question_id: str) -> str | None:
        """``skip`` / ``unsure`` of the latest deferral of the current revision, if any."""
        op_key = self.op_key(question_id)
        deferrals = [r for r in self.journal.of_kind("question_state") if r["op_key"] == op_key and r["state"] == st.DEFERRED]
        return deferrals[-1].get("outcome") if deferrals else None

    def asks(self, question_id: str) -> int:
        """How often the question's current revision was put to the operator (re-asks after a rejected
        read-back count as the same attempt)."""
        op_key = self.op_key(question_id)
        return sum(1 for r in self.journal.of_kind("question_state") if r["op_key"] == op_key
                   and r["state"] == st.ASKED and r.get("from") in (st.OPEN, st.DEFERRED, st.FAILED))

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for record in self.journal.records:
            kinds[record["kind"]] = kinds.get(record["kind"], 0) + 1
        latest = {}
        for key, state in self.states.items():
            latest[key] = state.state
        return {"live_session_id": self.live_session_id, "journal": str(self.journal.path), "records": kinds,
                "states": latest, "stopped": self.stopped,
                "census": census(load_events(self.memory_dir / "memory.jsonl")) if not self.stopped else None}


def _box(sources: list[dict]) -> list[float] | None:
    """[x, y, w, h] in sheet mm around the sources' centroids (zero size for one point), or None."""
    points = [s["centroid_mm"] for s in sources if s.get("centroid_mm")]
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


# -------------------------------------------------------------------- CLI demo

def _prepare_demo(fixture: Path, out: Path) -> tuple[list[Path], Path]:
    runs = []
    for tag in ("P1", "P2", "P3"):
        run = out / "runs" / tag
        if not run.exists():
            run.mkdir(parents=True)
            for name in ("questions.json", "parts.json"):
                shutil.copyfile(fixture / tag / name, run / name)
        runs.append(run)
    memory = out / "memory"  # the folder name is the memory session name, as in FULL-01
    if not (memory / "memory.jsonl").exists():
        memory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture / "audit" / "memory.before-answers.jsonl", memory / "memory.jsonl")
    return runs, memory


def _operator_input(op_key: str, text: str) -> dict:
    from journal import utc_now
    return {"schema": "anastylos-live-operator-input@1", "op_key": op_key, "source": "typed", "text": text,
            "choice": None, "received_at": utc_now(), "transcript": None}


def demo(args) -> int:
    out = args.out.resolve()
    if REPO in out.parents or out == REPO:
        print(f"--out must be outside the repository (CONTRACT §2), got {out}", file=sys.stderr)
        return 2
    runs, memory = _prepare_demo(args.fixture.resolve(), out)
    phase_words = json.loads(args.phase_words.read_text(encoding="utf-8"))["phases"]
    coordinator = Coordinator(out, memory, runs, phase_words, declared_by=args.operator, language=args.language).open()
    try:
        if coordinator.stopped:
            print(f"STOPPED: {coordinator.stopped}")
            return 1
        committed = {c["question_id"] for c in coordinator.committed_answers()}
        qid = args.question or next(q for q in coordinator.book.live_order() if q not in committed)
        prompt = coordinator.ask(qid)
        print(f"[{prompt['op_key']}] {prompt['wording']} ({prompt['mode']}): {prompt['text']}")
        for option in prompt["screen_options"]:
            print(f"   Screen {option['token']}: {option['description'] or '(marked only)'}"
                  + (f"  [already named for: {option['claimed_by']}]" if option["claimed_by"] else ""))
        answer_text = input("Answer> ") if args.interactive else args.answer
        print(f"Answer: {answer_text}")
        reply = coordinator.receive(_operator_input(prompt["op_key"], answer_text))
        print(f"-> {reply['status']}: {reply.get('read_back') or reply.get('message')}")
        if reply["status"] == "read_back":
            confirm_text = input("Confirm> ") if args.interactive else args.confirm
            print(f"Confirm: {confirm_text}")
            reply = coordinator.receive(_operator_input(prompt["op_key"], confirm_text))
            print(f"-> {reply['status']}: {reply.get('events') or reply.get('message') or reply.get('read_back')}")
        print(json.dumps(coordinator.summary(), indent=2, ensure_ascii=False))
        return 0
    finally:
        coordinator.close()


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("demo", help="L0 question -> typed answer -> read-back -> confirm -> write on a memory copy")
    run.add_argument("--fixture", type=Path, default=here / "tests" / "fixtures")
    run.add_argument("--out", type=Path, required=True, help=r"run folder, e.g. C:\ANASTYLOS\data\live-dev\A\<run>")
    run.add_argument("--language", default="en", choices=("en", "de"), help="operator language (default: English)")
    run.add_argument("--phase-words", type=Path, default=None,
                     help="operator setup: words for each phase (default: the fixture's words in --language)")
    run.add_argument("--operator", default="operator (live demo)", help="declared_by / confirmed_by in memory")
    run.add_argument("--question", default=None, help="question id (default: first uncommitted in live order)")
    run.add_argument("--answer", default=None, help="typed answer (default: an example in --language)")
    run.add_argument("--confirm", default=None, help="typed confirmation (default: yes / ja)")
    run.add_argument("--interactive", action="store_true", help="read answer and confirmation from the keyboard")
    args = parser.parse_args(argv)
    english = args.language == "en"
    fixtures = here / "tests" / "fixtures"
    args.phase_words = args.phase_words or fixtures / ("phase-words.en.json" if english else "phase-words.json")
    args.answer = args.answer or ("Screw from the top-left hole of the back plate" if english
                                  else "Schraube aus dem Loch oben links an der Rückplatte")
    args.confirm = args.confirm or ("yes" if english else "ja")
    sys.stdout.reconfigure(encoding="utf-8")  # operator words carry umlauts
    return demo(args)


if __name__ == "__main__":
    raise SystemExit(main())
