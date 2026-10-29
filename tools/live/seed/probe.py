"""Exactly-once probes against the unchanged answers.py / memory CLI, on scratch copies only.

X1 clean apply; X2 crash after the n-th memory call, then a naive restart; X3 the same answers applied twice;
X4 torn last line; X5 session.py retry after a crash between observation import and tray link;
X6 concurrent writers.
"""

from __future__ import annotations

import collections
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(r"C:\Users\HP\Documents\Anastylos")
V21 = Path(r"C:\ANASTYLOS\data\session-runs-v21\FULL-01")
HERE = Path(__file__).parent / "work"
sys.path.insert(0, str(REPO / "tools" / "session"))
import answers  # noqa: E402  (production module, unchanged)

RUNS = [V21 / p for p in ("P1", "P2", "P3")]
ANSWERS = [json.loads(l) for l in (V21 / "audit" / "apply_input.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
WHO = "probe (not operator)"


def fresh(name: str) -> Path:
    d = HERE / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    shutil.copyfile(V21 / "audit" / "memory.before-answers.jsonl", d / "memory.jsonl")
    return d


def events(d: Path) -> list[dict]:
    return [json.loads(l) for l in (d / "memory.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def census(d: Path) -> dict:
    ev = events(d)
    decl = [e for e in ev if e["type"] == "declared_removal"]
    stored = {e["target_id"] for e in ev if e["type"] == "storage_link"}
    notes = collections.Counter(e.get("note") for e in decl)
    return {"events": len(ev), "declared_removal": len(decl), "storage_link": len(stored),
            "orphan_declared_without_storage": sorted(e["id"] for e in decl if e["id"] not in stored),
            "same_answer_written_twice": sum(c - 1 for c in notes.values() if c > 1)}


def apply(d: Path):
    questions, _ = answers.load_runs(RUNS)
    return answers.apply(ANSWERS, questions, d, WHO)


def x2_crash_then_restart(after_calls: int) -> dict:
    d = fresh(f"x2-{after_calls}")
    real, calls = answers._memory, {"n": 0}

    def crashing(session, *argv):
        if calls["n"] == after_calls:
            raise SystemExit("simulated crash")
        calls["n"] += 1
        return real(session, *argv)

    answers._memory = crashing
    try:
        apply(d)
    except SystemExit:
        pass
    finally:
        answers._memory = real
    after_crash = census(d)
    apply(d)  # naive restart: the same answer file again
    return {"crash_after_memory_calls": after_calls, "after_crash": after_crash, "after_restart": census(d)}


def x4_torn_line() -> dict:
    d = fresh("x4")
    with (d / "memory.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"type": "declared_removal", "id": "dec-0099", "sess')  # process died mid-write
    done = subprocess.run([sys.executable, "-B", str(REPO / "assembly-memory" / "memory.py"), "--session-dir", str(d),
                           "record", "phase", "--label", "after crash"], capture_output=True, text=True, encoding="utf-8")
    return {"next_write_exit": done.returncode, "stderr_tail": done.stderr.strip().splitlines()[-1:]}


def x5_import_then_crash_before_tray_link() -> dict:
    d = fresh("x5")
    ev = events(d)
    link = next(e for e in ev if e["type"] == "tray_link")
    obs = next(e for e in ev if e["id"] == link["observation_id"])
    # state after a crash between `import` and `record tray-link`: drop the link line
    (d / "memory.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ev if e["id"] != link["id"]),
                                    encoding="utf-8")
    done = subprocess.run([sys.executable, "-B", str(REPO / "assembly-memory" / "memory.py"), "--session-dir", str(d),
                           "import", "--result-json", obs["evidence"]["result_json"], "--region", obs["evidence"]["region"]],
                          capture_output=True, text=True, encoding="utf-8")
    out = json.loads(done.stdout)
    return {"observation": obs["id"], "retry_import_imported": [e["id"] for e in out["imported"]], "retry_import_skipped": out["skipped"],
            "session_py_consequence": "imported is empty -> session.py:139-141 marks the part 'not written' and never records the tray link"}


def x6_concurrent(writers: int = 12) -> dict:
    d = fresh("x6")
    cmd = [sys.executable, "-B", str(REPO / "assembly-memory" / "memory.py"), "--session-dir", str(d), "record", "phase", "--label"]
    with ThreadPoolExecutor(max_workers=writers) as pool:
        results = list(pool.map(lambda i: subprocess.run(cmd + [f"w{i}"], capture_output=True, text=True), range(writers)))
    ids = collections.Counter(json.loads(l)["id"] for l in (d / "memory.jsonl").read_text(encoding="utf-8").splitlines()
                              if l.strip() and json.loads(l)["type"] == "phase")
    reread = subprocess.run([sys.executable, "-B", str(REPO / "assembly-memory" / "memory.py"), "--session-dir", str(d),
                             "where", "--tray", "3", "--sheet", "surface_a3"], capture_output=True, text=True)
    return {"writers": writers, "writer_failures": sum(r.returncode != 0 for r in results),
            "duplicate_ids": {k: v for k, v in ids.items() if v > 1}, "reader_exit_after": reread.returncode,
            "reader_error": reread.stderr.strip().splitlines()[-1:] if reread.returncode else []}


def main() -> None:
    report = {}
    d = fresh("x1"); apply(d); report["X1_clean"] = census(d)
    report["X2_crash"] = [x2_crash_then_restart(n) for n in (1, 5)]
    d = fresh("x3"); apply(d); apply(d); report["X3_applied_twice"] = census(d)
    report["X4_torn_line"] = x4_torn_line()
    report["X5_import_retry"] = x5_import_then_crash_before_tray_link()
    report["X6_concurrent"] = x6_concurrent()
    (Path(__file__).parent / "probe.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
