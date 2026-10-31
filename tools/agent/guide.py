"""The session's reassembly guide: build the package, bind everything to its SHA-256, answer from it in English.

``guide build`` runs the existing ``tools/guide/package.py`` (read-only, by subprocess) on the session memory and
appends a build record to ``guide/builds.jsonl`` (append-only, fsync): the package SHA-256 that every answer
and cached proposal is bound to (F6), and the memory SHA-256 it was built from. A package whose bytes differ
from the last recorded build is refused. The current memory must match that build before cache or plan use.

Answers are claims (``anastylos-guide-claims@1``) checked by ``tools/guide/claims.py`` against the exact package
and rendered by ``guide_render.py``. The deterministic path (L0) maps a small set of English questions to claims;
Muse (optional) proposes claims for other questions within ``MUSE_TIMEOUT_S``, else the L0 refusal stands.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import guide_render as en
from state import SessionState, t_wall

REPO = Path(__file__).resolve().parents[2]
GUIDE_TOOLS = REPO / "tools" / "guide"
if str(GUIDE_TOOLS) not in sys.path:
    sys.path.insert(0, str(GUIDE_TOOLS))
import claims  # noqa: E402  (lane B verifier, used unchanged)

PACKAGE_BUILDER = GUIDE_TOOLS / "package.py"
BUILD_SCHEMA = "anastylos-agent-guide-build@1"
ANSWER_SCHEMA = "anastylos-agent-guide-answer@1"
MUSE_TIMEOUT_S = 60.0  # per question (contract 0.3 latency budget); after it the L0 text is used
HELP = ("I can only answer from this session's package. Ask, for example: “where is cell 8”, "
        "“where does dec-0013 go”, “what comes before dec-0007”.")


class GuideError(Exception):
    """The guide cannot answer from a trusted package; the message is the English text for the operator."""


def guide_dir(state: SessionState) -> Path:
    return state.root / "guide"


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------------------ build and load

def build(state: SessionState) -> dict:
    """Build the package from the session memory and record its hash durably."""
    memory = state.memory_dir / "memory.jsonl"
    out = guide_dir(state)
    done = subprocess.run([sys.executable, str(PACKAGE_BUILDER), "--memory", str(memory), "--out", str(out)],
                          capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    package = out / "session.json"
    if done.returncode != 0 or not package.is_file():
        raise GuideError(f"Building the guide package failed: {(done.stderr or done.stdout).strip()[-400:]}")
    record = {"schema": BUILD_SCHEMA, "t_wall": t_wall(), "package": str(package), "package_sha256": _sha(package),
              "memory_sha256": _sha(memory), "builder": "tools/guide/package.py", "builder_sha256": _sha(PACKAGE_BUILDER)}
    _append(out / "builds.jsonl", record)
    return record


def recorded_sha256(state: SessionState) -> str | None:
    builds = _records(guide_dir(state) / "builds.jsonl")
    return builds[-1]["package_sha256"] if builds else None


def load(state: SessionState):
    """The intact package from the current memory, or GuideError with an English rebuild instruction."""
    path = guide_dir(state) / "session.json"
    if not path.is_file():
        raise GuideError("There is no guide package yet; run `guide build` first.")
    builds = _records(guide_dir(state) / "builds.jsonl")
    last = builds[-1] if builds else {}
    package, problem = claims.load_package(path.read_bytes(), last.get("package_sha256"))
    if problem:
        reason = ("it changed after it was built" if "differs from the hash" in problem
                  else "no build record" if "no build hash" in problem else "unsupported package layout")
        raise GuideError(en.UNCHECKABLE.format(reason=reason))
    memory = state.memory_dir / "memory.jsonl"
    if not memory.is_file() or _sha(memory) != last.get("memory_sha256"):
        raise GuideError("The guide is stale because the session memory changed since its build; run `guide build` again.")
    return package, json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ deterministic English questions (L0)

_WHERE_IS = re.compile(r"^(?:where(?:'s| is| are)|where did (?:i|you) put)\s+(?P<x>.+)$", re.IGNORECASE)
_GOES = re.compile(r"^(?:where (?:does|do) (?P<x>.+?) (?:go(?: back)?|belong)|where did (?P<y>.+?) come from)$", re.IGNORECASE)
_BEFORE = re.compile(r"^what (?:comes|goes|is installed) (?:before|ahead of) (?P<x>.+)$", re.IGNORECASE)
_CELL = re.compile(r"^(?:the )?(?:part (?:in|from) )?(?:the )?(?:cell|tray cell)[ -]?(\d{1,2})$", re.IGNORECASE)


def normalize(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().rstrip("?.! ")).strip()


def find(target: str, package) -> list[str]:
    """Citable ids the operator's words name: an id, a cell, or an operator label (exact, then contained)."""
    text = re.sub(r"^(?:the|my|a)\s+", "", target.strip(), flags=re.IGNORECASE)
    if text in package.aliases:
        return [text]
    cell = _CELL.match(text)
    if cell:
        parts = [p for p in package.parts.values() if p.cell == str(int(cell.group(1)))]
        groups = {p.group for p in parts}
        return [parts[0].group] if len(parts) > 1 and len(groups) == 1 and None not in groups else [p.id for p in parts]
    key = text.casefold()
    exact = [p.id for p in package.parts.values() if p.label and p.label.casefold() == key]
    hits = exact or [p.id for p in package.parts.values() if p.label and key in p.label.casefold()]
    groups = {package.parts[h].group for h in hits}
    if len(hits) > 1 and len(groups) == 1 and None not in groups:
        return [package.parts[hits[0]].group]
    return hits


