"""Append-only coordinator journal ``anastylos-live-journal@1`` (CONTRACT §3.4).

One JSON object per line, written with one ``write`` and ``fsync`` before the call returns. A
torn or unreadable line is never repaired or truncated: loading raises ``JournalCorrupt`` and the
coordinator stops with that message until a human decides what to do with the file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "anastylos-live-journal@1"
KINDS = ("session_start", "question_state", "provider_call", "intent", "memory_result", "done", "recovery", "fallback",
         "ui_event")


class JournalCorrupt(ValueError):
    """The journal on disk cannot be trusted (torn last line or invalid record)."""


def utc_now() -> str:
    """ISO-8601 with UTC offset and milliseconds, the `t_wall` format of CONTRACT §3.4 (0.2)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.records: list[dict] = []

    def load(self) -> list[dict]:
        if not self.path.exists():
            self.records = []
            return self.records
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            start = raw.rfind(b"\n") + 1
            raise JournalCorrupt(f"{self.path}: last line is torn (no newline after byte {start}); "
                                 "the coordinator stops and does not truncate it")
        records = []
        for lineno, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JournalCorrupt(f"{self.path} line {lineno}: invalid JSON ({exc})") from exc
            if not isinstance(record, dict) or record.get("schema") != SCHEMA or record.get("kind") not in KINDS:
                raise JournalCorrupt(f"{self.path} line {lineno}: not a {SCHEMA} record")
            if record.get("seq") != lineno:
                raise JournalCorrupt(f"{self.path} line {lineno}: sequence {record.get('seq')!r}, expected {lineno}")
            records.append(record)
        self.records = records
        return records

    def append(self, kind: str, **fields) -> dict:
        if kind not in KINDS:
            raise ValueError(f"unknown journal kind {kind!r}")
        record = {"schema": SCHEMA, "seq": len(self.records) + 1, "kind": kind, "t_wall": utc_now(), **fields}
        line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        return record

    def of_kind(self, kind: str) -> list[dict]:
        return [r for r in self.records if r["kind"] == kind]
