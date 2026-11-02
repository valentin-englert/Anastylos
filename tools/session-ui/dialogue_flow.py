"""Spoken question round of one session: the dialogue agent in front of the coordinator (via ``adapter``).

One open question per cell ("What's in cell 3?"). The detector's count is never said and never trusted. The answer
is understood by the model first (strict output, every word checked against the transcript; rules only as a fallback),
read back in one sentence, and only the operator's "yes" writes: each detected question of that cell then goes through
the coordinator's own answer / confirm path, extra parts through ``write_operator_notes``, surplus questions are left
unresolved (never deleted). Spoken text is a catalog line or a sentence built from the operator's checked words.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "live"))

from catalog.selector import where_words  # noqa: E402
from dialogue import bundle, items as cell_items, settings  # noqa: E402
from dialogue.agent import DialogueAgent  # noqa: E402
from dialogue.executor import _NOISE, Executor, MuseClient, extract_by_rule, model_key  # noqa: E402

ALL_DONE = "There are no more questions."
UNAVAILABLE = "The workstation could not handle that. Please use the screen."
SURPLUS_REASON = "dialogue: left unresolved after the cell was described"
_FLOWS: dict[tuple[str, str], "DialogueFlow"] = {}
_GUARD = threading.Lock()
_LEADING_NO = re.compile(r"^\W*(?:no|nope|wrong|not (?:really|quite)|incorrect)\b[\s,.:;-]*", re.IGNORECASE)


def _default_executor(root: Path, session_id: str) -> Executor:
    """Muse extracts first; short timeout, no retries. Without a key in reach the rules answer alone."""
    if not model_key(os.environ):
        return Executor(None)
    return Executor(MuseClient(timeout_s=8.0, retries=0, usage_log=Path(root) / session_id / "dialogue-muse-usage.jsonl"), "minimal")


def flow_for(root: Path, session_id: str, api) -> "DialogueFlow":
    with _GUARD:
        key = (str(root), session_id)
        if key not in _FLOWS:
            _FLOWS[key] = DialogueFlow(root, session_id, api)
        return _FLOWS[key]


def _speak(text: str, kind: str, basis_ref: str | None = None) -> dict:
    return {"text": text, "kind": kind, "basis_ref": basis_ref}


class DialogueFlow:
    def __init__(self, root: Path, session_id: str, api, executor: Executor | None = None):
        self.root, self.session_id, self.api = Path(root), session_id, api
        self.agent = DialogueAgent(settings.load(self.root)["persona"], executor or _default_executor(root, session_id),
                                   open_items_path=self.root / session_id / "dialogue-open-items.jsonl")
        self.current: dict | None = None
        self._closing_marker = self.root / session_id / "dialogue-closing-done"
        self._notes = self.root / session_id / "dialogue-notes.jsonl"
        self._tail_offered_path = self.root / session_id / "dialogue-tail-offered.jsonl"
        self._tail_offered = self._load_tail_offers()
        self._tail_queue: dict[str, list[str]] = {}
        self._tail_seeded = False
        self._last_origin: str | None = None
        self._marker = self.root / session_id / "dialogue-pending-open"  # written when a video finished processing
        self.last_speak: dict | None = None
        self._trace: dict = {}
        self._turns_log = self.root / session_id / "dialogue-turns.jsonl"

    # ------------------------------------------------------------------ entry

    def turn(self, command: str | None, text: str | None) -> dict:
        """{handled, speak}; ``handled`` False means: not part of the question round (the guide may answer).

        Every turn is logged locally (``dialogue-turns.jsonl``: no audio, no key) so a failed exchange can be read back."""
        stage = None if self.current is None else f"{self.current['mode']}/{self.current['stage']}"
        self._trace = {}
        began = time.monotonic()
        result = self._turn(command, text)
        self._trace["turn_ms"] = round((time.monotonic() - began) * 1000)
        self._log_turn(command, text, stage, result)
        return result

    def _log_turn(self, command, text, stage, result) -> None:
        record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "stage": stage, "command": command, "transcript": text,
                  **self._trace, "handled": result.get("handled"), "ignored": result.get("ignored"),
                  "reply": (result.get("speak") or {}).get("text")}
        try:
            with open(self._turns_log, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _turn(self, command: str | None, text: str | None) -> dict:
        self.agent.set_persona(settings.load(self.root)["persona"])
        if text and self._is_echo(text):
            return {"handled": True, "speak": None, "ignored": "echo of the agent's own last line"}
        if command == "next_question" and self.current is not None and self.last_speak:
            return {"handled": True, "speak": self.last_speak}  # a bare wake word while a question is open: say it again
        if command == "next_question":
            self._marker.unlink(missing_ok=True)
            return self._said(self._open_next())
        if self.current is None:
            # unresolved questions remain: speech continues the question round, it never falls through to the guide
            self._marker.unlink(missing_ok=True)
            speak = self._open_next(guide_ok=text is not None)
            return {"handled": False} if speak is None else self._said(speak)
        handler = getattr(self, f"_{self.current['mode']}_{self.current['stage']}")
        return self._said(handler(text or ""))

    def _said(self, speak: dict) -> dict:
        self.last_speak = speak
        return {"handled": True, "speak": speak}

    def _is_echo(self, text: str) -> bool:
        """The microphone picked up the agent's own speech: three or more words, all from its last line."""
        if not self.last_speak:
            return False
        said = set(re.findall(r"[a-z0-9']+", text.lower()))
        spoken = set(re.findall(r"[a-z0-9']+", self.last_speak["text"].lower()))
        return len(re.findall(r"[a-z0-9']+", text)) >= 3 and said <= spoken

    def _load_tail_offers(self) -> set[str]:
        if not self._tail_offered_path.exists():
            return set()
        offered = set()
        for line_number, line in enumerate(self._tail_offered_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("schema") != "anastylos-dialogue-tail-offer@1" or not isinstance(record.get("cell"), str):
                raise ValueError(f"Invalid dialogue tail-offer record on line {line_number}")
            offered.add(record["cell"])
        return offered

    def _seed_tail_queue(self) -> None:
        if self._tail_seeded:
            return
        overview = self.api.question_overview(self.root, self.session_id)
        for question in overview.get("questions", []):
            if (question.get("kind") == "source_of_part" and question.get("state") == "LEFT_UNRESOLVED"
                    and question.get("reason") != SURPLUS_REASON):
                self._queue_tail(question.get("cell"), [question.get("id")])
        self._tail_seeded = True

    def _queue_tail(self, cell: str | None, qids: list[str]) -> None:
        qids = [qid for qid in qids if qid]
        if not cell or not qids or cell in self._tail_offered:
            return
        queued = self._tail_queue.setdefault(cell, [])
        queued.extend(qid for qid in qids if qid and qid not in queued)

    @staticmethod
    def _tail_order(cell: str) -> tuple[int, int | str]:
        match = re.search(r"\d+", cell)
        return (0, int(match.group())) if match else (1, cell)

    def _open_tail_cell(self) -> dict | None:
        if not self._tail_queue:
            return None
        cell = min(self._tail_queue, key=self._tail_order)
        qids = self._tail_queue.pop(cell)
        record = {"schema": "anastylos-dialogue-tail-offer@1", "cell": cell, "qids": qids,
                  "wall_time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        with open(self._tail_offered_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._tail_offered.add(cell)

        prompt = None
        for qid in qids:
            reopened = self.api.question_reopen(self.root, self.session_id, qid)
            if prompt is None:
                prompt = reopened
        if prompt is None:
            return None
        group = self.api.question_siblings(self.root, self.session_id, prompt["question_id"])
        event = f"cell-{prompt['question_id']}"
        self.current = {"mode": "cell", "stage": "ask", "qids": group["qids"], "cell": group["cell"],
                        "event": event, "label": where_words(group["cell"]), "items": [], "asked": prompt["text"]}
        return _speak(prompt["text"], "question", prompt.get("op_key"))

    # ------------------------------------------------------------------ opening a cell

    def _advance(self, line: str) -> dict:
        """The same turn goes on with the next cell (or the closing questions) after a record or a deferral."""
        following = self._open_next()
        if following["kind"] == "status":
            return _speak(f"{line} {following['text']}", "status")
        return _speak(f"{line} {following['text']}", following["kind"], following["basis_ref"])

    def _open_next(self, guide_ok: bool = False) -> dict | None:
        self._seed_tail_queue()
        result = self.api.question_next(self.root, self.session_id)
        for _ in range(3):  # a read-back left open by an older flow is closed with "no": nothing is written
            if result.get("stage") != "confirm":
                break
            self.api.question_confirm(self.root, self.session_id, result["question_id"], "no")
            result = self.api.question_next(self.root, self.session_id)
        if result.get("done"):
            tail = self._open_tail_cell()
            if tail is not None:
                return tail
            if not self._closing_marker.exists():
                return self._closing_assemblies_ask()
            self.current = None
            return None if guide_ok else _speak(ALL_DONE, "status")
        qid = result["question_id"]
        group = self.api.question_siblings(self.root, self.session_id, qid)
        event = f"cell-{qid}"
        self.current = {"mode": "cell", "stage": "ask", "qids": group["qids"], "cell": group["cell"], "event": event,
                        "label": where_words(group["cell"]), "items": []}
        self.current["asked"] = self._question()
        return _speak(self.current["asked"], "question", result.get("op_key"))

    def _question(self, again: bool = False) -> str:
        cur = self.current
        return self.agent.say("cell_again" if again else "cell_open", cur["event"], {"where": cur["label"]})

    # ------------------------------------------------------------------ understanding

    def _understand(self, text: str) -> dict:
        """{kind, items, source}: yes/no/skip/unsure/empty/meta/incomplete by rule, else model first, rules on failure."""
        plain = cell_items.classify_plain(text)
        if plain not in (None, "unclear"):
            return {"kind": plain, "items": [], "source": "rule"}
        if plain == "unclear":
            return {"kind": "unclear", "items": [], "source": "rule"}
        proposed = self.agent.extract_items(text, self.current.get("label", ""), tag=self.current.get("event", ""))
        if proposed is not None:
            if proposed["kind"] != "answer":
                return {"kind": proposed["kind"], "items": [], "source": "model"}
            checked = cell_items.check_items(proposed["items"], text)
            return {"kind": "answer" if checked else "unclear", "items": checked, "source": "model"}
        checked = cell_items.rule_items(text)
        return {"kind": "answer" if checked else "unclear", "items": checked, "source": "rule"}

    def _cell_ask(self, text: str) -> dict:
        cur = self.current
        heard = self._understand(text)
        self._trace.update(parser=heard["kind"], source=heard["source"], items=heard["items"])
        kind = heard["kind"]
        if kind == "empty":
            return self._leave_cell(text, "operator: the cell is empty", self.agent.say("ack", cur["event"]), open_item=False)
        if kind in ("unsure", "skip"):
            line = self.agent.defer(cur["event"], text, f"operator {kind}")["speak"]
            return self._leave_cell(text, f"operator {kind}", line, open_item=False)
        if kind == "meta":
            return _speak(cur["asked"], "question")  # the same words again; asking back costs no re-ask
        if kind == "incomplete":
            return _speak(self.agent.say("go_on", cur["event"]), "question")
        if kind != "answer":  # unclear, or a bare yes/no that answers nothing
            again = self.agent.misunderstood(cur["event"], text)
            if again["closed"]:
                return self._leave_cell(text, "answer not understood twice", again["speak"], open_item=False)
            return _speak(again["speak"], "status")
        return self._take_items(heard["items"])

    def _take_items(self, found: list[dict]) -> dict:
        cur = self.current
        cur["items"] = found
        self.agent.reset_reasks(cur["event"])
        if cell_items.total_count(found) is None:
            cur["stage"] = "count"
            return _speak(self.agent.say("cell_how_many", cur["event"]), "question")
        if any(item["origin"] is None for item in found):
            if self._last_origin:
                cur["stage"] = "origin_confirm"
                cur["origin_proposal"] = self._last_origin
                proposed_items = [{**item, "origin": item["origin"] or self._last_origin} for item in found]
                read_back = cell_items.sentence(cur["label"].capitalize(), proposed_items).removesuffix(" Right?")
                cur["origin_proposal_prompt"] = f"{read_back}. Same origin as last time; is that right?"
                return _speak(cur["origin_proposal_prompt"], "read_back")
            cur["stage"] = "origin"
            return _speak(self.agent.say("cell_origin", cur["event"]), "question")
        return self._read_back()

    def _cell_count(self, text: str) -> dict:
        cur = self.current
        plain = cell_items.classify_plain(text)
        number = bundle.parse_count(text)
        self._trace.update(parser=plain or ("count" if number else "unclear"), source="rule", count=number)
        if plain == "meta":
            return _speak(self.agent.say("cell_how_many", cur["event"]), "question")
        if plain in ("unsure", "skip"):
            line = self.agent.defer(cur["event"], text, f"operator {plain}")["speak"]
            return self._leave_cell(text, f"operator {plain}", line, open_item=False)
        if not number:
            again = self.agent.misunderstood(cur["event"], text)
            if again["closed"]:
                return self._leave_cell(text, "count not understood twice", again["speak"], open_item=False)
            return _speak(again["speak"], "status")
        for item in cur["items"]:
            if item["count"] is None:
                item["count"] = number
                break
        return self._take_items(cur["items"])

    def _cell_origin(self, text: str) -> dict:
        cur = self.current
        plain = cell_items.classify_plain(text)
        parsed = bundle.parse_origin(text)
        if parsed["kind"] == "origin":
            origin = cell_items.clean_origin(parsed.get("origin"), set(cell_items.words(text)))
            parsed = {"kind": "origin", "origin": origin} if origin else {"kind": "unclear"}
        self._trace.update(parser=plain or parsed["kind"], source="rule")
        if plain == "meta":
            return _speak(self.agent.say("cell_origin", cur["event"]), "question")
        if plain == "incomplete":
            return _speak(self.agent.say("go_on", cur["event"]), "question")
        if parsed["kind"] == "unclear" and plain is None:
            fields, source, dropped = self.agent.extract(text, "origin", tag=cur["event"])
            origin = cell_items.clean_origin(fields["origin"], set(cell_items.words(text)))
            if source == "muse" and fields["intent"] == "answer" and origin:
                parsed = {"kind": "origin", "origin": origin}
                self._trace.update(source="model", dropped=dropped)
        if parsed["kind"] in ("unsure", "skip") or plain in ("unsure", "skip", "no"):
            origin = cell_items.UNKNOWN  # "don't know" is an answer: recorded as unknown, never asked again
        elif parsed["kind"] == "origin":
            origin = parsed["origin"]
        else:
            again = self.agent.misunderstood(cur["event"], text)
            if not again["closed"]:
                return _speak(again["speak"], "status")
            origin = cell_items.UNKNOWN
        for item in cur["items"]:
            item["origin"] = item["origin"] or origin
        return self._read_back()

    def _cell_origin_confirm(self, text: str) -> dict:
        cur = self.current
        proposal = cur["origin_proposal"]
        plain = cell_items.classify_plain(text)
        confirmation = cell_items.classify_confirmation(text)
        self._trace.update(parser=confirmation or plain or "unclear", source="rule")
        if confirmation == "yes":
            for item in cur["items"]:
                if item["origin"] is None:
                    item["origin"] = proposal
            return self._commit(text)
        if confirmation == "no":
            cur["stage"] = "origin"
            cur.pop("origin_proposal", None)
            cur.pop("origin_proposal_prompt", None)
            self.agent.reset_reasks(cur["event"])
            return _speak(self.agent.say("cell_origin", cur["event"]), "question")
        if plain == "meta":
            return _speak(cur["origin_proposal_prompt"], "question")

        corrected = _LEADING_NO.sub("", text, count=1).strip()
        parsed = bundle.parse_origin(corrected)
        origin = cell_items.clean_origin(parsed.get("origin"), set(cell_items.words(corrected)))
        if parsed["kind"] == "origin" and origin:
            for item in cur["items"]:
                if item["origin"] is None:
                    item["origin"] = origin
            return self._read_back()
        if parsed["kind"] in ("unsure", "skip"):
            for item in cur["items"]:
                if item["origin"] is None:
                    item["origin"] = cell_items.UNKNOWN
            return self._read_back()
        again = self.agent.misunderstood(cur["event"], text)
        if again["closed"]:
            return self._leave_cell(text, "proposed origin not understood twice", again["speak"], open_item=False)
        return _speak(again["speak"], "status")

    def _read_back(self) -> dict:
        cur = self.current
        cur["stage"] = "confirm"
        self.agent.reset_reasks(cur["event"])
        return _speak(cell_items.sentence(cur["label"].capitalize(), cur["items"]), "read_back")

    def _cell_confirm(self, text: str) -> dict:
        cur = self.current
        plain = cell_items.classify_plain(text)
        confirmation = cell_items.classify_confirmation(text)
        self._trace.update(parser=confirmation or plain, source="rule")
        if confirmation == "yes":
            return self._commit(text)
        if plain == "meta":
            return _speak(cell_items.sentence(cur["label"].capitalize(), cur["items"]), "read_back")
        if plain in ("unsure", "skip"):
            line = self.agent.defer(cur["event"], text, f"operator {plain}")["speak"]
            return self._leave_cell(text, f"operator {plain}", line, open_item=False)
        corrected = _LEADING_NO.sub("", text, count=1).strip()
        if confirmation == "no" or not corrected:
            cur.update(stage="ask", items=[])
            cur["asked"] = self._question(again=True)
            return _speak(cur["asked"], "question")
        heard = self._understand(corrected)  # a correction in the same sentence is taken directly
        self._trace.update(parser=heard["kind"], source=heard["source"], items=heard["items"], correction=True)
        if heard["kind"] == "answer":
            return self._take_items(heard["items"])
        if heard["kind"] in ("incomplete",):
            return _speak(self.agent.say("go_on", cur["event"]), "question")
        again = self.agent.misunderstood(cur["event"], text)
        if again["closed"]:
            return self._leave_cell(text, "correction not understood twice", again["speak"], open_item=False)
        return _speak(again["speak"], "status")

    # ------------------------------------------------------------------ writing

    def _commit(self, text: str) -> dict:
        """After "yes": map the parts onto the detected questions. Fewer parts leave the surplus unresolved, more go
        through ``write_operator_notes``. Every detected question passes the coordinator's answer / confirm path."""
        cur = self.current
        parts = [(item["name"], item["origin"] or cell_items.UNKNOWN) for item in cur["items"] for _ in range(item["count"])]
        qids, failed = cur["qids"], []
        for qid, (name, origin) in zip(qids, parts):
            self.api.question_open(self.root, self.session_id, qid)
            reply = self.api.question_answer(self.root, self.session_id, qid, f"{name} from {origin}", None)
            done = self.api.question_confirm(self.root, self.session_id, qid, "yes") if reply.get("status") == "read_back" else {}
            if done.get("status") not in ("committed", "already_committed"):
                failed.append(qid)
        deferred_qids = qids[len(parts):] + [qid for qid in failed if qid in qids]
        for qid in deferred_qids:
            self.api.question_defer(self.root, self.session_id, qid, "dialogue: left unresolved after the cell was described")
        extra = parts[len(qids):]
        if extra and not failed:
            cell = f"cell-{str(cur['cell']).removeprefix('cell-')}"
            said = {"described": [[n, o] for n, o in parts], "detected_questions": len(qids)}
            if not self._write_note("cell_extra", [name for name, _ in extra], cell, said,
                                    origins=[origin for _, origin in extra], extra_key=str(len(parts))):
                failed.append("notes")
        if not failed:
            origins = {item["origin"] for item in cur["items"]}
            self._last_origin = next(iter(origins)) if len(origins) == 1 and None not in origins \
                and cell_items.UNKNOWN not in origins else None
        self.current = None
        if failed:
            return self._advance(self.agent.defer(cur["event"], text, "a part was not committed")["speak"])
        return self._advance(self.agent.recorded_line(cur["event"]))

    def _leave_cell(self, text: str, reason: str, line: str, open_item: bool = True) -> dict:
        """Defer this cell for one tail retry; unresolved questions remain available to the desktop."""
        for qid in self.current["qids"]:
            self.api.question_defer(self.root, self.session_id, qid, f"dialogue: {reason}")
        self._queue_tail(self.current["cell"], self.current["qids"])
        self.current = None
        return self._advance(line)

    # ------------------------------------------------------------------ end of the video's dialogue
    # What is said here has no coordinator question kind (the frozen answer plan knows only parts). It goes through
    # a read-back + "yes"; only then the coordinator's writer records each named part as operator-stated
    # (``declared_removal`` + ``storage_link``, stable op key, write-ahead, recovery). ``dialogue-notes.jsonl`` keeps the audit line.

    def _closing_assemblies_ask(self) -> dict:
        self.current = {"mode": "closing", "stage": "assemblies", "event": "closing-assemblies", "label": "the work area", "items": []}
        return _speak(self.agent.say("assemblies_left", "closing-assemblies"), "question")

    def _closing_missed_ask(self, lead: str = "") -> dict:
        self.current = {"mode": "closing", "stage": "missed", "event": "closing-missed", "label": "the sheet", "items": []}
        line = self.agent.say("missed_anything", "closing-missed")
        return _speak(f"{lead} {line}".strip(), "question")

    def _closing_finish(self, lead: str = "") -> dict:
        self._closing_marker.write_text("done", encoding="utf-8")
        self.current = None
        return _speak(f"{lead} {ALL_DONE}".strip(), "status")

    def _write_note(self, kind: str, labels: list[str], location: str, said: dict,
                    origins: list[str | None] | None = None, extra_key: str = "") -> bool:
        origins = [None] * len(labels) if origins is None else [
            None if origin == cell_items.UNKNOWN else origin for origin in origins]
        if len(origins) != len(labels):
            raise ValueError("Every operator-stated part label needs one origin")
        serialized = json.dumps(said, ensure_ascii=False)
        key = hashlib.sha256(json.dumps([kind, labels, location, origins, serialized, extra_key],
                                        ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
        reply = self.api.closing_write(self.root, self.session_id, f"CLOSE-{key}", labels, location,
                                       serialized, origins=origins)
        if reply.get("status") != "committed":
            return False
        self._note(kind, said)
        return True

    def _note(self, kind: str, said: dict) -> None:
        record = {"schema": "anastylos-dialogue-note@1", "kind": kind, "operator_said": said, "confirmed_by_operator": True,
                  "memory_write": True, "wall_time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        with open(self._notes, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _closing_unclear(self, text: str, on_defer) -> dict:
        again = self.agent.misunderstood(self.current["event"], text)
        return on_defer(again["speak"]) if again["closed"] else _speak(again["speak"], "status")

    def _closing_assemblies(self, text: str) -> dict:
        heard = self._understand(text)
        self._trace.update(parser=heard["kind"], source=heard["source"], items=heard["items"])
        kind = heard["kind"]
        if kind in ("no", "skip", "empty"):
            return self._closing_missed_ask()
        if kind == "unsure":
            return self._closing_missed_ask(self.agent.defer("closing-assemblies", text, "operator unsure")["speak"])
        if kind == "meta":
            return _speak(self.agent.say("assemblies_left", "closing-assemblies"), "question")
        if kind == "incomplete":
            return _speak(self.agent.say("go_on", "closing-assemblies"), "question")
        if kind != "answer":
            return self._closing_unclear(text, lambda line: self._closing_missed_ask(line))
        found = heard["items"]
        self.current.update(stage="assemblies_confirm", items=found, said=text.strip())
        self.agent.reset_reasks("closing-assemblies")
        names = ", ".join(i["name"] for i in found)
        return _speak(f"Still in the work area: {names}. Right?", "read_back")

    def _closing_assemblies_confirm(self, text: str) -> dict:
        plain = cell_items.classify_plain(text)
        confirmation = cell_items.classify_confirmation(text)
        if confirmation == "yes":
            labels = [i["name"] for i in self.current["items"]]
            if not self._write_note("assemblies_left", labels, "work area", {"text": self.current["said"], "items": labels}):
                return _speak(UNAVAILABLE, "status")
            return self._closing_missed_ask(self.agent.recorded_line("closing-assemblies"))
        if confirmation == "no":
            self.agent.reset_reasks("closing-assemblies")
            return self._closing_assemblies_ask()
        return self._closing_unclear(text, lambda line: self._closing_missed_ask(line))

    _CELL_WORDS = {w: i for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split(), 1)}
    _CELL = re.compile(r"\bcell\s+(\d{1,3}|" + "|".join(_CELL_WORDS) + r")\b", re.I)
    _MISSED_NO = re.compile(r"^(?:no|nope|not really|(?:now )?i (?:do not|don't|dont) think so|(?:no )?that'?s all|nothing(?: (?:else|more))?|none)$", re.I)

    @classmethod
    def _is_missed_negative(cls, text: str) -> bool:
        plain = (text or "").lower().replace("’", "'")
        plain = re.sub(r"[,.;:!?]+", " ", plain)
        return bool(cls._MISSED_NO.fullmatch(" ".join(plain.split())))

    def _closing_missed(self, text: str) -> dict:
        plain = cell_items.classify_plain(text)
        if self._is_missed_negative(text) or plain in ("skip", "empty"):
            return self._closing_finish()
        if cell_items.classify_confirmation(text) == "yes":
            self.current["stage"] = "missed_detail"
            self.agent.reset_reasks("closing-missed")
            return _speak(self.agent.say("missed_cell", "closing-missed"), "question")
        if plain == "unsure":
            return self._closing_finish(self.agent.defer("closing-missed", text, "operator unsure")["speak"])
        if plain == "meta":
            return _speak(self.agent.say("missed_anything", "closing-missed"), "question")
        if self._CELL.search(text):
            self.current["stage"] = "missed_detail"
            return self._closing_missed_detail(text)
        return self._closing_unclear(text, self._closing_finish)

    def _closing_missed_detail(self, text: str) -> dict:
        if self._is_missed_negative(text) or cell_items.classify_plain(text) in ("empty", "skip"):
            return self._closing_finish()
        if cell_items.classify_confirmation(text) == "yes" or cell_items.classify_plain(text) == "meta":
            return _speak(self.agent.say("missed_cell", "closing-missed"), "question")
        match = self._CELL.search(text)
        found = []
        if match:
            rest = " ".join((text[:match.start()] + " " + text[match.end():]).strip(" ,.;").split())
            heard = self._understand(rest) if rest.strip() else {"kind": "unclear", "items": [], "source": "rule"}
            found = heard["items"] if heard["kind"] == "answer" else []
            self._trace.update(parser=heard["kind"], source=heard["source"], items=found)
        if not match or len(found) != 1:
            return self._closing_unclear(text, self._closing_finish)
        token = match.group(1).lower()
        cell = int(token) if token.isdigit() else self._CELL_WORDS[token]
        self.current.update(stage="missed_detail", cell=cell, items=found, said=text.strip())
        self.agent.reset_reasks("closing-missed")
        return self._closing_missed_next()

    def _closing_missed_next(self) -> dict:
        item = self.current["items"][0]
        if item["count"] is None:
            self.current["stage"] = "missed_count"
            self.agent.reset_reasks("closing-missed")
            return _speak(self.agent.say("cell_how_many", "closing-missed"), "question")
        if item["origin"] is None:
            self.current["stage"] = "missed_origin"
            self.agent.reset_reasks("closing-missed")
            return _speak(self.agent.say("cell_origin", "closing-missed"), "question")
        self.current["stage"] = "missed_confirm"
        sentence = cell_items.sentence(f"Missed in cell {self.current['cell']}", [item])
        self.current["read_back"] = sentence
        self.agent.reset_reasks("closing-missed")
        return _speak(sentence, "read_back")

    def _closing_missed_count(self, text: str) -> dict:
        plain = cell_items.classify_plain(text)
        count = bundle.parse_count(text)
        self._trace.update(parser=plain or ("count" if count else "unclear"), source="rule", count=count)
        if plain == "meta":
            return _speak(self.agent.say("cell_how_many", "closing-missed"), "question")
        if plain in ("unsure", "skip", "no"):
            line = self.agent.defer("closing-missed", text, "operator count unknown")["speak"]
            return self._closing_finish(line)
        if not count:
            again = self.agent.misunderstood("closing-missed", text)
            if again["closed"]:
                return self._closing_finish(again["speak"])
            return _speak(again["speak"], "status")
        self.current["items"][0]["count"] = count
        return self._closing_missed_next()

    def _closing_missed_origin(self, text: str) -> dict:
        plain = cell_items.classify_plain(text)
        parsed = bundle.parse_origin(text)
        if parsed["kind"] == "origin":
            origin = cell_items.clean_origin(parsed.get("origin"), set(cell_items.words(text)))
            parsed = {"kind": "origin", "origin": origin} if origin else {"kind": "unclear"}
        self._trace.update(parser=plain or parsed["kind"], source="rule")
        if plain == "meta":
            return _speak(self.agent.say("cell_origin", "closing-missed"), "question")
        if plain == "incomplete":
            return _speak(self.agent.say("go_on", "closing-missed"), "question")
        if parsed["kind"] in ("unsure", "skip") or plain == "no":
            origin = cell_items.UNKNOWN
        elif parsed["kind"] == "origin":
            origin = parsed["origin"]
        else:
            again = self.agent.misunderstood("closing-missed", text)
            if again["closed"]:
                return self._closing_finish(again["speak"])
            return _speak(again["speak"], "status")
        self.current["items"][0]["origin"] = origin
        return self._closing_missed_next()

    def _closing_missed_confirm(self, text: str) -> dict:
        plain = cell_items.classify_plain(text)
        confirmation = cell_items.classify_confirmation(text)
        if confirmation == "yes":
            item = self.current["items"][0]
            count = item["count"]
            labels = [item["name"]] * count
            origin = item["origin"] if item["origin"] != cell_items.UNKNOWN else None
            origins = [origin] * count
            location = f"cell-{self.current['cell']}"
            said = {"cell": self.current["cell"], "part_name": item["name"], "count": count,
                    "origin": origin, "location": location, "operator_text": self.current["said"],
                    "read_back": self.current["read_back"]}
            if not self._write_note("missed_part", labels, location, said, origins=origins):
                return _speak(UNAVAILABLE, "status")
            return self._closing_finish(self.agent.recorded_line("closing-missed"))
        if confirmation == "no":
            self.current["stage"] = "missed_detail"
            self.agent.reset_reasks("closing-missed")
            return _speak(self.agent.say("missed_cell", "closing-missed"), "question")
        return self._closing_unclear(text, self._closing_finish)