def citable(part_ids: list[str], package) -> str:
    """One install step's citable id: the part, or the group when the step is a group of interchangeable parts."""
    if len(part_ids) == 1:
        return part_ids[0]
    groups = {package.parts[p].group for p in part_ids}
    if len(groups) != 1 or None in groups:
        raise GuideError(f"An install step lists parts that are not one group: {', '.join(part_ids)}.")
    return groups.pop()


def _basis_word(citable: str, package) -> str:
    parts = en.resolve(citable, package)
    return claims.BY_OPERATOR if all(p.basis == "operator_declared" for p in parts) else claims.SYSTEM_ASSIGNED


def l0_claims(question: str, package) -> tuple[dict | None, str | None]:
    """A claims document for a supported English question, or (None, English reason)."""
    q = normalize(question)
    match = _GOES.match(q) or _BEFORE.match(q) or _WHERE_IS.match(q)
    if not match:
        return None, HELP
    target = match.groupdict().get("x") or match.groupdict().get("y")
    found = find(target, package)
    if not found:
        return None, f"I could not find {en.q(target)} in this session's package. {HELP}"
    items = []
    for citable in found:
        if match.re is _GOES:
            group = citable.startswith("grp-")
            items.append({"relation": "removed_from", "subject": citable, "object": None,
                          "basis_word": claims.GROUP_STATEMENT if group else _basis_word(citable, package)})
        elif match.re is _BEFORE:
            step = en.resolve(citable, package)[0].step
            before = [p for p in package.parts.values() if step is not None and p.step == step - 1]
            if not before:
                return None, f"Nothing comes before {en.name(citable, package)} in the install order."
            first = before[0].group if len(before) > 1 else before[0].id
            items.append({"relation": "install_before", "subject": first, "object": citable,
                          "basis_word": claims.REVERSE_ORDER})
        else:
            items.append({"relation": "stored_in", "subject": citable, "object": en.resolve(citable, package)[0].location,
                          "basis_word": _basis_word(citable, package)})
    return {"schema": claims.CLAIMS_SCHEMA, "claims": items, "insufficient_evidence": False}, None


# ------------------------------------------------------------------ answers

def minimized_package(raw: dict) -> dict:
    """What Muse may see: ids, labels, phases, cells, bases and order; no owner names, notes or evidence prose."""
    phase = {p["id"]: p["label"] for p in raw["phases"]}
    return {"allowed_basis_words": list(claims.BASIS_WORDS),
            "parts": [{"id": p["id"], "operator_label": p.get("operator_label"), "phase": phase.get(p.get("phase")),
                       "removal_index": p.get("removal_index"), "basis": p.get("basis"),
                       "group": (p.get("group") or {}).get("id"),
                       "stored_in": (p.get("stored_in") or {}).get("location")} for p in raw["parts"]],
            "install_order": [{"step": s["step"], "parts": s["parts"]} for s in raw["install_order"]["steps"]],
            "dependencies": raw["dependencies"]}


def _propose_with_timeout(proposer, payload: dict, request_id: str, timeout_s: float) -> dict | None:
    box: dict = {}

    def call():
        try:
            box["result"] = proposer.propose("guide_claims", payload, request_id)
        except Exception as exc:  # a provider bug must not take the guide down: L0 stays
            box["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(timeout_s)
    return box.get("result") if not worker.is_alive() else None


def answer(state: SessionState, question: str, proposer=None, timeout_s: float = MUSE_TIMEOUT_S) -> dict:
    """English answer bound to the package hash; every factual sentence comes from accepted claims."""
    try:
        package, raw = load(state)
    except GuideError as error:
        return {"text": str(error), "source": "refusal", "accepted": False, "package_sha256": None}
    log = guide_dir(state) / "answers.jsonl"
    key = normalize(question).casefold()
    for record in reversed(_records(log)):
        if record["question_key"] == key and record["package_sha256"] == package.sha256 and record["accepted"]:
            return {**record, "source": "cache"}
    document, reason = l0_claims(question, package)
    source, findings = "L0", []
    if document is None and proposer is not None:
        payload = {"package_sha256": package.sha256, "operator_question": question[:500],
                   "package_excerpt": minimized_package(raw)}
        result = _propose_with_timeout(proposer, payload, f"guide:{package.sha256[:12]}:{len(_records(log)) + 1}", timeout_s)
        if result is None:
            findings.append(f"model: no answer within {timeout_s:.0f} s")
        elif result.get("status") != "ok":
            findings.append(f"model: status {result.get('status')}")
        else:
            document, source = result["output"], "L1"
    if document is None:
        record = {"text": reason, "source": "refusal", "accepted": False, "claims": [], "findings": findings}
    else:
        verdict = claims.verify(document, package)
        text = en.render(document["claims"], package, document["insufficient_evidence"]) if verdict.accepted else en.NOT_PROVEN
        record = {"text": text, "source": source, "accepted": verdict.accepted, "claims": verdict.claims,
                  "findings": findings + verdict.findings}
    record = {"schema": ANSWER_SCHEMA, "t_wall": t_wall(), "question": question, "question_key": key,
              "package_sha256": package.sha256, **record}
    _append(log, record)
    return record
