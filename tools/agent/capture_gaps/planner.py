"""Deterministic proposals and evidence-bound operator closure, without I/O."""
import hashlib
import json
import re

CATALOG = {
    "outer_boundary": ("The complete outer boundary is not visible.",
        "Place the selected part alone on a contrasting matte surface. Show the entire outer rim, remove your hands, and leave space around every edge.",
        "The selected part's entire outer boundary is visible, unclipped and unobscured; shadows are excluded."),
    "missing_side": ("A required view side is missing.",
        "Show the requested side of the selected part alone, with the entire part in frame and your hands removed.",
        "The requested side of the selected part is visible and correctly identified."),
    "orientation": ("The part orientation is unconfirmed.",
        "Show the selected part and an asymmetric orientation landmark. Confirm its viewing side and the direction of the landmark.",
        "The viewing side and asymmetric landmark direction are explicitly confirmed."),
    "frame_identity": ("The frame-to-part assignment is unconfirmed.",
        "Review the cited frame and selected part. Explicitly confirm whether this frame depicts that exact part; do not match by label alone.",
        "The cited frame depicts the exact selected part, explicitly confirmed by the operator."),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def binding(value):
    require(isinstance(value, dict), "binding must be an object")
    require(set(value) == {"session", "target", "revision", "hashes"}, "binding fields must be explicit")
    require(text(value["session"]) and text(value["revision"]), "session and revision are required")
    target = value["target"]
    require(isinstance(target, dict) and set(target) == {"kind", "id"}, "target needs kind and exact id")
    require(target["kind"] in ("event", "drawing_element") and text(target["id"]), "unsupported target")
    hashes = value["hashes"]
    require(isinstance(hashes, dict) and all(text(k) and sha(v) for k, v in hashes.items()), "invalid bound hashes")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def evaluate(document):
    require(isinstance(document, dict) and document.get("schema") == "anastylos-capture-gap-input@1", "unsupported input schema")
    gap = document["gap"]
    require(isinstance(gap, dict), "gap must be an object")
    require(text(gap.get("id")), "gap id required")
    kind = gap.get("kind")
    require(kind in CATALOG, "unsupported gap kind")
    binding(gap["binding"])
    binding(document["current_binding"])
    previous = gap["source_image_sha256s"]
    require(isinstance(previous, list) and all(sha(x) for x in previous), "source image hashes must be explicit")
    detail = gap.get("detail")
    require(text(detail), "specific missing side, landmark, boundary or frame detail required")
    missing, action, criterion = CATALOG[kind]
    gap_hash = fingerprint(gap)
    result = {"schema": "anastylos-capture-gap-result@1", "gap_id": gap["id"],
        "gap_sha256": gap_hash, "binding": gap["binding"], "kind": kind,
        "missing_information": missing, "detail": detail,
        "instruction": action, "technical_criteria": ["Image bytes match the cited SHA-256.",
        "An explicit technical review accepts image readability, framing and quality.",
        "Source binding matches the current revision and hashes."],
        "content_criterion": criterion, "status": "open", "reason": "No evidence candidate supplied.",
        "basis": None, "dispatch_allowed": False, "candidate": None}
    if gap["binding"] != document["current_binding"]:
        result.update(status="needs_review", reason="Stale source binding; closure refused.")
        return result
    candidate = document.get("candidate")
    if candidate is None:
        return result
    require(isinstance(candidate, dict), "candidate must be an object")
    require(text(candidate.get("evidence_id")) and sha(candidate.get("image_sha256")), "candidate needs evidence id and image hash")
    binding(candidate["binding"])
    result["candidate"] = {"evidence_id": candidate["evidence_id"], "image_sha256": candidate["image_sha256"]}
    result.update(status="needs_review", reason="Candidate requires evidence-bound technical and operator reviews.")
    if candidate["binding"] != gap["binding"]:
        result["reason"] = "Candidate source binding differs; closure refused."
        return result
    if kind != "frame_identity" and candidate["image_sha256"] in previous:
        result["reason"] = "Identical image bytes are not a new view; closure refused."
        return result
    # For frame identity the existing frame itself is the subject of confirmation.
    for name in ("technical_review", "operator_confirmation"):
        review = candidate.get(name)
        if not isinstance(review, dict):
            return result
        if not (review.get("accepted") is True and text(review.get("reviewer"))
                and text(review.get("recorded_at")) and review.get("gap_sha256") == gap_hash
                and review.get("evidence_id") == candidate["evidence_id"]
                and review.get("image_sha256") == candidate["image_sha256"]):
            result["reason"] = "Missing, rejected or mismatched evidence-bound review."
            return result
        if name == "operator_confirmation" and review.get("criterion") != criterion:
            result["reason"] = "Operator confirmation does not confirm the required content criterion."
            return result
    result.update(status="closed", reason="Content explicitly confirmed by the operator; no automatic image analysis or measurement.",
                  basis="operator_declared", reviews={name: candidate[name] for name in ("technical_review", "operator_confirmation")})
    return result
