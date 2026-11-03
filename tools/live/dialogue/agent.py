"""The dialogue agent: speaks catalog lines, understands answers, enforces the one-re-ask rule.

It never writes memory. Observed facts (event type, cell) and what the operator said (part name, origin,
storage) stay in separate dicts. An understood answer is handed back as ``forward`` text for the coordinator,
whose read-back plus "yes" remains the only way to write.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from catalog.selector import Recents, render, where_words

from .executor import Executor

OPEN_ITEM_SCHEMA = "anastylos-dialogue-open-item@1"
EVENT_TYPES = ("part_removed", "origin_unseen", "part_stored", "need_photo")
QUIET_S = 8.0  # a question asked this recently makes "ask nothing now" a real option for Muse
MAX_REASKS = 1
FLAVOURED = frozenset({"recorded", "cell_open", "cell_again", "go_on", "cell_how_many", "cell_origin", "ack"})


class DialogueAgent:
    def __init__(self, persona: str, executor: Executor, *, open_items_path: Path | None = None, clock=time.monotonic):
        self._persona, self._executor, self._clock = persona, executor, clock
        self._open_items = open_items_path
        self._recents = Recents()
        self._events: dict[str, dict] = {}
        self._last_question_at: float | None = None
        self._flavoured_lines = 0

    # ------------------------------------------------------------------ speaking

    def _say(self, kind: str, event_id: str, slots: dict | None = None) -> tuple[str, int]:
        persona = self._persona
        if persona != "serious" and kind in FLAVOURED:
            self._flavoured_lines += 1
            if self._flavoured_lines % 2 == 1:  # persona colour on every other line at most; the rest are plain
                persona = "serious"
        text, index = render(kind, persona, event_id, slots or {}, self._recents.recent(kind, persona))
        self._recents.note(kind, persona, index)
        return text, index

    def _event(self, event_id: str) -> dict:
        return self._events.setdefault(event_id, {"status": "new", "reasks": 0, "type": None, "cell": None})

    def next_question(self, event: dict) -> dict:
        """Catalog question for a verified event ``{id, type, cell?}``; Muse is asked only when there is a real choice."""
        state = self._event(event["id"])
        state.update(type=event["type"], cell=event.get("cell"))
        if state["status"] in ("deferred", "done") or event["type"] not in EVENT_TYPES:
            return {"speak": None, "type": "none", "source": "rule"}
        allowed = [event["type"]]
        recent = self._last_question_at is not None and self._clock() - self._last_question_at < QUIET_S
        picked, source = event["type"], "rule"
        if len(allowed) > 1 or recent:
            picked, source = self._executor.choose({"event_type": event["type"], "recent_question": recent},
                                                   allowed, tag=event["id"])
        if picked == "none":
            return {"speak": None, "type": "none", "source": source}
        slots = {"where": where_words(event.get("cell"))}
        if picked == "part_stored":
            slots["name"] = event.get("name") or "the part"
        text, index = self._say(picked, event["id"], slots)
        state["status"] = "awaiting"
        self._last_question_at = self._clock()
        return {"speak": text, "type": picked, "variant": index, "source": source,
                "observed": {"event_type": event["type"], "cell": event.get("cell")}}

    def read_back_line(self, event_id: str, core: str, lead: bool = False) -> str:
        """The coordinator's own read-back (``core``, unchanged). A persona lead-in only when asked for: follow-up
        turns stay short."""
        return self._say("read_back", event_id, {"core": core})[0] if lead else core

    def recorded_line(self, event_id: str) -> str:
        self._event(event_id)["status"] = "done"
        return self._say("recorded", event_id)[0]

    # ------------------------------------------------------------------ hearing

    def on_answer(self, event_id: str, text: str, marks: dict | None = None, ack=None) -> dict:
        """Handle one transcribed answer. ``marks`` carries ``speech_end`` and ``transcript`` timestamps (clock units).

        ``ack`` is called at once with a short acknowledgement before any model call. Result keys: ``speak`` (the
        reply, or None), ``forward`` (text for the coordinator, or None), ``operator_said``, ``observed``,
        ``memory_write`` (always False here), ``closed``, ``timing``.
        """
        arrived = self._clock()
        marks = marks or {}
        state = self._event(event_id)
        base = {"memory_write": False, "forward": None, "operator_said": None, "speak": None,
                "observed": {"event_type": state["type"], "cell": state["cell"]}, "closed": False}
        if state["status"] in ("deferred", "done"):
            return {**base, "closed": True, "timing": self._timing(marks, arrived, arrived, None, arrived)}
        if ack is not None:
            ack(self._say("ack", event_id)[0])
        acked = self._clock()
        fields, source, dropped = self._executor.extract(text or "", state["type"] or "", tag=event_id)
        muse_done = self._clock()
        intent = fields["intent"]
        said = {k: fields[k] for k in ("part_name", "origin", "storage")}
        if intent in ("answer", "yes", "no"):
            result = {**base, "forward": text.strip(), "operator_said": said, "intent": intent}
        elif intent in ("unsure", "skip"):
            result = {**base, **self._defer(event_id, state, text, f"operator {intent}")}
        else:
            result = {**base, **self.misunderstood(event_id, text)}
        result.update(source=source, dropped=dropped,
                      timing=self._timing(marks, arrived, acked, muse_done, self._clock()))
        return result

    def misunderstood(self, event_id: str, text: str) -> dict:
        """First time: the one re-ask. Second time: the defer line, an open item, and the event is closed."""
        state = self._event(event_id)
        if state["reasks"] < MAX_REASKS:
            state["reasks"] += 1
            return {"speak": self._say("unclear_answer", event_id)[0], "type": "unclear_answer", "closed": False}
        return self._defer(event_id, state, text, "answer not understood twice")

    def defer(self, event_id: str, text: str, reason: str) -> dict:
        return self._defer(event_id, self._event(event_id), text, reason)

    def reset_reasks(self, event_id: str) -> None:
        """A new stage of the same event (e.g. the read-back after the answer) gets its own single re-ask."""
        self._event(event_id)["reasks"] = 0

    def say(self, kind: str, event_id: str, slots: dict | None = None) -> str:
        return self._say(kind, event_id, slots)[0]

    def extract(self, text: str, question_type: str, tag: str = "", need_name: bool = False) -> tuple[dict, str, list[str]]:
        """Fields from a free answer: rules first, the model only where the rules cannot tell (see Executor)."""
        return self._executor.extract(text, question_type, tag, need_name)

    def extract_origin(self, text: str, tag: str = "") -> dict | None:
        return self._executor.extract_origin(text, tag)

    def extract_items(self, text: str, cell: str, tag: str = "") -> dict | None:
        return self._executor.extract_items(text, cell, tag)

    def set_persona(self, persona: str) -> None:
        self._persona = persona

    def _defer(self, event_id: str, state: dict, text: str, reason: str) -> dict:
        state["status"] = "deferred"
        self._append_open_item({"schema": OPEN_ITEM_SCHEMA, "event_id": event_id, "event_type": state["type"],
                                "observed": {"cell": state["cell"]}, "reason": reason,
                                "operator_text_sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
                                "wall_time": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        return {"speak": self._say("defer_to_desktop", event_id)[0], "type": "defer_to_desktop", "closed": True}

    def _append_open_item(self, item: dict) -> None:
        if self._open_items is None:
            return
        self._open_items.parent.mkdir(parents=True, exist_ok=True)
        with open(self._open_items, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _timing(marks: dict, arrived: float, acked: float, muse_done: float | None, replied: float) -> dict:
        def ms(a, b):
            return None if a is None or b is None else round((b - a) * 1000)
        return {"speech_end_to_transcript_ms": ms(marks.get("speech_end"), marks.get("transcript")),
                "transcript_to_ack_ms": ms(marks.get("transcript", arrived), acked),
                "muse_ms": ms(acked, muse_done),
                "speech_end_to_reply_ms": ms(marks.get("speech_end", arrived), replied)}
