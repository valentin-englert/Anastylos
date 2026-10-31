"""TL vertical slices across lanes A, B, C, D (CONTRACT 0.2), each on fresh memory / package copies.

    python tools/live/integration/slices.py --out C:\\ANASTYLOS\\data\\live-dev\\TL\\<run> [--offline]

S1 L0: question -> typed answer -> read-back -> "yes" -> one controlled write (no provider).
S2 L1: Muse wording + interpretation proposals -> gates -> accepted or L0 fallback -> read-back -> write.
S3 L2: Muse next_action proposals -> l2sim validator -> executed action or P0 fallback.
S4 guide: Muse guide_claims -> claims.check -> rendered speech (and the same claims against a wrong hash).

`--offline` runs the same wiring with no key (status `disabled`), which must give the L0/P0 path.
Writes `slices.json` beside the run; never touches the active FULL-01 memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parents[1]
REPO = LIVE.parents[1]
for _path in (LIVE, REPO / "tools" / "guide"):
    sys.path.insert(0, str(_path))

import wiring  # noqa: E402  (sets the providers path)
from meta import MetaProposer  # noqa: E402

FIXTURE = LIVE / "tests" / "fixtures"
PHASE_WORDS = FIXTURE / "phase-words.en.json"  # English product language
PACKAGE = REPO / "tools" / "guide" / "guide02" / "fixtures" / "session.v21.json"
SLICE_CALL_CAP = 4
OPERATOR = "operator (TL slice)"


def operator_input(op_key: str, text: str) -> dict:
    from journal import utc_now
    return {"schema": "anastylos-live-operator-input@1", "op_key": op_key, "source": "typed", "text": text,
            "choice": None, "received_at": utc_now(), "transcript": None}


def memory_copy(out: Path) -> tuple[list[Path], Path]:
    runs = []
    for tag in ("P1", "P2", "P3"):
        run = out / "runs" / tag
        run.mkdir(parents=True)
        for name in ("questions.json", "parts.json"):
            shutil.copyfile(FIXTURE / tag / name, run / name)
        runs.append(run)
    memory = out / "memory"
    memory.mkdir()
    shutil.copyfile(FIXTURE / "audit" / "memory.before-answers.jsonl", memory / "memory.jsonl")
    return runs, memory


def line_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def provider(out: Path, offline: bool) -> MetaProposer:
    return MetaProposer(out / "provider", max_calls=SLICE_CALL_CAP, environ={} if offline else None)


def provider_summary(journal_records: list[dict]) -> list[dict]:
    calls = []
    for r in journal_records:
        if r["kind"] == "provider_call":
            res = r["result"]
            calls.append({"task": r["task"], "status": res["status"], "latency_s": res["latency_s"],
                          "usage": res["usage"], "model": res["model"],
                          "effort": res["reasoning_effort_returned"], "cache_hit": res["cache_hit"]})
        elif r["kind"] == "fallback":
            calls.append({"task": r["task"], "fallback": r["reason"]})
    return calls


def question_slice(out: Path, proposer, answer_text: str) -> dict:
    """S1 (proposer None) and S2: one question through ask, answer, read-back and confirmation."""
    from coordinator import Coordinator
    runs, memory = memory_copy(out)
    phase_words = json.loads(PHASE_WORDS.read_text(encoding="utf-8"))["phases"]
    coordinator = Coordinator(out, memory, runs, phase_words, declared_by=OPERATOR, proposer=proposer,
                              provider_call_cap=SLICE_CALL_CAP).open()
    try:
        if coordinator.stopped:
            return {"ok": False, "stopped": coordinator.stopped}
        qid = coordinator.book.live_order()[0]
        before = line_count(memory / "memory.jsonl")
        prompt = coordinator.ask(qid)
        reply = coordinator.receive(operator_input(prompt["op_key"], answer_text))
        after_answer = line_count(memory / "memory.jsonl")
        confirm = coordinator.receive(operator_input(prompt["op_key"], "yes")) if reply["status"] == "read_back" else None
        after = line_count(memory / "memory.jsonl")
        summary = coordinator.summary()
        return {
            "question": qid, "wording": prompt["wording"], "prompt": prompt["text"],
            "rubric_ok": all(prompt["rubric"].values()) if isinstance(prompt["rubric"], dict) else prompt["rubric"],
            "answer_status": reply["status"], "read_back": reply.get("read_back"),
            "memory_lines": {"before": before, "after_answer": after_answer, "after_confirm": after},
            "no_write_before_confirm": after_answer == before,
            "confirm_status": confirm and confirm["status"], "events": confirm and confirm.get("events"),
            "census": summary["census"], "provider": provider_summary(coordinator.journal.records),
            "ok": bool(confirm) and confirm["status"] == "committed" and after_answer == before and after > before,
        }
    finally:
        coordinator.close()


def l2_slice(out: Path, offline: bool) -> dict:
    """S3 in its own process: l2sim's `session` module shares its name with tools/session/session.py."""
    args = [sys.executable, str(Path(__file__).with_name("l2_slice.py")), "--out", str(out), "--cap", str(SLICE_CALL_CAP)]
    done = subprocess.run(args + (["--offline"] if offline else []), capture_output=True, text=True, encoding="utf-8")
    if done.returncode not in (0, 1):
        return {"ok": False, "error": done.stderr[-1500:]}
    return json.loads((out / "l2_slice.json").read_text(encoding="utf-8"))


