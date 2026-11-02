"""Spoken question round of one session: the dialogue agent in front of the coordinator (via ``adapter``).

One turn = one operator utterance or command. The flow decides what to say; the coordinator still does every
read-back and every write (only "yes" to its read-back commits). Spoken text is a catalog line plus coordinator or
verified text, never free model text. Parts that share a cell are bundled into one question; each part stays its own
coordinator question and is committed individually after the combined read-back.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "live"))

from catalog.selector import where_words  # noqa: E402
from dialogue import bundle, settings  # noqa: E402
from dialogue.agent import DialogueAgent  # noqa: E402
from dialogue.executor import Executor, extract_by_rule  # noqa: E402

ALL_DONE = "There are no more questions."
SCREEN_HINT = " Please look at the screen for the options."
UNAVAILABLE = "The workstation could not handle that. Please use the screen."
_FLOWS: dict[tuple[str, str], "DialogueFlow"] = {}
_GUARD = threading.Lock()


def flow_for(root: Path, session_id: str, api) -> "DialogueFlow":
    with _GUARD:
        key = (str(root), session_id)
        if key not in _FLOWS:
            _FLOWS[key] = DialogueFlow(root, session_id, api)
        return _FLOWS[key]


def _speak(text: str, kind: str, basis_ref: str | None = None) -> dict:
    return {"text": text, "kind": kind, "basis_ref": basis_ref}


class DialogueFlow:
    def __init__(self, root: Path, session_id: str, api):
        self.root, self.session_id, self.api = Path(root), session_id, api
        self.agent = DialogueAgent(settings.load(self.root)["persona"], Executor(None),
                                   open_items_path=self.root / session_id / "dialogue-open-items.jsonl")
        self.current: dict | None = None
        self.unbundled: set[str] = set()
        self._marker = self.root / session_id / "dialogue-pending-open"  # written when a video finished processing

    # ------------------------------------------------------------------ entry

    def turn(self, command: str | None, text: str | None) -> dict:
        """{handled, speak}; ``handled`` False means: not part of the question round (the guide may answer)."""
        self.agent.set_persona(settings.load(self.root)["persona"])
        if command == "next_question" or (self.current is None and self._marker.exists() and text is not None):
            self._marker.unlink(missing_ok=True)
            return {"handled": True, "speak": self._open_next()}
        if self.current is None:
            return {"handled": False}
        handler = getattr(self, f"_{self.current['mode']}_{self.current['stage']}")
        return {"handled": True, "speak": handler(text or "")}

    # ------------------------------------------------------------------ opening a question

    def _open_next(self) -> dict:
        result = self.api.question_next(self.root, self.session_id)
        if result.get("done"):
            self.current = None
            return _speak(ALL_DONE, "status")
        qid = result["question_id"]
        if result.get("stage") == "confirm":
            self.current = {"mode": "single", "stage": "confirm", "qid": qid}
            return _speak(self.agent.read_back_line(qid, result["read_back"]), "read_back", result.get("op_key"))
        group = self.api.question_siblings(self.root, self.session_id, qid)
        qids = [q for q in group["qids"] if q == qid or q not in self.unbundled]
        if len(qids) >= 2 and qid not in self.unbundled:
            event = f"bundle-{qids[0]}"
            self.current = {"mode": "bundle", "stage": "label", "qids": qids, "cell": group["cell"], "event": event,
                            "list_mode": False}
            return _speak(self.agent.say("bundle_same", event, {"where": where_words(group["cell"])}), "question",
                          result.get("op_key"))
        self.current = {"mode": "single", "stage": "answer", "qid": qid}
        lead = self.agent.next_question({"id": qid, "type": "part_removed", "cell": group["cell"]})["speak"]
        text = f"{lead} {result['text']}" if lead else result["text"]
        if result.get("screen_options"):
            text += SCREEN_HINT
        return _speak(text, "question", result.get("op_key"))

    # ------------------------------------------------------------------ one part

    def _single_answer(self, text: str) -> dict:
        qid = self.current["qid"]
        heard = self.agent.on_answer(qid, text)
        if heard["closed"]:
            return self._left_for_desktop([qid], heard["speak"])
        if heard["speak"]:
            return _speak(heard["speak"], "status")
        reply = self.api.question_answer(self.root, self.session_id, qid, heard["forward"], None)
        if reply.get("status") == "read_back":
            self.current["stage"] = "confirm"
            self.agent.reset_reasks(qid)
            return _speak(self.agent.read_back_line(qid, reply["read_back"]), "read_back", reply.get("op_key"))
        again = self.agent.misunderstood(qid, text)
        if again["closed"]:
            return self._left_for_desktop([qid], again["speak"])
        return _speak(again["speak"], "status")

    def _single_confirm(self, text: str) -> dict:
        qid = self.current["qid"]
        reply = self.api.question_confirm(self.root, self.session_id, qid, text)
        status = reply.get("status")
        if status in ("committed", "already_committed"):
            self.current = None
            return _speak(self.agent.recorded_line(qid), "status", reply.get("op_key"))
        if status == "asked_again":
            self.current = {"mode": "single", "stage": "answer", "qid": qid}
            self.agent.reset_reasks(qid)
            return _speak(reply["text"], "question", reply.get("op_key"))
        again = self.agent.misunderstood(qid, text)
        if again["closed"]:
            return self._left_for_desktop([qid], again["speak"])
        return _speak(again["speak"], "status")

    def _left_for_desktop(self, qids: list[str], line: str) -> dict:
        for qid in qids:
            self.api.question_defer(self.root, self.session_id, qid, "dialogue: left for the desktop")
        self.current = None
        return _speak(line, "status")

    # ------------------------------------------------------------------ several parts in one cell

    def _bundle_label(self, text: str) -> dict:
        cur = self.current
        parsed = bundle.parse(text, len(cur["qids"]), cur["list_mode"])
        kind = parsed["kind"]
        if kind in ("unsure", "skip"):
            return self._defer_bundle(text, f"operator {kind}")
        if kind == "different":
            cur["list_mode"] = True
            return _speak(self.agent.say("bundle_list", cur["event"]), "question")
        if kind == "unclear":
            return self._bundle_unclear(text)
        cur["names"], cur["origins"] = parsed["names"], parsed["origins"]
        if any(origin is None for origin in cur["origins"]):
            cur["stage"] = "origin"
            self.agent.reset_reasks(cur["event"])
            return _speak(self.agent.say("origin_unseen", cur["event"]), "question")
        return self._bundle_submit()

    def _bundle_origin(self, text: str) -> dict:
        cur = self.current
        parsed = bundle.parse_origin(text)
        if parsed["kind"] in ("unsure", "skip"):
            return self._defer_bundle(text, f"operator {parsed['kind']}")
        if parsed["kind"] == "unclear":
            return self._bundle_unclear(text)
        cur["origins"] = [origin or parsed["origin"] for origin in cur["origins"]]
        return self._bundle_submit()

    def _bundle_unclear(self, text: str) -> dict:
        again = self.agent.misunderstood(self.current["event"], text)
        if again["closed"]:
            return self._left_for_desktop(self.current["qids"], again["speak"])
        return _speak(again["speak"], "status")

    def _defer_bundle(self, text: str, reason: str) -> dict:
        line = self.agent.defer(self.current["event"], text, reason)["speak"]
        return self._left_for_desktop(self.current["qids"], line)

    def _bundle_submit(self) -> dict:
        """Hand every part its own answer; each reaches its own read-back. Nothing is written yet."""
        cur = self.current
        replies = []
        for qid, name, origin in zip(cur["qids"], cur["names"], cur["origins"]):
            self.api.question_open(self.root, self.session_id, qid)
            replies.append(self.api.question_answer(self.root, self.session_id, qid, f"{name} from {origin}", None))
        if any(reply.get("status") != "read_back" for reply in replies):
            return self._unbundle(cur["qids"], [r.get("status") == "read_back" for r in replies])
        cur["stage"] = "confirm"
        self.agent.reset_reasks(cur["event"])
        core = bundle.combined_read_back(where_words(cur["cell"]), cur["names"], cur["origins"])
        return _speak(self.agent.read_back_line(cur["event"], core), "read_back", replies[0].get("op_key"))

    def _unbundle(self, qids: list[str], in_read_back: list[bool]) -> dict:
        """The coordinator did not accept a bundled answer: nothing is kept, the parts are asked one by one."""
        for qid, waiting in zip(qids, in_read_back):
            if waiting:
                self.api.question_confirm(self.root, self.session_id, qid, "no")
        self.unbundled.update(qids)
        return self._open_next()

    def _bundle_confirm(self, text: str) -> dict:
        cur = self.current
        intent = extract_by_rule(text)["intent"]
        if intent == "yes":
            done = [self.api.question_confirm(self.root, self.session_id, qid, "yes") for qid in cur["qids"]]
            if all(r.get("status") in ("committed", "already_committed") for r in done):
                self.current = None
                return _speak(self.agent.recorded_line(cur["event"]), "status", done[0].get("op_key"))
            return self._defer_bundle(text, "a part was not committed")
        if intent == "no":
            self.unbundled.update(cur["qids"])
            for qid in cur["qids"]:
                self.api.question_confirm(self.root, self.session_id, qid, "no")
            return self._open_next()
        return self._bundle_unclear(text)
