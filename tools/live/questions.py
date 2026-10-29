"""Open questions of a live session, loaded from ``questions.json`` (``anastylos-session-questions@1``).

Questions are read through ``answers.load_runs`` so the live core sees exactly what
``answers.plan`` validates against. Each question carries a fingerprint of everything an
answer depends on (kind, cell, windows, candidates and their sites); when a reload changes a
fingerprint, the question's revision increments and older op_keys become stale.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from contracts import answers


def _sites(sources: list[dict]) -> list:
    return sorted(([s["site"], s.get("centroid_mm")] for s in sources), key=lambda site: site[0])


def fingerprint(question: dict) -> str:
    relevant = {
        "kind": question["kind"],
        "part": question.get("part"),
        "cell": question.get("cell"),
        "removal_event": question.get("removal_event"),
        "window": question.get("deposit_window_s") or question.get("window_s"),
        "candidates": [{"removal_event": c["removal_event"], "window_s": c["window_s"],
                        "sites": _sites(c["sources"])}
                       for c in question.get("candidates", [])],
        "sources": _sites(question.get("sources", [])),
        "memory_phase": question.get("memory_phase"),
    }
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode("utf-8")).hexdigest()


def candidate_token(index: int) -> str:
    """Screen token of a candidate (as in ``answers.package``): A, B, C, ..."""
    return chr(ord("A") + index)


class QuestionBook:
    """The current questions plus the revision of each (revisions survive restarts via the journal)."""

    def __init__(self, run_dirs: list[Path]):
        self.run_dirs = [Path(r) for r in run_dirs]
        self.questions: dict[str, dict] = {}
        self.parts_reports: dict[str, dict] = {}
        self.revisions: dict[str, tuple[int, str]] = {}  # question id -> (revision, fingerprint)
        self._load()

    def _load(self) -> None:
        questions, _ = answers.load_runs(self.run_dirs)
        self.questions = {qid: q for qid, q in questions.items() if q["kind"] != "extra"}
        self.parts_reports = {}
        for run in self.run_dirs:
            report = json.loads((run / "parts.json").read_text(encoding="utf-8"))
            self.parts_reports[report["phase"]] = report

    def restore(self, question_id: str, revision: int, fp: str) -> None:
        """Adopt a revision recorded in the journal by an earlier coordinator of this live session."""
        known = self.revisions.get(question_id)
        if known is None or revision > known[0]:
            self.revisions[question_id] = (revision, fp)

    def sync(self) -> list[tuple[str, int, str]]:
        """Assign revisions to questions whose fingerprint is new; returns (id, revision, fingerprint) changes."""
        changes = []
        for qid in sorted(self.questions):
            fp = fingerprint(self.questions[qid])
            known = self.revisions.get(qid)
            if known is None or known[1] != fp:
                revision = 1 if known is None else known[0] + 1
                self.revisions[qid] = (revision, fp)
                changes.append((qid, revision, fp))
        return changes

    def reload(self) -> list[tuple[str, int, str]]:
        """Re-read the run folders (e.g. after a re-link) and bump revisions of changed questions."""
        self._load()
        return self.sync()

    def revision(self, question_id: str) -> int:
        return self.revisions[question_id][0]

    def live_order(self) -> list[str]:
        """Phase, then deposit / removal time: the order a live session raises the questions in."""
        def key(qid):
            q = self.questions[qid]
            return (q["phase_tag"], (q.get("deposit_window_s") or q.get("window_s"))[0])
        return sorted(self.questions, key=key)

    def choice_tokens(self, question_id: str) -> dict[str, str]:
        """Screen token -> removal event of the question's candidates."""
        return {candidate_token(i): c["removal_event"] for i, c in enumerate(self.questions[question_id].get("candidates", []))}