def minimized_package(package: dict, basis_words) -> dict:
    """Ids, labels, phases, cells, bases and order only: no owner names, notes or evidence prose.

    `basis_words` lists the only words claims.py accepts; the claims schema does not enumerate them (0.3).
    """
    phase = {p["id"]: p["label"] for p in package["phases"]}
    return {
        "allowed_basis_words": list(basis_words),
        "parts": [{"id": p["id"], "operator_label": p.get("operator_label"), "phase": phase.get(p.get("phase")),
                   "removal_index": p.get("removal_index"), "basis": p.get("basis"),
                   "stored_in": (p.get("stored_in") or {}).get("location")} for p in package["parts"]],
        "install_order": package["install_order"],
        "dependencies": package["dependencies"],
    }


def guide_slice(out: Path, proposer) -> dict:
    """S4: Muse claims about the v21 package, verified and rendered by claims.py."""
    import claims
    raw = PACKAGE.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    payload = {"package_sha256": sha, "operator_question": "Where is the first screw from P1, and what do I install first?",
               "package_excerpt": minimized_package(json.loads(raw), claims.BASIS_WORDS)}
    result = proposer.propose("guide_claims", payload, "TL-slice-S4:guide_claims:1")
    if result["status"] != "ok":
        speech = claims.NOT_PROVEN
        return {"provider_status": result["status"], "speech": speech, "fallback": True,
                "ok": result["status"] in ("disabled", "budget_exhausted", "timeout", "provider_error")}
    verdict = claims.check(raw, sha, document=result["output"])
    tampered = claims.check(raw, "0" * 64, document=result["output"])
    return {"provider_status": "ok", "latency_s": result["latency_s"], "usage": result["usage"],
            "claims": result["output"], "verdict": verdict.as_json(),
            "wrong_hash_rejected": not tampered.accepted,
            "ok": (not tampered.accepted) and (verdict.accepted or verdict.speech == claims.NOT_PROVEN)}


SLICES = {"S1_L0": lambda out, offline, answer: question_slice(out / "S1", None, answer),
          "S2_L1": lambda out, offline, answer: question_slice(out / "S2", provider(out / "S2", offline), answer),
          "S3_L2": lambda out, offline, answer: l2_slice(out / "S3", offline),
          "S4_guide": lambda out, offline, answer: guide_slice(out / "S4", provider(out / "S4", offline))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="no key: every provider call is disabled")
    parser.add_argument("--answer", default="Screw from the top-left hole of the back plate")
    parser.add_argument("--only", default=",".join(SLICES), help="comma-separated subset of " + ",".join(SLICES))
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if REPO in out.parents or out.exists():
        print(f"--out must be a new folder outside the repository: {out}", file=sys.stderr)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    out.mkdir(parents=True)
    names = args.only.split(",")
    report = {"schema": "anastylos-live-slices@1", "offline": args.offline}
    report |= {name: SLICES[name](out, args.offline, args.answer) for name in names}
    (out / "slices.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for name in names:
        print(f"{name}: {'OK' if report[name]['ok'] else 'FAIL'}")
    return 0 if all(report[n]["ok"] for n in names) else 1


if __name__ == "__main__":
    raise SystemExit(main())
