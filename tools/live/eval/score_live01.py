"""Score one LIVE-01 take against the sealed die and the owner's ground truth (docs/LIVE-01.md §6–§8).

Two subcommands:

    python score_live01.py seal  --journal live.jsonl --journal offline.jsonl --memory memory.jsonl
    python score_live01.py score --journal live.jsonl --journal offline.jsonl --memory memory.jsonl \
        --die live01-die.json [--die-seed live01-die-seed.SEALED.txt] --actual live01-actual.json \
        [--run-automatic DIR] [--run-assisted DIR] --out live01-score.json

``seal`` prints the answers hash the owner records before writing the ground truth. ``score`` reads the
coordinator journals (``anastylos-live-journal@1``, one per round), the memory copy the coordinator wrote to,
the die (``live01-die@1``) and the ground truth (``live01-actual@1``), and writes ``live01-score@1``.

Read-only: nothing here writes to a journal, a memory log or a run folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tools" / "proto-01"))
sys.path.insert(0, str(HERE))
from score_e3b import judge_link  # noqa: E402  frozen E3-B link criteria, reused unchanged
from h3_rubric import rubric  # noqa: E402
import make_die  # noqa: E402

SCHEMA = "live01-score@1"
JOURNAL = "anastylos-live-journal@1"
ACTUAL = "live01-actual@1"
ROUNDS = ("live", "offline")
TIME_TOLERANCE_S = 2.0
SPACE_TOLERANCE_MM = 3.0
DELAY_TOLERANCE_S = 1.0
OFFLINE_DELAY_TOLERANCE_S = 60.0
FALSE_ALARM_LIMIT = 0.25
MAX_TIME_RATIO = 2.0
SIDE_STATES = ("DEFERRED", "LEFT_UNRESOLVED", "SUPERSEDED", "FAILED")
QUESTION_FIELDS = ("slot", "question_kind", "cell", "deposit_window_s", "removal_window_s", "source_box_mm", "deposit_t_wall")
SCRUB_ACTIONS = ("scrub", "replay_open")


# --- inputs -------------------------------------------------------------------------------------------------

def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _t(stamp: str) -> float:
    return datetime.fromisoformat(stamp).timestamp()


def answers_sha256(paths: list[Path]) -> str:
    """Hash over the sorted SHA-256 hex digests of every answer artefact, one per line."""
    return hashlib.sha256("".join(f"{d}\n" for d in sorted(_sha(p) for p in paths)).encode("ascii")).hexdigest()


def read_journal(path: Path) -> dict:
    raw = Path(path).read_text(encoding="utf-8").splitlines()
    records, torn = [], 0
    for number, line in enumerate(raw, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if number == len(raw):  # the last write of a crashed process; the coordinator journals a recovery
                torn += 1
                continue
            raise SystemExit(f"{path} line {number}: not JSON")
        if record.get("schema") != JOURNAL:
            raise SystemExit(f"{path} line {number}: not an {JOURNAL} record")
        records.append(record)
    start = next((r for r in records if r["kind"] == "session_start"), None)
    if start is None or start.get("round") not in ROUNDS:
        raise SystemExit(f"{path}: needs a session_start with round in {ROUNDS}")
    return {"path": Path(path), "round": start["round"], "start": start, "records": records, "torn_lines": torn}


def read_memory(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


# --- questions ----------------------------------------------------------------------------------------------

def collect_questions(journals: list[dict]) -> dict[str, dict]:
    """question_id -> static fields (last non-null value) and every state record in journal order."""
    questions: dict[str, dict] = {}
    for journal in journals:
        for pos, record in enumerate(journal["records"]):
            if record["kind"] != "question_state":
                continue
            q = questions.setdefault(record["question_id"], {"question_id": record["question_id"], "states": []})
            q["states"].append({**record, "_round": journal["round"], "_pos": pos})
            for key in QUESTION_FIELDS:
                if record.get(key) is not None:
                    q[key] = record[key]
    return questions


def _first(states: list[dict], state: str, after: int = -1) -> dict | None:
    return next((s for s in states if s["state"] == state and s["_pos"] > after), None)


def _inside(point: list[float], box: list[float], grow: float) -> bool:
    x, y, w, h = box
    return x - grow <= point[0] <= x + w + grow and y - grow <= point[1] <= y + h + grow


def true_targets(q: dict, steps: list[dict]) -> list[dict]:
    """Ground-truth steps the question is about; empty means a false-alarm question."""
    if q.get("question_kind") == "source_of_part":
        w = q["deposit_window_s"]
        return [s for s in steps if s["destination"] == q["cell"]
                and w[0] - TIME_TOLERANCE_S <= s["deposit_t_s"] <= w[1] + TIME_TOLERANCE_S]
    if q.get("question_kind") == "destination_of_removal":
        w = q["removal_window_s"]
        return [s for s in steps if w[0] - TIME_TOLERANCE_S <= s["removal_t_s"] <= w[1] + TIME_TOLERANCE_S
                and _inside(s["source_point_mm"], q["source_box_mm"], SPACE_TOLERANCE_MM)]
    raise SystemExit(f"{q['question_id']}: unknown question kind {q.get('question_kind')!r}")


def judge_answer(q: dict, targets: list[dict], resolved: dict | None, adjudication: dict | None) -> dict:
    outcome = (resolved or {}).get("outcome")
    if outcome == "no_part":
        return {"verdict": "wrong_dismissal" if targets else "correct_dismissal", "true_part": None}
    if outcome not in ("source", "destination"):
        return {"verdict": "unresolved", "true_part": None}
    if not targets:
        return {"verdict": "wrong", "true_part": None, "why": "declared a part where no true removal or deposit was"}
    if outcome == "source" and resolved.get("source_box_mm") is not None:
        match = [s for s in targets if _inside(s["source_point_mm"], resolved["source_box_mm"], SPACE_TOLERANCE_MM)]
    elif outcome == "destination" and resolved.get("destination") is not None:
        match = [s for s in targets if s["destination"] == resolved["destination"]]
    elif adjudication is not None:  # free words only: the owner's sealed adjudication decides
        ok = adjudication["verdict"] == "correct"
        return {"verdict": "correct" if ok else "wrong", "true_part": adjudication.get("part") if ok else None,
                "adjudicated": True}
    else:
        return {"verdict": "needs_adjudication", "true_part": None}
    return {"verdict": "correct", "true_part": match[0]["part"]} if match else \
        {"verdict": "wrong", "true_part": None, "why": "answer does not match the true source or destination"}


def episodes(questions: dict[str, dict], steps: list[dict], die: dict, adjudications: dict[str, dict],
             committed_op_keys: set[str]) -> list[dict]:
    """One row per question that reached ASKED: round, assigned and delivered wording, times, verdict, rubric."""
    rows, live_ordinal = [], 0
    asked_in_order = sorted((q for q in questions.values() if _first(q["states"], "ASKED")),
                            key=lambda q: (ROUNDS.index(_first(q["states"], "ASKED")["_round"]),
                                           _first(q["states"], "ASKED")["_pos"]))
    for q in asked_in_order:
        states = q["states"]
        asked = _first(states, "ASKED")
        answered = _first(states, "ANSWERED", asked["_pos"])
        # a declaration resolves only through a committed read-back; a confirmed "no part" writes nothing
        final = next((s for s in reversed(states) if s["state"] == "CONFIRMED" and (
            s["op_key"] in committed_op_keys or (s.get("resolved") or {}).get("outcome") == "no_part")), None)
        end = _first(states, "CONFIRMED", asked["_pos"]) or next(
            (s for s in reversed(states) if s["state"] in SIDE_STATES), None)
        round_ = asked["_round"]
        assigned = None
        if round_ == "live":
            live_ordinal += 1
            assigned = die["live_questions"].get(str(asked.get("die_ordinal", live_ordinal)))
        targets = true_targets(q, steps)
        judged = judge_answer(q, targets, final.get("resolved") if final else None,
                              adjudications.get(final["op_key"]) if final else None)
        deposit = q.get("deposit_t_wall")
        rows.append({
            "question_id": q["question_id"], "round": round_, "slot": q.get("slot"), "question_kind": q.get("question_kind"),
            "op_keys": sorted({s["op_key"] for s in states}),
            "wording_assigned": assigned, "wording_delivered": asked.get("wording"),
            "rounds_asked": sorted({s["_round"] for s in states if s["state"] == "ASKED"}),
            "true_parts": [s["part"] for s in targets], "false_alarm": not targets,
            "operator_s": _t(end["t_wall"]) - _t(asked["t_wall"]) if end else None,
            "answer_s": _t(answered["t_wall"]) - _t(asked["t_wall"]) if answered else None,
            "deposit_to_answer_s": _t(answered["t_wall"]) - _t(deposit) if answered and deposit else None,
            "delivery_lag_s": _t(asked["t_wall"]) - _t(deposit) if deposit else None,
            "rubric": rubric(asked.get("text") or "", bool(asked.get("anchor_possible")), asked.get("anchor_ref") is not None),
            **judged,
        })
    return rows


# --- metrics ------------------------------------------------------------------------------------------------

def _median(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def arm_summary(rows: list[dict], operator_minutes: float) -> dict:
    count = lambda verdict: sum(r["verdict"] == verdict for r in rows)  # noqa: E731
    correct, wrong = count("correct"), count("wrong")
    parts = sorted({r["true_part"] for r in rows if r["verdict"] == "correct"})
    return {
        "questions_asked": len(rows), "resolved": correct + wrong + count("needs_adjudication"),
        "correct": correct, "wrong_declarations": wrong, "wrong_dismissals": count("wrong_dismissal"),
        "correct_dismissals": count("correct_dismissal"), "unresolved": count("unresolved"),
        "needs_adjudication": count("needs_adjudication"), "correct_parts": parts,
        "accuracy": correct / (correct + wrong) if correct + wrong else None,
        "wrong_declaration_rate": wrong / len(rows) if rows else None,
        "false_alarm_questions": sum(r["false_alarm"] for r in rows),
        "false_alarm_rate": sum(r["false_alarm"] for r in rows) / len(rows) if rows else None,
        "operator_minutes": operator_minutes,
        "resolved_parts_per_minute": len(parts) / operator_minutes if operator_minutes else None,
        "minutes_per_resolved_part": operator_minutes / len(parts) if parts else None,
        "median_answer_s": _median([r["answer_s"] for r in rows]),
        "median_deposit_to_answer_s": _median([r["deposit_to_answer_s"] for r in rows]),
        "h3_pass_rate": sum(r["rubric"]["pass"] for r in rows) / len(rows) if rows else None,
    }


def _episode_minutes(rows: list[dict]) -> float:
    return sum(r["operator_s"] or 0.0 for r in rows) / 60.0


def round_minutes(journal: dict | None) -> float | None:
    if journal is None:
        return None
    done = next((r for r in journal["records"] if r["kind"] == "done"), None)
    return (_t(done["t_wall"]) - _t(journal["start"]["t_wall"])) / 60.0 if done else None


def arms(rows: list[dict], offline_journal: dict | None) -> dict:
    live = [r for r in rows if r["round"] == "live"]
    offline = [r for r in rows if r["round"] == "offline"]
    offline_round = round_minutes(offline_journal)
    return {
        "live_L0": arm_summary([r for r in live if r["wording_assigned"] == "L0"],
                               _episode_minutes([r for r in live if r["wording_assigned"] == "L0"])),
        "live_L1": arm_summary([r for r in live if r["wording_assigned"] == "L1"],
                               _episode_minutes([r for r in live if r["wording_assigned"] == "L1"])),
        "live": arm_summary(live, _episode_minutes(live)),
        "offline": arm_summary(offline, offline_round if offline_round is not None else _episode_minutes(offline)),
        "offline_by_episodes": arm_summary(offline, _episode_minutes(offline)),
    }


def _p95(values: list[float]) -> float | None:
    values = sorted(values)
    return values[max(0, -(-95 * len(values) // 100) - 1)] if values else None


def provider_summary(journals: list[dict]) -> dict:
    calls = [r for j in journals for r in j["records"] if r["kind"] == "provider_call"]
    fallbacks = [r for j in journals for r in j["records"] if r["kind"] == "fallback"]
    by_status: dict[str, int] = {}
    for call in calls:
        key = f"{call.get('task')}:{call.get('status')}"
        by_status[key] = by_status.get(key, 0) + 1
    wording = [c["latency_s"] for c in calls if c.get("task") == "question_wording" and c.get("status") == "ok"
               and c.get("latency_s") is not None and not c.get("cache_hit")]
    reasons: dict[str, int] = {}
    for item in fallbacks:
        reasons[str(item.get("reason"))] = reasons.get(str(item.get("reason")), 0) + 1
    return {"calls": by_status, "cache_hits": sum(bool(c.get("cache_hit")) for c in calls),
            "question_wording_latency_s": {"n": len(wording), "median": _median(wording), "p95": _p95(wording),
                                           "max": max(wording) if wording else None},
            "fallbacks": reasons,
            "grounding_rejections": sum(str(f.get("reason", "")).startswith("grounding") for f in fallbacks)}


# --- integrity and contamination ----------------------------------------------------------------------------

def _answer_may_write(event: dict) -> bool:
    """An operator answer may only write declared content; anything that claims image evidence is a promotion."""
    kind, basis = event.get("type"), (event.get("basis") or {}).get("kind")
    if event.get("inference") is not None:
        return False
    if kind in ("declared_removal", "storage_link"):
        return True
    if kind == "compatible_group":
        return basis == "operator_statement"
    if kind == "tray_link":
        return basis == "human"
    return False


def integrity(journals: list[dict], questions: dict[str, dict], memory: list[dict], actual: dict) -> dict:
    by_id = {e["id"]: e for e in memory}
    op_question = {s["op_key"]: (q["question_id"], s.get("revision")) for q in questions.values() for s in q["states"]}
    counters = {"declarations_promoted_to_evidence": [], "commits_without_read_back": [], "stale_commits": [],
                "duplicate_commits": [], "commits_without_intent": [], "commits_missing_from_memory": [],
                "unjournaled_declarations": [], "scrubbing_live": [], "duplicate_memory_ids": []}
    written, committed_questions = set(), {}
    for journal in journals:
        records = journal["records"]
        for pos, record in enumerate(records):
            if record["kind"] == "ui_event" and journal["round"] == "live" and record.get("action") in SCRUB_ACTIONS:
                counters["scrubbing_live"].append({"source": "journal", "t_wall": record.get("t_wall")})
            if record["kind"] != "memory_result" or record.get("status") != "ok":
                continue
            op_key = record["op_key"]
            before = records[:pos]
            question_id, revision = op_question.get(op_key, (None, None))
            read_back = next((i for i, r in enumerate(before) if r["kind"] == "question_state"
                              and r.get("op_key") == op_key and r["state"] == "READ_BACK"), None)
            if read_back is None or not any(r["kind"] == "question_state" and r.get("op_key") == op_key
                                            and r["state"] == "CONFIRMED" for r in before[read_back:]):
                counters["commits_without_read_back"].append(op_key)
            if not any(r["kind"] == "intent" and r.get("op_key") == op_key for r in before):
                counters["commits_without_intent"].append(op_key)
            if any(r["kind"] == "question_state" and r.get("question_id") == question_id
                   and ((r.get("revision") or 0) > (revision or 0) or (r.get("op_key") == op_key and r["state"] == "SUPERSEDED"))
                   for r in before):
                counters["stale_commits"].append(op_key)
            committed_questions.setdefault(question_id or op_key, []).append(op_key)
            for event_id in record.get("written_ids") or []:
                written.add(event_id)
                event = by_id.get(event_id)
                if event is None:
                    counters["commits_missing_from_memory"].append(event_id)
                elif not _answer_may_write(event):
                    counters["declarations_promoted_to_evidence"].append(event_id)
    for question_id, op_keys in committed_questions.items():
        counters["duplicate_commits"].extend(op_keys[1:])
    counters["unjournaled_declarations"] = [e["id"] for e in memory
                                            if e.get("type") in ("declared_removal", "storage_link") and e["id"] not in written]
    seen: set[str] = set()
    for event in memory:
        if event["id"] in seen:
            counters["duplicate_memory_ids"].append(event["id"])
        seen.add(event["id"])
    counters["scrubbing_live"] += [{"source": "witness", **item} for item in actual.get("scrubbing_live") or []]
    result = {name: {"count": len(items), "items": items} for name, items in counters.items()}
    result["journal_torn_lines"] = sum(j["torn_lines"] for j in journals)
    result["recoveries"] = sum(r["kind"] == "recovery" for j in journals for r in j["records"])
    return result


def contamination(journals: list[dict], rows: list[dict], die: dict, die_sha: str, seed_problems: list[str],
                  actual: dict, sealed_hash: str) -> dict:
    flags = {"die_hash_mismatch": [], "part_asked_more_than_once": [], "arm_mismatch": [], "wording_mismatch": [],
             "answers_not_sealed_before_truth": [], "offline_delay_violation": [], "delay_parity_violations": [],
             "seed_problems": list(seed_problems)}
    for journal in journals:
        if journal["start"].get("die_sha256") != die_sha:
            flags["die_hash_mismatch"].append(str(journal["path"].name))
    if actual.get("answers_sha256") != sealed_hash:
        flags["answers_not_sealed_before_truth"].append({"recorded": actual.get("answers_sha256"), "inputs": sealed_hash})
    asked_for: dict[str, list[str]] = {}
    for row in rows:
        for part in row["true_parts"]:
            asked_for.setdefault(part, []).append(row["question_id"])
        if len(row["rounds_asked"]) > 1:
            flags["part_asked_more_than_once"].append({"question": row["question_id"], "rounds": row["rounds_asked"]})
        arm = die["slots"].get(str(row["slot"]))
        if arm != row["round"]:
            flags["arm_mismatch"].append({"question": row["question_id"], "slot": row["slot"], "die": arm, "round": row["round"]})
    flags["part_asked_more_than_once"] += [{"part": p, "questions": qs} for p, qs in asked_for.items() if len(qs) > 1]
    fallback_ops = {r.get("op_key") for j in journals for r in j["records"] if r["kind"] == "fallback"}
    delay = die["delivery_delay_s"]
    for row in (r for r in rows if r["round"] == "live"):
        delivered, assigned = row["wording_delivered"], row["wording_assigned"]
        fell_back = assigned == "L1" and delivered == "L0" and fallback_ops & set(row["op_keys"])
        if delivered != assigned and not fell_back:
            flags["wording_mismatch"].append({"question": row["question_id"], "assigned": assigned, "delivered": delivered})
        lag = row["delivery_lag_s"]
        if lag is None or abs(lag - delay) > DELAY_TOLERANCE_S:
            flags["delay_parity_violations"].append({"question": row["question_id"], "wording": assigned, "lag_s": lag})
    live = next((j for j in journals if j["round"] == "live"), None)
    offline = next((j for j in journals if j["round"] == "offline"), None)
    if live and offline:
        done = next((r for r in live["records"] if r["kind"] == "done"), None)
        gap = _t(offline["start"]["t_wall"]) - _t(done["t_wall"]) if done else None
        if gap is None or abs(gap - die["offline_delay_s"]) > OFFLINE_DELAY_TOLERANCE_S:
            flags["offline_delay_violation"].append({"gap_s": gap, "planned_s": die["offline_delay_s"]})
    return {name: {"count": len(items), "items": items} for name, items in flags.items()}


def autonomy(run: Path | None, steps: list[dict]) -> dict | None:
    """Session-runner links of one run, judged with the E3-B criteria and counted per autonomy class."""
    if run is None:
        return None
    parts = _load(run / "parts.json")["parts"]
    boxes = {s["id"]: s["geometry"]["bbox_mm"] for s in _load(run / "registry.json")["sources"]}
    counts = {cls: {"links": 0, "correct": 0, "incorrect": 0} for cls in ("automatic", "operator_assisted")}
    for part in parts:
        if not str(part.get("status", "")).startswith("candidate"):
            continue
        cls = part.get("autonomy", "automatic")
        counts[cls]["links"] += 1
        counts[cls][judge_link(part, steps, boxes)["verdict"]] += 1
    return counts


# --- verdicts -----------------------------------------------------------------------------------------------

def safety(arm_table: dict, checks: dict) -> list[str]:
    reasons = [f"{name} = {checks[name]['count']}" for name in
               ("declarations_promoted_to_evidence", "scrubbing_live", "stale_commits", "duplicate_commits",
                "commits_without_read_back") if checks[name]["count"]]
    for arm in ("live", "offline"):
        rate = arm_table[arm]["false_alarm_rate"]
        if rate is not None and rate > FALSE_ALARM_LIMIT:
            reasons.append(f"{arm} false-alarm rate {rate:.2f} > {FALSE_ALARM_LIMIT}")
    return reasons


def timing_verdict(arm_table: dict, flags: dict, safety_reasons: list[str], autonomy_flags: list[str]) -> dict:
    blocking = [n for n in ("die_hash_mismatch", "part_asked_more_than_once", "arm_mismatch",
                            "answers_not_sealed_before_truth", "offline_delay_violation", "seed_problems")
                if flags[n]["count"]] + autonomy_flags
    live, offline = arm_table["live_L0"], arm_table["offline"]
    if live["needs_adjudication"] or offline["needs_adjudication"]:
        blocking.append("answers need adjudication")
    if blocking:
        return {"verdict": "INCONCLUSIVE", "why": blocking}
    if safety_reasons:
        return {"verdict": "SAFETY_FAIL", "why": safety_reasons}
    if live["accuracy"] is None or offline["accuracy"] is None or not live["operator_minutes"] or not offline["operator_minutes"]:
        return {"verdict": "INSUFFICIENT", "why": ["an arm has no scored answer or no operator time"]}
    live_mpp, off_mpp = live["minutes_per_resolved_part"], offline["minutes_per_resolved_part"]
    if live["accuracy"] < offline["accuracy"]:
        return {"verdict": "REJECT", "why": ["live accuracy lower than offline"]}
    if off_mpp is not None and (live_mpp is None or live_mpp > MAX_TIME_RATIO * off_mpp):
        return {"verdict": "REJECT", "why": ["live operator time per resolved part more than twice offline"]}
    if (live["resolved_parts_per_minute"] or 0) > (offline["resolved_parts_per_minute"] or 0):
        return {"verdict": "ACCEPT", "why": ["more resolved parts per minute, accuracy not lower"]}
    return {"verdict": "NOT_ACCEPTED", "why": ["resolved parts per minute not higher than offline"]}


def wording_verdict(arm_table: dict, flags: dict) -> dict:
    blocking = [n for n in ("die_hash_mismatch", "part_asked_more_than_once", "arm_mismatch", "wording_mismatch",
                            "answers_not_sealed_before_truth", "delay_parity_violations", "seed_problems")
                if flags[n]["count"]]
    if blocking:
        return {"verdict": "INCONCLUSIVE", "why": blocking}
    l1, l0 = arm_table["live_L1"], arm_table["live_L0"]
    if not l1["questions_asked"] or not l0["questions_asked"]:
        return {"verdict": "INSUFFICIENT", "why": ["no L1 or no L0 question was asked"]}
    if l1["wrong_declaration_rate"] > l0["wrong_declaration_rate"]:
        return {"verdict": "NOT_ADOPTED", "why": ["L1 wrong-declaration rate higher than L0"]}
    faster = l1["median_answer_s"] is not None and l0["median_answer_s"] is not None and l1["median_answer_s"] < l0["median_answer_s"]
    better_h3 = (l1["h3_pass_rate"] or 0) > (l0["h3_pass_rate"] or 0)
    if faster or better_h3:
        return {"verdict": "ADOPT", "why": [w for w, ok in (("faster answers", faster), ("better H3", better_h3)) if ok]}
    return {"verdict": "NOT_ADOPTED", "why": ["neither faster nor better on H3"]}


# --- entry --------------------------------------------------------------------------------------------------

def score(journal_paths: list[Path], memory_path: Path, die_path: Path, actual_path: Path, die_seed: Path | None = None,
          run_automatic: Path | None = None, run_assisted: Path | None = None) -> dict:
    journals = [read_journal(p) for p in journal_paths]
    if sorted(j["round"] for j in journals) != sorted(set(j["round"] for j in journals)):
        raise SystemExit("at most one journal per round")
    die, actual, memory = _load(die_path), _load(actual_path), read_memory(memory_path)
    if die.get("schema") != make_die.SCHEMA:
        raise SystemExit(f"{die_path}: not a {make_die.SCHEMA} file")
    if actual.get("schema") != ACTUAL:
        raise SystemExit(f"{actual_path}: not a {ACTUAL} file")
    seed_problems = make_die.verify(die, make_die.read_seed(die_seed)) if die_seed else []
    steps = actual["steps"]
    adjudications = {a["op_key"]: a for a in actual.get("adjudications") or []}
    questions = collect_questions(journals)
    committed = {r["op_key"] for j in journals for r in j["records"] if r["kind"] == "memory_result" and r.get("status") == "ok"}
    rows = episodes(questions, steps, die, adjudications, committed)
    arm_table = arms(rows, next((j for j in journals if j["round"] == "offline"), None))
    checks = integrity(journals, questions, memory, actual)
    flags = contamination(journals, rows, die, _sha(die_path), seed_problems, actual,
                          answers_sha256([*journal_paths, memory_path]))
    runs = {"automatic": autonomy(run_automatic, steps), "assisted": autonomy(run_assisted, steps)}
    autonomy_flags = (["automatic run contains operator_assisted links"]
                      if runs["automatic"] and runs["automatic"]["operator_assisted"]["links"] else [])
    safety_reasons = safety(arm_table, checks)
    return {
        "schema": SCHEMA,
        "inputs": {"journals": {str(p): _sha(p) for p in journal_paths}, "memory": _sha(memory_path),
                   "die": _sha(die_path), "actual": _sha(actual_path), "die_seed_checked": die_seed is not None},
        "delivery_delay_s": die["delivery_delay_s"],
        "questions": rows,
        "never_asked": sorted(q["question_id"] for q in questions.values() if not _first(q["states"], "ASKED")),
        "arms": arm_table,
        "providers": provider_summary(journals),
        "integrity": checks,
        "contamination": flags,
        "autonomy": runs,
        "safety": {"verdict": "FAIL" if safety_reasons else "PASS", "why": safety_reasons},
        "live_timing": timing_verdict(arm_table, flags, safety_reasons, autonomy_flags),
        "l1_wording": wording_verdict(arm_table, flags),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("seal", help="print the answers hash to record before writing the ground truth")
    run = sub.add_parser("score", help="score a take")
    for p in (seal, run):
        p.add_argument("--journal", required=True, type=Path, action="append")
        p.add_argument("--memory", required=True, type=Path)
    run.add_argument("--die", required=True, type=Path)
    run.add_argument("--die-seed", type=Path)
    run.add_argument("--actual", required=True, type=Path)
    run.add_argument("--run-automatic", type=Path)
    run.add_argument("--run-assisted", type=Path)
    run.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "seal":
        print(answers_sha256([*args.journal, args.memory]))
        return
    result = score(args.journal, args.memory, args.die, args.actual, args.die_seed, args.run_automatic, args.run_assisted)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("safety", "live_timing", "l1_wording")}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
