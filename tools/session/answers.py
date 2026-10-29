"""Turn operator answers to a session's open questions into declared Assembly Memory events.

Reads the run folders of one session (``parts.json`` and ``questions.json`` from
``session.py``) and an answers file (JSON lines), checks every answer against
its question, and writes only valid ones as operator-declared events:

- ``source_of_part``: ``choice`` names one of the question's candidate removal
  events, or ``source`` describes the place in the operator's words. Written as
  a ``declared_removal`` (the chosen candidate's sites are quoted) plus a
  ``storage_link`` to the part's cell.
- ``destination_of_removal``: ``destination`` is ``cell-N`` or any place name
  (e.g. ``OUT-A``). Written as a ``declared_removal`` for that removal plus a
  ``storage_link`` to the destination.
- ``label`` (optional) is the operator's name for the part; ``identical_to``
  (optional) lists other part numbers that are interchangeable with it.

An answer with ``skip: true``, an unknown question, a candidate that was not
offered, or a missing field is rejected and reported; nothing is guessed.

``remap`` carries answers from an earlier run of the same videos onto a
rebuilt run. Question ids and event ids may differ, so a question maps only
when exactly one rebuilt question has the same phase, kind and cell (or
removal sites) and an overlapping time window; a chosen candidate maps only
to the one rebuilt candidate with the same sites and an overlapping window.
Anything else is reported as unresolved and never forced.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Child Pythons print in the console code page unless told otherwise; operator words carry umlauts.
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_runs(run_dirs: list[Path]) -> tuple[dict, dict]:
    """questions by id (with their run's memory phase), parts by number."""
    questions, parts = {}, {}
    for run in run_dirs:
        report = _json(run / "parts.json")
        phase_id = report["memory"]["phase"]
        questions.setdefault("EXTRA", {"id": "EXTRA", "kind": "extra", "phases": {}})["phases"][report["phase"]] = phase_id
        for part in report["parts"]:
            parts[part["part"]] = {**part, "memory_phase": phase_id}
        for question in _json(run / "questions.json")["questions"]:
            questions[question["id"]] = {**question, "memory_phase": phase_id, "phase_tag": report["phase"]}
    return questions, parts


def _sites_text(sources: list[dict]) -> str:
    return ", ".join(f"{s['site']}" + (f" at {s['centroid_mm'][0]:.0f}/{s['centroid_mm'][1]:.0f} mm" if s.get("centroid_mm") else "")
                     for s in sources)


def _overlap(a: list[float], b: list[float]) -> float:
    """Intersection over union of two time windows (0 when they do not overlap)."""
    inter = min(a[1], b[1]) - max(a[0], b[0])
    union = max(a[1], b[1]) - min(a[0], b[0])
    return max(0.0, inter) / union if union > 0 else float(a == b)


def _sites(sources: list[dict]) -> frozenset:
    return frozenset(s["site"] for s in sources)


def _same_context(old: dict, new: dict, min_overlap: float) -> bool:
    if (old["kind"], old.get("phase_tag")) != (new["kind"], new.get("phase_tag")):
        return False
    if old["kind"] == "source_of_part":
        return old["cell"] == new["cell"] and _overlap(old["deposit_window_s"], new["deposit_window_s"]) >= min_overlap
    return bool(_sites(old["sources"]) & _sites(new["sources"])) and _overlap(old["window_s"], new["window_s"]) >= min_overlap


def remap(answers: list[dict], old_questions: dict, new_questions: dict, min_overlap: float = 0.5) -> tuple[list[dict], list[dict]]:
    """(answers rewritten for the rebuilt questions, one report entry per input answer)."""
    replayed, report = [], []
    for answer in answers:
        old = old_questions.get(answer.get("question"))
        if answer.get("question") == "EXTRA" or old is None or old["kind"] == "extra":
            if answer.get("question") == "EXTRA":
                replayed.append({**answer, "replayed_from": {"question": "EXTRA"}})
            report.append({"question": answer.get("question"), "outcome": "carried" if answer.get("question") == "EXTRA" else "unknown_question"})
            continue
        matches = [q for q in new_questions.values() if q["kind"] != "extra" and _same_context(old, q, min_overlap)]
        if len(matches) != 1:
            outcome = ("false_positive_eliminated" if answer.get("not_a_part") else "unresolved_no_counterpart") if not matches \
                else "unresolved_ambiguous"
            report.append({"question": old["id"], "outcome": outcome, "matches": [q["id"] for q in matches], "answer": answer})
            continue
        new = matches[0]
        entry = {**answer, "question": new["id"], "replayed_from": {"question": old["id"]}}
        result = {"question": old["id"], "outcome": "mapped", "to": new["id"]}
        if answer.get("choice"):
            chosen = next(c for c in old["candidates"] if c["removal_event"] == answer["choice"])
            same = [c for c in new.get("candidates", []) if _sites(c["sources"]) == _sites(chosen["sources"])
                    and _overlap(c["window_s"], chosen["window_s"]) >= min_overlap]
            if len(same) != 1:
                # Keep what the operator said, quoting the earlier candidate; no automatic event is linked.
                where = ", ".join(f"{s['site']} at {s['centroid_mm'][0]:.0f}/{s['centroid_mm'][1]:.0f} mm" if s.get("centroid_mm") else s["site"]
                                  for s in chosen["sources"])
                entry.update(choice=None, source=f"operator chose the earlier run's candidate {chosen['removal_event']} ({where}, "
                                                 f"{chosen['window_s'][0]:.1f}-{chosen['window_s'][1]:.1f} s); no unique counterpart after the rebuild")
                replayed.append(entry)
                report.append({**result, "outcome": "unresolved_candidate_kept_as_declaration", "old_choice": answer["choice"],
                               "matches": [c["removal_event"] for c in same],
                               "rebuilt_candidates": [{"removal_event": c["removal_event"], "window_s": c["window_s"], "sites": sorted(_sites(c["sources"]))}
                                                      for c in new.get("candidates", [])]})
                continue
            entry["choice"] = same[0]["removal_event"]
            result["choice"] = [answer["choice"], same[0]["removal_event"]]
        replayed.append(entry)
        report.append(result)
    return replayed, report


def plan(answer: dict, questions: dict) -> dict:
    """Validated memory actions for one answer, or a rejection with the reason."""
    question = questions.get(answer.get("question"))
    if question is None:
        return {"rejected": "unknown question"}
    if answer.get("skip"):
        return {"rejected": "skipped by the operator"}
    if answer.get("not_a_part"):
        return {"dismissed": "operator: not a part (e.g. overhang, tool, hand shadow)"}
    label = answer.get("label") or "part (no name given)"
    if question["kind"] == "extra":
        phase = question["phases"].get(answer.get("phase"))
        missing = [k for k in ("label", "destination") if not str(answer.get(k) or "").strip()]
        if phase is None or missing:
            return {"rejected": f"extra part needs a known 'phase' {sorted(question['phases'])} and {missing or 'nothing else'}"}
        # A part the operator named stays in memory even without its origin; the source is then explicitly unknown.
        source = str(answer.get("source") or "").strip() or None
        return {"label": answer["label"], "source": source, "location": answer["destination"], "phase": phase,
                "part": None, "note": "operator-added part that raised no question" + ("" if source else "; source not stated")
                + (f"; replayed; {answer['provenance']}" if answer.get("provenance") else ""),
                "t_s": answer.get("t_s")}
    if question["kind"] == "source_of_part":
        if answer.get("choice"):
            chosen = next((c for c in question["candidates"] if c["removal_event"] == answer["choice"]), None)
            if chosen is None:
                return {"rejected": f"choice {answer['choice']!r} was not one of the offered candidates"}
            source = f"automatic candidate {chosen['removal_event']} ({_sites_text(chosen['sources'])}), chosen by the operator"
            t_s = chosen["window_s"][0]
        elif str(answer.get("source", "")).strip():
            source = answer["source"].strip()
            t_s = question["deposit_window_s"][0]
        else:
            return {"rejected": "needs 'choice' (an offered candidate) or 'source' (the operator's words)"}
        return {"label": label, "source": source, "location": question["cell"], "phase": question["memory_phase"],
                "part": question["part"], "note": _note(question["id"], answer), "t_s": t_s}
    if question["kind"] == "destination_of_removal":
        destination = str(answer.get("destination", "")).strip()
        if not destination:
            return {"rejected": "needs 'destination' (cell-N or a place name)"}
        return {"label": label, "source": f"removal {question['removal_event']} ({_sites_text(question['sources'])})",
                "location": destination, "phase": question["memory_phase"], "part": None,
                "note": _note(question["id"], answer), "t_s": question["window_s"][0]}
    return {"rejected": f"unsupported question kind {question['kind']!r}"}


def _note(question_id: str, answer: dict) -> str:
    replayed = (answer.get("replayed_from") or {}).get("question")
    note = f"answer to {question_id}" + (f" (replayed from earlier run's {replayed})" if replayed else "")
    return note + (f"; {answer['provenance']}" if answer.get("provenance") else "")


def _memory(session: Path, *argv) -> dict:
    done = subprocess.run([sys.executable, str(REPO / "assembly-memory" / "memory.py"), "--session-dir", str(session), *map(str, argv)],
                          capture_output=True, text=True, encoding="utf-8", env=CHILD_ENV)
    if done.returncode != 0:
        raise SystemExit(done.stderr or done.stdout)
    return json.loads(done.stdout)


def apply(answers: list[dict], questions: dict, session: Path, declared_by: str) -> list[dict]:
    results, by_part = [], {}
    for answer in answers:
        action = plan(answer, questions)
        if "rejected" in action or "dismissed" in action:
            results.append({"question": answer.get("question"), **action})
            continue
        removal = _memory(session, "record", "declared-removal", "--label", action["label"],
                          *([] if action["source"] is None else ["--source", action["source"]]), "--phase", action["phase"], "--declared-by", declared_by, "--note", action["note"],
                          *([] if action.get("t_s") is None else ["--t-s", action["t_s"]]))
        stored = _memory(session, "record", "storage-link", "--target", removal["id"], "--location", action["location"],
                         "--confirmed-by", declared_by, "--note", action["note"])
        if action["part"]:
            by_part[action["part"]] = removal["id"]
        results.append({"question": answer["question"], "declared_removal": removal["id"], "storage_link": stored["id"],
                        "identical_to": answer.get("identical_to") or []})
    for result in results:
        mates = [by_part[p] for p in result.get("identical_to", []) if p in by_part]
        if mates:
            members = [result["declared_removal"], *mates]
            group = _memory(session, "record", "group", "--members", ",".join(members), "--declared-by", declared_by,
                            "--note", f"identical per operator answer to {result['question']}")
            result["group"] = group["id"]
    return results


def package(questions: dict, out: Path) -> Path:
    """Write the question folder the guide (Muse) is started in: questions.json and QUESTION_RULES.md."""
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for qid, q in questions.items():
        if q["kind"] == "extra":
            continue
        item = {"id": qid, "kind": q["kind"], "ask": q["ask"], "part": q.get("part"), "cell": q.get("cell"),
                "window_s": q.get("deposit_window_s") or q.get("window_s")}
        if q["kind"] == "source_of_part":
            item["candidates"] = [{"letter": chr(ord("A") + i), "removal_event": c["removal_event"], "window_s": c["window_s"],
                                   "positions_mm": [s.get("centroid_mm") for s in c["sources"]]}
                                  for i, c in enumerate(q["candidates"])]
        else:
            item["removed_at_mm"] = [s.get("centroid_mm") for s in q["sources"]]
        items.append(item)
    phases = sorted(next((q["phases"] for q in questions.values() if q["kind"] == "extra"), {}))
    data = json.dumps({"schema": "anastylos-open-questions@1", "phases": phases, "questions": items}, indent=2, ensure_ascii=False) + chr(10)
    (out / "questions.json").write_bytes(data.encode("utf-8"))
    (out / "QUESTION_RULES.md").write_bytes((Path(__file__).with_name("QUESTION_RULES.md")).read_bytes())
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("package", help="write the question folder for the guide")
    pack.add_argument("--runs", required=True, nargs="+", type=Path)
    pack.add_argument("--out", required=True, type=Path)
    app = sub.add_parser("apply", help="write valid answers into memory")
    app.add_argument("--runs", required=True, nargs="+", type=Path, help="session run folders (parts.json, questions.json)")
    app.add_argument("--answers", required=True, type=Path, help="JSON lines, one answer per line")
    app.add_argument("--memory", required=True, type=Path, help="assembly-memory session folder of these runs")
    app.add_argument("--declared-by", required=True)
    rem = sub.add_parser("remap", help="carry answers from an earlier run onto rebuilt runs of the same videos")
    rem.add_argument("--old-runs", required=True, nargs="+", type=Path)
    rem.add_argument("--runs", required=True, nargs="+", type=Path, help="rebuilt session run folders")
    rem.add_argument("--answers", required=True, type=Path)
    rem.add_argument("--out", required=True, type=Path, help="replayed answers (JSON lines)")
    rem.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    questions, _ = load_runs(args.runs)
    if args.command == "remap":
        answers = [json.loads(line) for line in args.answers.read_text(encoding="utf-8").splitlines() if line.strip()]
        replayed, report = remap(answers, load_runs(args.old_runs)[0], questions)
        args.out.write_bytes("".join(json.dumps(a, ensure_ascii=False) + chr(10) for a in replayed).encode("utf-8"))
        args.report.write_bytes((json.dumps(report, indent=2, ensure_ascii=False) + chr(10)).encode("utf-8"))
        print(f"{len(replayed)} replayed; " + ", ".join(f"{k} {sum(r['outcome'] == k for r in report)}"
                                                       for k in sorted({r['outcome'] for r in report})))
        return
    if args.command == "package":
        print(f"{len(questions)} questions -> {package(questions, args.out)}")
        return
    answers = [json.loads(line) for line in args.answers.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = apply(answers, questions, args.memory, args.declared_by)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    open_ids = sorted(set(questions) - {"EXTRA"} - {r["question"] for r in results if "declared_removal" in r or "dismissed" in r})
    print(f"applied {sum('declared_removal' in r for r in results)}, dismissed {sum('dismissed' in r for r in results)}, "
          f"rejected {sum('rejected' in r for r in results)}, "
          f"still open: {open_ids}")


if __name__ == "__main__":
    main()
