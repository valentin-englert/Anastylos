"""Guided reassembly: one install step at a time from the verified package, progress that survives restarts.

Each step's instruction is rendered only from claims ``tools/guide/claims.py`` accepted (where the part is,
where it goes back, what came before it). A group of interchangeable parts is one step and never gets a
part-to-hole assignment. Progress is an append-only log bound to the package SHA-256: ``done`` comes from the
operator, ``verified`` / ``verification_failed`` from a return check (``tools/reassembly/returns.py`` output).
A failed check reopens its step; nothing is skipped or repeated silently.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import guide
import guide_render as en
from state import SessionError, SessionState, t_wall

PROGRESS_SCHEMA = "anastylos-agent-assembly@1"
RETURNS_SCHEMA = "anastylos-reassembly-returns@1"
DONE, VERIFIED, FAILED = "done", "verified", "verification_failed"
STOP_WORDS = {"stop", "pause", "quit", "exit"}
DONE_WORDS = {"done", "installed", "finished", "ok done", "next"}
SKIP_WORDS = {"skip", "skip it", "later"}


def progress_path(state: SessionState) -> Path:
    return state.root / "assemble" / "progress.jsonl"


def steps(state: SessionState) -> tuple[list[dict], object]:
    """The install steps with their verified English instruction."""
    package, raw = guide.load(state)
    total = len(raw["install_order"]["steps"])
    out, previous = [], None
    for step in raw["install_order"]["steps"]:
        citable = guide.citable(step["parts"], package)
        items = [{"relation": "stored_in", "subject": citable, "object": en.resolve(citable, package)[0].location,
                  "basis_word": None},
                 {"relation": "removed_from", "subject": citable, "object": None,
                  "basis_word": guide.claims.GROUP_STATEMENT if citable.startswith("grp-") else None}]
        if previous:
            items.append({"relation": "install_before", "subject": previous, "object": citable,
                          "basis_word": guide.claims.REVERSE_ORDER})
        document = {"schema": guide.claims.CLAIMS_SCHEMA, "claims": items, "insufficient_evidence": False}
        verdict = guide.claims.verify(document, package)
        body = en.render(items, package) if verdict.accepted else en.NOT_PROVEN
        if verdict.accepted and citable.startswith("grp-"):
            body += " Put each one into any free place of the group; no part is matched to a particular place."
        out.append({"step": step["step"], "parts": step["parts"], "citable": citable, "accepted": verdict.accepted,
                    "claims": verdict.claims, "text": f"Step {step['step']} of {total}: {body}"})
        previous = citable
    return out, package


def _records(state: SessionState, sha: str) -> list[dict]:
    path = progress_path(state)
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r["package_sha256"] == sha]


def _append(state: SessionState, record: dict) -> None:
    guide._append(progress_path(state), {"schema": PROGRESS_SCHEMA, "t_wall": t_wall(), **record})


def status(state: SessionState) -> dict:
    """Per step: latest event (or None); the current step is the first not done and not verified."""
    plan, package = steps(state)
    latest: dict[int, dict] = {}
    for record in _records(state, package.sha256):
        latest[record["step"]] = record
    current = next((s for s in plan if (latest.get(s["step"]) or {}).get("event") not in (DONE, VERIFIED)), None)
    other = guide._records(progress_path(state))
    stale = sorted({r["package_sha256"][:12] for r in other if r["package_sha256"] != package.sha256})
    return {"package_sha256": package.sha256, "steps": plan, "latest": latest, "current": current,
            "complete": current is None, "progress_for_other_packages": stale}


def mark_done(state: SessionState, step: int) -> None:
    now = status(state)
    if now["current"] is None or now["current"]["step"] != step:
        raise SessionError(f"Step {step} is not the current step; nothing was recorded.")
    _append(state, {"package_sha256": now["package_sha256"], "step": step, "event": DONE, "basis": "operator"})


def apply_returns(state: SessionState, returns_path: Path) -> dict:
    """Mark steps of automatic parts by a return check: a mismatch reopens the step, no match changes nothing."""
    document = json.loads(Path(returns_path).read_text(encoding="utf-8"))
    if document.get("schema") != RETURNS_SCHEMA:
        raise SessionError(f"{returns_path} is not a {RETURNS_SCHEMA} file.")
    now = status(state)
    package = guide.load(state)[0]
    step_of_cell = {p.location: p.step for p in package.parts.values() if p.basis == "automatic" and p.location}
    sha = hashlib.sha256(Path(returns_path).read_bytes()).hexdigest()
    marked = {}
    for item in document["returns"]:
        cell = (item.get("departure") or {}).get("zone")
        step = step_of_cell.get(cell)
        check = item.get("memory_check")
        if step is None or check not in ("consistent_with_memory", "MISMATCH_with_memory"):
            continue  # ambiguous, unattributed, back in a cell or unknown home: nothing is claimed
        event = VERIFIED if check == "consistent_with_memory" else FAILED
        _append(state, {"package_sha256": now["package_sha256"], "step": step, "event": event, "basis": "returns",
                        "returns_sha256": sha, "return_id": item["id"], "status": item.get("status")})
        marked[step] = event
    return marked


def run(state: SessionState, operator) -> dict:
    """Show the current step until the operator says done; questions go to the guide; stop pauses."""
    while True:
        now = status(state)
        if now["current"] is None:
            operator.show("All steps are done. Reassembly is complete according to the recorded steps.")
            return now
        step = now["current"]
        failed = (now["latest"].get(step["step"]) or {}).get("event") == FAILED
        if failed:
            operator.show(f"Step {step['step']} needs attention: the return check found the part in a different place.")
        operator.show(step["text"])
        reply = operator.reply("assemble", f"step-{step['step']}", step["text"], {})
        text = guide.normalize(reply.get("text", "")).casefold()
        if text in STOP_WORDS:
            operator.show(f"Paused at step {step['step']}. Run `assemble` again to continue.")
            return status(state)
        if text in DONE_WORDS:
            mark_done(state, step["step"])
            continue
        if text in SKIP_WORDS:
            operator.show("Steps cannot be skipped. Say done when the part is installed, or stop to pause.")
            continue
        operator.show(guide.answer(state, reply.get("text", ""))["text"])
