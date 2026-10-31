"""Selected single-part guide answers; no shared provider/claim contract changes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import guide
import selection
from state import SessionError

CACHE_SCHEMA = "anastylos-selected-answer-cache@1"
QUESTIONS = {"origin": "State only the recorded origin of the selected part. Use unknown and insufficient_evidence if absent.",
             "storage": "State only the recorded storage location of the selected part. Use unknown and insufficient_evidence if absent."}


def context(resolved: dict) -> dict:
    origin, storage = resolved["original_location"], resolved["stored_location"]
    return {"schema": "anastylos-selected-part-context@2", "id": resolved["event_id"],
            "memory_sha256": resolved["memory_sha256"], "operator_label": resolved["operator_label"],
            "origin": {"description": origin["value"], "basis": origin["basis"],
                       "reference_ids": [origin["record_id"]] + ([origin["observation_id"]] if origin["observation_id"] else []),
                       "precision": "operator_description_only" if origin["value"] else None},
            "storage": {"value": storage["value"], "basis": (storage["basis"] or {}).get("kind"),
                        "reference_ids": [storage["record_id"]] if storage["record_id"] else []},
            "image_evidence": "not_supplied", "frame_record_confirmation": "not_established"}


def _document(resolved: dict, intent: str) -> dict:
    value = resolved["original_location" if intent == "origin" else "stored_location"]["value"]
    claim = {"relation": "removed_from" if intent == "origin" else "stored_in", "subject": resolved["event_id"],
             "object": None if intent == "origin" else value, "basis_word": guide.claims.BY_OPERATOR}
    if not value:
        claim = {"relation": "unknown", "subject": resolved["event_id"], "object": None, "basis_word": None}
    return {"schema": guide.claims.CLAIMS_SCHEMA, "claims": [claim], "insufficient_evidence": not bool(value)}


def _checked(document: dict, package, resolved: dict, intent: str) -> dict:
    verdict = guide.claims.verify(document, package)
    if not verdict.accepted:
        return {"accepted": False, "outcome": "verifier_rejection", "text": guide.en.NOT_PROVEN,
                "claims": verdict.claims, "findings": verdict.findings}
    relation = "removed_from" if intent == "origin" else "stored_in"
    value = resolved["original_location" if intent == "origin" else "stored_location"]["value"]
    problems = []
    for c in document["claims"]:
        if c["subject"] != resolved["event_id"]:
            problems.append("Claim redirects the selected part or uses a reference alias.")
        if c["relation"] not in (relation, "unknown"):
            problems.append("This relation is unsupported for the selected intent.")
        if c["relation"] == relation and (not value or (intent == "origin" and c["object"] is not None)):
            problems.append("A positive origin/storage claim needs a stored value; origin must quote the complete local description.")
        if c["relation"] == "unknown" and (c["object"] is not None or c["basis_word"] is not None):
            problems.append("Unknown must not invent a target or basis.")
    abstention = bool(document["claims"]) and all(c["relation"] == "unknown" for c in document["claims"])
    if not document["claims"] or document["insufficient_evidence"] != abstention:
        problems.append("Use either supported positive claims or explicit unknown with insufficient_evidence.")
    if problems:
        return {"accepted": False, "outcome": "selection_rejection", "text": guide.en.NOT_PROVEN,
                "claims": verdict.claims, "findings": problems}
    return {"accepted": True, "outcome": "abstention" if abstention else "supported",
            "text": guide.en.render(document["claims"], package, abstention), "claims": verdict.claims, "findings": []}


class _SafeProposal:
    def __init__(self, delegate):
        self.delegate = delegate
        self.max_call_seconds = getattr(delegate, "max_call_seconds", guide.PROVIDER_WORK_SECONDS)

    def propose(self, *args):
        try:
            return self.delegate.propose(*args)
        except Exception:
            return {"status": "provider_error"}


def answer(memory: Path, package_path: Path, binding: dict, intent: str, proposer=None,
           cache_path: Path | None = None, timeout_s: float = guide.MUSE_TIMEOUT_S, provider_source: str = "Muse") -> dict:
    if intent not in QUESTIONS:
        raise SessionError("Only recorded origin and storage questions are supported for a selected part.")
    resolved = selection.resolve(memory, package_path, binding)
    package, problem = guide.claims.load_package(Path(package_path).read_bytes(), binding["guide_sha256"])
    if problem:
        raise selection.SelectionError("Guide changed before selected answer preparation.")
    payload = {"package_sha256": package.sha256, "operator_question": QUESTIONS[intent],
               "package_excerpt": {"selected_part": context(resolved), "allowed_basis_words": list(guide.claims.BASIS_WORDS)}}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    records = []
    if cache_path is not None:
        cache_path = Path(cache_path)
        if cache_path.resolve() in (Path(memory).resolve(), Path(package_path).resolve()):
            raise SessionError("Selected cache must not overwrite an input file.")
        records = guide._records(cache_path)
        if any(r.get("schema") != CACHE_SCHEMA for r in records):
            raise SessionError("Selected cache cannot reuse an unselected answer log.")
    document, source, provider_status = None, "L0", None
    provider_cache_hit = False
    cached = next((r for r in reversed(records) if r.get("key") == key), None)
    if cached:
        document, source = cached["document"], "cache"
    elif proposer is not None:
        result = guide._propose_with_timeout(_SafeProposal(proposer), payload, f"selected:{key}", timeout_s)
        try:
            provider_status = result.get("status") if result else "answer_timeout"
            if provider_status == "ok":
                document, source = result["output"], provider_source
                provider_cache_hit = bool(result.get("cache_hit"))
                if provider_cache_hit:
                    source = "provider_cache"
        finally:
            guide.wait_for_provider_work()
    # Revalidate before rendering or using any cached/model answer, including after late work.
    selection.resolve(memory, package_path, binding)
    if document is None:
        document = _document(resolved, intent)
    checked = _checked(document, package, resolved, intent)
    if checked["outcome"] == "abstention" and source == "Muse":
        checked["outcome"] = "model_abstention"
    if cached:
        checked["original_source"] = cached["source"]
    if provider_status and provider_status != "ok":
        checked["fallback_outcome"] = checked["outcome"]
        checked["outcome"] = "answer_timeout" if provider_status == "answer_timeout" else "provider_error"
    if source not in ("cache", "L0") and checked["accepted"] and cache_path is not None:
        guide._append(cache_path, {"schema": CACHE_SCHEMA, "key": key, "document": document, "source": source})
    return {"schema": "anastylos-selected-answer@1", "event_id": resolved["event_id"],
            "drawing_element_id": resolved["drawing_element_id"], "guide_sha256": package.sha256,
            "memory_sha256": resolved["memory_sha256"], "context_sha256": key, "intent": intent,
            "source": source, "provider_status": provider_status, "provider_cache_hit": provider_cache_hit,
            "proposal": document if source != "L0" else None, **checked,
            "limits": ["Descriptions are quoted operator data, not a precise installation point or orientation.",
                       "No images supplied; frame-to-record correspondence remains unconfirmed."]}
