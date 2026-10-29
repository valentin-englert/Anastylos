"""Synthetic LIVE-01 takes for the scorer tests: journals, memory copy, die and ground truth in a temp dir.

The baseline take: four parts P1–P4 into cells 1–4. Slots 1 and 3 are asked live (L0, L1), slots 2 and 4 in the
offline round 600 s after the take. Live answers are both correct; offline P2 is correct and P4 wrong.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL))
import score_live01  # noqa: E402

T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
GOOD_TEXT = "Beim Öffnen des Gehäuses: Woher kam das Teil, das du als Erstes in Zelle {cell} gelegt hast? (abgelegt bei etwa 0:12)"
BAD_TEXT = "Q-P1-01: Quelle von cell-1 bei 12.3 s, Punkt 182/140 mm?"
DELAY_S = 10.0
OFFLINE_DELAY_S = 600.0
STEPS = [
    {"part": "P1", "source_point_mm": [100, 100], "removal_t_s": 10.0, "deposit_t_s": 12.0, "destination": "cell-1"},
    {"part": "P2", "source_point_mm": [150, 100], "removal_t_s": 30.0, "deposit_t_s": 32.0, "destination": "cell-2"},
    {"part": "P3", "source_point_mm": [200, 100], "removal_t_s": 50.0, "deposit_t_s": 52.0, "destination": "cell-3"},
    {"part": "P4", "source_point_mm": [250, 100], "removal_t_s": 70.0, "deposit_t_s": 72.0, "destination": "cell-4"},
]


def ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def box_around(point, half=5):
    return [point[0] - half, point[1] - half, 2 * half, 2 * half]


class Take:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.journals = {"live": [], "offline": []}
        self.memory: list[dict] = []
        self.die = {"schema": "live01-die@1", "seed_sha256": "0" * 64,
                    "slots": {"1": "live", "2": "offline", "3": "live", "4": "offline", "5": "live", "6": "offline"},
                    "live_questions": {"1": "L0", "2": "L1", "3": "L0", "4": "L1"},
                    "delivery_delay_s": DELAY_S, "delay_basis_sha256": None, "offline_delay_s": OFFLINE_DELAY_S}
        self.actual = {"schema": "live01-actual@1", "object": "synthetic", "steps": [dict(s) for s in STEPS]}
        self.die_sha_override = None
        self._ids = 0

    def rec(self, round_: str, kind: str, t: float, **fields) -> dict:
        record = {"schema": "anastylos-live-journal@1", "kind": kind, "t_wall": ts(t), **fields}
        self.journals[round_].append(record)
        return record

    def start(self, round_: str, t: float):
        self.rec(round_, "session_start", t, round=round_, live_session_id="LS1", die_sha256=None,
                 delivery_delay_s=DELAY_S)

    def done(self, round_: str, t: float):
        self.rec(round_, "done", t)

    def declaration(self) -> list[str]:
        self._ids += 1
        dec, link = f"dec-{self._ids:04d}", f"sto-{self._ids:04d}"
        self.memory += [{"id": dec, "type": "declared_removal", "declared_by": "owner", "operator_label": "x"},
                        {"id": link, "type": "storage_link", "target_id": dec, "location": "cell",
                         "basis": {"kind": "human", "confirmed_by": "owner"}}]
        return [dec, link]

    def ask(self, round_, qid, slot, cell, deposit_s, asked, answered, confirmed, resolved, wording="L0",
            kind="source_of_part", text=None, revision=1, read_back=True, commit=True, intent=True, written=None,
            extra=None):
        """One question episode in journal order: OPEN, ASKED, ANSWERED, READ_BACK, CONFIRMED, intent, memory_result."""
        op_key = f"LS1:{qid}:{revision}"
        statics = {"slot": slot, "question_kind": kind, "deposit_t_wall": ts(deposit_s)}
        if kind == "source_of_part":
            statics |= {"cell": cell, "deposit_window_s": [deposit_s - 1, deposit_s + 1]}
        statics |= extra or {}
        q = lambda state, t, **f: self.rec(round_, "question_state", t, op_key=op_key, question_id=qid,  # noqa: E731
                                           revision=revision, state=state, **f)
        q("OPEN", deposit_s, **statics)
        q("ASKED", asked, wording=wording, text=text or GOOD_TEXT.format(cell=str(cell).split("-")[-1]), anchor_possible=False, anchor_ref=None)
        q("ANSWERED", answered, input={"schema": "anastylos-live-operator-input@1", "source": "screen_choice"})
        if read_back:
            q("READ_BACK", answered + 0.5)
        q("CONFIRMED", confirmed, resolved=resolved)
        if commit:
            if intent:
                self.rec(round_, "intent", confirmed + 0.1, op_key=op_key, planned=["declared_removal", "storage_link"])
            self.rec(round_, "memory_result", confirmed + 0.2, op_key=op_key, status="ok",
                     written_ids=written if written is not None else self.declaration())
        return op_key

    def baseline(self) -> "Take":
        self.start("live", 0)
        self.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": box_around([100, 100])})
        self.ask("live", "Q3", 3, "cell-3", 53, 63, 66, 68, {"outcome": "source", "source_box_mm": box_around([200, 100])},
                 wording="L1")
        self.done("live", 120)
        self.start("offline", 720)
        self.ask("offline", "Q2", 2, "cell-2", 33, 730, 760, 765, {"outcome": "source", "source_box_mm": box_around([150, 100])})
        self.ask("offline", "Q4", 4, "cell-4", 73, 770, 790, 795, {"outcome": "source", "source_box_mm": box_around([300, 300])})
        self.done("offline", 840)
        return self

    def write(self, seal: bool = True) -> dict:
        self.folder.mkdir(parents=True, exist_ok=True)
        die_path = self.folder / "die.json"
        die_path.write_text(json.dumps(self.die), encoding="utf-8")
        die_sha = self.die_sha_override or hashlib.sha256(die_path.read_bytes()).hexdigest()
        paths = []
        for round_, records in self.journals.items():
            if not records:
                continue
            for record in records:
                if record["kind"] == "session_start" and record["die_sha256"] is None:
                    record["die_sha256"] = die_sha
            path = self.folder / f"{round_}.journal.jsonl"
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
            paths.append(path)
        memory = self.folder / "memory.jsonl"
        memory.write_text("".join(json.dumps(e) + "\n" for e in self.memory), encoding="utf-8")
        if seal and "answers_sha256" not in self.actual:
            self.actual["answers_sha256"] = score_live01.answers_sha256([*paths, memory])
        actual = self.folder / "actual.json"
        actual.write_text(json.dumps(self.actual), encoding="utf-8")
        return {"journal_paths": paths, "memory_path": memory, "die_path": die_path, "actual_path": actual}

    def score(self, **kwargs) -> dict:
        return score_live01.score(**self.write(), **kwargs)
