"""Replay episodes: FULL-01 v21 run folders (read-only) and seeded synthetic adversarial families (PREREG §3)."""

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

BUSY_PAD_S = 2.0
SEGMENT_TAIL_S = 5.0
PHASES = ("P1", "P2", "P3")
FAMILIES = ("stale_revisions", "simultaneous", "unanswerable", "busy_bursts", "resolvable")
GENERATOR_SEEDS = (1, 2)


@dataclass(frozen=True)
class Question:
    id: str
    kind: str
    target: str               # part id (source_of_part) or removal event id (destination_of_removal)
    cell: str | None
    open_t: float             # end of the deposit / removal window
    candidates: tuple
    truth: dict               # {"kind": choice|declared|not_a_part|unanswerable, "value": ...}; oracle only
    resolvable_from_t: float | None = None


@dataclass(frozen=True)
class Episode:
    id: str
    source: str
    segment: str
    questions: tuple
    busy: tuple               # merged (start, end) intervals
    segment_end: float
    revision_changes: tuple = ()          # (t, question_id, new candidates)
    committed_at_start: tuple = ()        # {"part_ref", "basis", "cell", "t_s"}
    provenance: dict = field(default_factory=dict)


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _padded(window):
    return (max(0.0, window[0] - BUSY_PAD_S), window[1] + BUSY_PAD_S)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _truth(answer):
    if answer is None:
        raise ValueError("question without owner answer")
    if answer.get("not_a_part"):
        return {"kind": "not_a_part", "value": None}
    if answer.get("choice"):
        return {"kind": "choice", "value": answer["choice"]}
    if answer.get("source") or answer.get("destination"):
        return {"kind": "declared", "value": "declared"}
    raise ValueError(f"owner answer for {answer['question']} carries no usable truth")


def load_full01(root):
    """Episodes P1..P3 from a FULL-01 run root; reads only questions.json, parts.json and audit/apply_input.jsonl."""
    root = Path(root)
    answers_path = root / "audit" / "apply_input.jsonl"
    answers = {}
    for line in answers_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if record["question"] != "EXTRA":
                answers[record["question"]] = record
    episodes = []
    for phase in PHASES:
        questions_path, parts_path = root / phase / "questions.json", root / phase / "parts.json"
        raw_questions = json.loads(questions_path.read_text(encoding="utf-8"))["questions"]
        parts = json.loads(parts_path.read_text(encoding="utf-8"))
        questions, busy = [], []
        for raw in raw_questions:
            if raw["kind"] == "source_of_part":
                window, target, cell = raw["deposit_window_s"], raw["part"], raw["cell"]
                candidates = tuple(c["removal_event"] for c in raw["candidates"])
                busy.extend(_padded(c["window_s"]) for c in raw["candidates"])
            else:
                window, target, cell, candidates = raw["window_s"], raw["removal_event"], None, ()
            busy.append(_padded(window))
            questions.append(Question(raw["id"], raw["kind"], target, cell, float(window[1]), candidates,
                                      _truth(answers.get(raw["id"]))))
        written = set(parts["memory"]["written"])
        committed = tuple({"part_ref": p["part"], "basis": "linker", "cell": p["cell"],
                           "t_s": p["deposit_window_s"][1]} for p in parts["parts"] if p["part"] in written)
        for p in parts["parts"]:
            busy.append(_padded(p["deposit_window_s"]))
        merged = merge_intervals(busy)
        last = max([q.open_t for q in questions] + [end for _, end in merged])
        episodes.append(Episode(
            id=f"FULL-01-{phase}", source="FULL-01 v21", segment=phase, questions=tuple(questions), busy=merged,
            segment_end=round(last + SEGMENT_TAIL_S, 3), committed_at_start=committed,
            provenance={str(p): sha256_file(p) for p in (questions_path, parts_path, answers_path)}))
    return episodes


def _synthetic_question(rng, index, open_t, n_candidates=None):
    n = rng.randint(1, 4) if n_candidates is None else n_candidates
    candidates = tuple(f"E-S{index:02d}{k}" for k in range(n))
    roll = rng.random()
    if candidates and roll < 0.6:
        truth = {"kind": "choice", "value": rng.choice(candidates)}
    elif roll < 0.9:
        truth = {"kind": "declared", "value": "declared"}
    else:
        truth = {"kind": "not_a_part", "value": None}
    return Question(f"Q-S-{index:02d}", "source_of_part", f"S-C{index:02d}", f"cell-{index}", round(open_t, 3),
                    candidates, truth)


def synthetic(family, seed):
    """One deterministic adversarial episode of the given family (PREREG §3)."""
    if family not in FAMILIES:
        raise ValueError(family)
    rng = random.Random(f"l2sim-synthetic|{family}|{seed}")
    revisions, busy = [], []
    if family == "simultaneous":
        questions = [_synthetic_question(rng, i + 1, 20.0 + 0.25 * i) for i in range(8)]
        busy.append((14.0, 40.0))
    else:
        count = {"stale_revisions": 4, "unanswerable": 4, "busy_bursts": 5, "resolvable": 4}[family]
        times = sorted(rng.uniform(10.0, 80.0) for _ in range(count))
        questions = [_synthetic_question(rng, i + 1, t) for i, t in enumerate(times)]
    if family == "unanswerable":
        first, second = questions[0], questions[1]
        questions[0] = Question(first.id, first.kind, first.target, first.cell, first.open_t, (),
                                {"kind": "unanswerable", "value": None})
        questions[1] = Question(second.id, second.kind, second.target, second.cell, second.open_t,
                                tuple(f"E-S02{k}" for k in range(3)), {"kind": "unanswerable", "value": None})
    if family == "stale_revisions":
        for q in rng.sample(questions, 2):
            new = q.candidates + (q.candidates[0][:-1] + "x",)
            revisions.append((round(q.open_t + rng.uniform(2.0, 15.0), 3), q.id, new))
    if family == "resolvable":
        for i in rng.sample(range(len(questions)), 2):
            q = questions[i]
            questions[i] = Question(q.id, q.kind, q.target, q.cell, q.open_t, q.candidates, q.truth,
                                    round(q.open_t + rng.uniform(5.0, 20.0), 3))
    if family == "busy_bursts":
        t = 0.0
        while t < 95.0:
            busy.append((t, t + 7.0))
            t += 10.0
    else:
        busy.extend((max(0.0, q.open_t - 3.0 - BUSY_PAD_S), q.open_t + BUSY_PAD_S) for q in questions)
    merged = merge_intervals(busy)
    last = max([q.open_t for q in questions] + [end for _, end in merged] + [t for t, _, _ in revisions])
    return Episode(id=f"SYN-{family}-{seed}", source="synthetic", segment="S", questions=tuple(questions),
                   busy=merged, segment_end=round(last + SEGMENT_TAIL_S, 3), revision_changes=tuple(sorted(revisions)),
                   provenance={"generator": f"episodes.synthetic({family!r}, {seed})"})


def primary(full01_root):
    """The preregistered 13 episodes: FULL-01 P1..P3 plus 5 families x generator seeds {1, 2}."""
    return load_full01(full01_root) + [synthetic(f, s) for f in FAMILIES for s in GENERATOR_SEEDS]
