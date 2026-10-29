#!/usr/bin/env python3
"""Durable guided-control journal for the capture workstation receiver.

Standard library only (``sqlite3``). Additive Wave-1a store for *new*
control state — it never migrates the existing JSON staging files
(``config.json``, ``pairing.json``, ``tokens.json``, ``index.json``).

Contract: frozen G0 ``capture-companion/control/contracts/``
(``ec313f7f...``), read-only. The journal binds itself to one contract
SHA at creation and refuses to open a database sealed under another.

Implemented (Wave-1a): session open/get, request create/revise/cancel
with compare-and-swap revisions, ``(principal, session_id,
operation_id, operation_kind)`` idempotency with canonical-body
comparison, source event log with ``event_id`` dedupe, change cursor,
atomic capture-set sealing with ``manifest_sha256``, and structural +
semantic ViewRequest validation covering the frozen request vectors.

Deferred to Wave-1b (explicitly NOT here): operator consent/grants,
enrollment/claim, attempt reservation, lease renewal, spatial sidecar
binding, package/asset association, receipt qualification, scoped HTTP
routes, resume, cursor compaction/HMAC, and the MCP adapter. Receipt
statuses are produced only by :func:`delivery_state_for_receipt` /
:func:`receipt_for_delivery_state` mapping helpers until then.

Error-code mappings chosen where the frozen schema constrains them:

- Contradictory ``event_id`` reuse maps to ``idempotency_conflict``
  (the frozen ``Error.code`` enum has no ``event_conflict`` member).
- Queue-full (20 non-terminal requests/session) maps to ``busy``.
- Operations on an ``EXPIRED`` session/request map to ``expired``;
  other terminal states map to ``invalid_input``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

MAX_CONTROL_BODY_BYTES = 64 * 1024
MAX_QUEUED_PER_SESSION = 20
MAX_JSON_DEPTH = 16
MAX_CHANGES_PAGE = 100

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
MONOTONIC_RE = re.compile(r"^[0-9]{1,20}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

REQUEST_STATUSES = (
    "QUEUED", "ACCEPTED", "NAVIGATING", "ALIGNING", "HOLD_STILL",
    "CAPTURING", "VERIFYING_IMAGE", "CAPTURED_LOCAL", "RETAKE_REQUIRED",
    "UNREACHABLE", "CANCELLED", "EXPIRED", "SUPERSEDED",
)
TERMINAL_REQUEST = ("CANCELLED", "EXPIRED", "SUPERSEDED")
TERMINAL_SESSION = ("CANCELLED", "EXPIRED", "COMPLETE", "COMPLETE_WITH_SKIPS")
MUTABLE_SESSION = ("DISARMED", "ACTIVE", "PAUSED")
EVENT_KINDS = (
    "armed", "disarmed", "accepted", "unreachable", "paused", "resumed",
    "capture_started", "captured_local", "retake_required", "cancelled",
    "expired",
)
ROI_BASES = ("operator_declared", "measured", "model_assumed")
CAD_CONTEXT_KEYS = ("model_id", "model_revision", "model_sha256",
                    "feature_id", "evidence_need")


class ControlError(Exception):
    """Journal failure with a frozen contract error code."""

    def __init__(self, code, message, current_revision=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.current_revision = current_revision


# -- strict JSON input --------------------------------------------------------


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ControlError("invalid_input",
                               "duplicate JSON key: %r" % (key,))
        obj[key] = value
    return obj


def _reject_constant(value):
    raise ControlError("invalid_input",
                       "non-finite JSON token rejected: %s" % value)


def _check_depth(value, depth=0):
    if depth > MAX_JSON_DEPTH:
        raise ControlError("invalid_input", "JSON nesting exceeds %d"
                           % MAX_JSON_DEPTH)
    if isinstance(value, dict):
        for item in value.values():
            _check_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_depth(item, depth + 1)


def parse_strict_json(raw):
    """Parse control JSON: no NaN/Infinity, no duplicate keys, depth cap."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    try:
        parsed = json.loads(raw, object_pairs_hook=_no_duplicates,
                            parse_constant=_reject_constant)
    except ControlError:
        raise
    except (ValueError, UnicodeDecodeError) as exc:
        raise ControlError("invalid_input", "malformed JSON: %s" % exc)
    _check_depth(parsed)
    if not isinstance(parsed, dict):
        raise ControlError("invalid_input", "control body must be an object")
    return parsed


def canonical_bytes(obj):
    """Canonical UTF-8 bytes: sorted keys, compact separators, no NaN."""
    try:
        text = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ControlError("invalid_input", "non-canonical body: %s" % exc)
    return text.encode("utf-8")


def body_sha256(obj):
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


# -- structural validation (frozen G0 shapes, hand-rolled stdlib checks) ------


def _is_uuid(value):
    return isinstance(value, str) and UUID_RE.match(value) is not None


def _is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_moment(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_str(value, low=1, high=120):
    return (isinstance(value, str) and low <= len(value) <= high)


def _unknown(errors, label, obj, allowed):
    for key in obj:
        if key not in allowed:
            errors.append("%s: unknown key %r" % (label, key))


def _validate_transform(errors, label, node):
    if not isinstance(node, dict):
        errors.append("%s: must be an object" % label)
        return
    _unknown(errors, label, node, ("from", "to", "units", "matrix"))
    if not _is_str(node.get("from")):
        errors.append("%s.from: 1..120 chars" % label)
    if not _is_str(node.get("to")):
        errors.append("%s.to: 1..120 chars" % label)
    if node.get("units") != "mm":
        errors.append("%s.units: must be 'mm'" % label)
    matrix = node.get("matrix")
    if (not isinstance(matrix, list) or len(matrix) != 16
            or not all(_is_number(v) for v in matrix)):
        errors.append("%s.matrix: 16 finite numbers" % label)


def _validate_roi(errors, label, node):
    if not isinstance(node, dict):
        errors.append("%s: must be an object" % label)
        return
    _unknown(errors, label, node,
             ("min_B_mm", "max_B_mm", "basis", "evidence_ids"))
    for edge in ("min_B_mm", "max_B_mm"):
        vec = node.get(edge)
        if (not isinstance(vec, list) or len(vec) != 3
                or not all(_is_number(v) for v in vec)):
            errors.append("%s.%s: 3 finite numbers" % (label, edge))
    if node.get("basis") not in ROI_BASES:
        errors.append("%s.basis: one of %s" % (label, "|".join(ROI_BASES)))
    evidence = node.get("evidence_ids")
    if (not isinstance(evidence, list) or len(evidence) > 50
            or not all(_is_uuid(v) for v in evidence)):
        errors.append("%s.evidence_ids: up to 50 UUIDs" % label)


def _validate_target(errors, label, node):
    if not isinstance(node, dict):
        errors.append("%s: must be an object" % label)
        return
    kind = node.get("kind")
    if kind == "pose":
        _unknown(errors, label, node,
                 ("kind", "T_B_C", "translation_tolerance_mm",
                  "rotation_tolerance_deg", "roi"))
        for key in ("T_B_C", "translation_tolerance_mm",
                    "rotation_tolerance_deg", "roi"):
            if key not in node:
                errors.append("%s: missing %r" % (label, key))
        if "T_B_C" in node:
            _validate_transform(errors, label + ".T_B_C", node["T_B_C"])
        tol = node.get("translation_tolerance_mm")
        if (not isinstance(tol, list) or len(tol) != 3
                or not all(_is_number(v) for v in tol)):
            errors.append("%s.translation_tolerance_mm: 3 numbers" % label)
        rot = node.get("rotation_tolerance_deg")
        if not _is_number(rot) or not 0 < rot <= 30:
            errors.append("%s.rotation_tolerance_deg: (0, 30]" % label)
        if "roi" in node:
            _validate_roi(errors, label + ".roi", node["roi"])
    elif kind == "view_region":
        _unknown(errors, label, node,
                 ("kind", "look_at_B_mm", "azimuth_deg_range",
                  "elevation_deg_range", "range_mm_range", "roll_deg_range",
                  "roi"))
        for key in ("look_at_B_mm", "azimuth_deg_range",
                    "elevation_deg_range", "range_mm_range",
                    "roll_deg_range", "roi"):
            if key not in node:
                errors.append("%s: missing %r" % (label, key))
        look = node.get("look_at_B_mm")
        if (not isinstance(look, list) or len(look) != 3
                or not all(_is_number(v) for v in look)):
            errors.append("%s.look_at_B_mm: 3 numbers" % label)
        for key in ("azimuth_deg_range", "elevation_deg_range",
                    "range_mm_range", "roll_deg_range"):
            span = node.get(key)
            if (not isinstance(span, list) or len(span) != 2
                    or not all(_is_number(v) for v in span)):
                errors.append("%s.%s: 2 numbers" % (label, key))
        if "roi" in node:
            _validate_roi(errors, label + ".roi", node["roi"])
    else:
        errors.append("%s.kind: must be 'pose' or 'view_region'" % label)


def _validate_cad_context(errors, label, node):
    if node is None:
        return
    if not isinstance(node, dict):
        errors.append("%s: must be null or an object" % label)
        return
    for key in CAD_CONTEXT_KEYS:
        if key not in node:
            errors.append("%s: missing %r" % (label, key))
        elif not isinstance(node[key], str):
            errors.append("%s.%s: must be a string" % (label, key))


def validate_view_request_structural(body):
    """Frozen ``ViewRequest`` shape check; returns a list of errors."""
    errors = []
    if not isinstance(body, dict):
        return ["ViewRequest: must be an object"]
    required = ("request_id", "session_id", "source_id", "revision",
                "operation_id", "created_at", "expires_at", "target_label",
                "board_id", "calibration_id", "target",
                "quality_profile_id", "status",
                "board_registration_epoch_id", "object_pose_epoch_id",
                "cad_context", "reason")
    _unknown(errors, "ViewRequest", body, required)
    for key in required:
        if key not in body:
            errors.append("ViewRequest: missing %r" % key)
    for key in ("request_id", "session_id", "source_id", "operation_id",
                "board_id", "calibration_id", "board_registration_epoch_id",
                "object_pose_epoch_id"):
        if key in body and not _is_uuid(body[key]):
            errors.append("ViewRequest.%s: UUID required" % key)
    if "revision" in body and (not _is_int(body["revision"])
                               or body["revision"] < 1):
        errors.append("ViewRequest.revision: integer >= 1")
    for key in ("created_at", "expires_at"):
        if key in body and not _is_moment(body[key]):
            errors.append("ViewRequest.%s: RFC3339 Z required" % key)
    if "target_label" in body and not _is_str(body["target_label"]):
        errors.append("ViewRequest.target_label: 1..120 chars")
    if ("quality_profile_id" in body
            and not _is_str(body["quality_profile_id"])):
        errors.append("ViewRequest.quality_profile_id: 1..120 chars")
    if "status" in body and body["status"] not in REQUEST_STATUSES:
        errors.append("ViewRequest.status: unknown status")
    if "target" in body:
        _validate_target(errors, "ViewRequest.target", body["target"])
    if "cad_context" in body:
        _validate_cad_context(errors, "ViewRequest.cad_context",
                              body["cad_context"])
    if "reason" in body and not _is_str(body["reason"], 1, 500):
        errors.append("ViewRequest.reason: 1..500 chars")
    return errors


def _rotation_determinant(matrix):
    a, b, c = matrix[0], matrix[1], matrix[2]
    d, e, f = matrix[4], matrix[5], matrix[6]
    g, h, i = matrix[8], matrix[9], matrix[10]
    return (a * (e * i - f * h) - b * (d * i - f * g)
            + c * (d * h - e * g))


def validate_target_semantic(target, errors, label="target"):
    """Cross-field geometry rules; JSON Schema is necessary, not sufficient."""
    kind = target.get("kind")
    roi = target.get("roi") or {}
    lo, hi = roi.get("min_B_mm"), roi.get("max_B_mm")
    if (isinstance(lo, list) and isinstance(hi, list)
            and len(lo) == 3 and len(hi) == 3
            and all(_is_number(v) for v in lo + hi)):
        if not all(a <= b for a, b in zip(lo, hi)):
            errors.append("%s.roi: min_B_mm must precede max_B_mm" % label)
    if kind == "pose":
        matrix = (target.get("T_B_C") or {}).get("matrix")
        if isinstance(matrix, list) and len(matrix) == 16:
            tail = matrix[12:16]
            if not all(abs(v - w) < 1e-9
                       for v, w in zip(tail, (0, 0, 0, 1))):
                errors.append("%s.T_B_C: last row must be 0,0,0,1" % label)
            det = _rotation_determinant(matrix)
            if abs(det - 1.0) >= 1e-6:
                errors.append("%s.T_B_C: rotation must be proper "
                              "(det +1, got %.6f)" % (label, det))
            for row in ((0, 1, 2), (4, 5, 6), (8, 9, 10)):
                norm = math.sqrt(sum(matrix[k] ** 2 for k in row))
                if abs(norm - 1.0) >= 1e-6:
                    errors.append("%s.T_B_C: rotation rows must be unit"
                                  % label)
                    break
            if not matrix[11] < 0:
                errors.append("%s.T_B_C: camera must sit in front of "
                              "the board (Tz < 0 mm)" % label)
        tol = target.get("translation_tolerance_mm")
        if (isinstance(tol, list)
                and not all(_is_number(v) and v > 0 for v in tol)):
            errors.append("%s.translation_tolerance_mm: positive" % label)
    elif kind == "view_region":
        for key in ("azimuth_deg_range", "elevation_deg_range",
                    "range_mm_range", "roll_deg_range"):
            span = target.get(key)
            if (isinstance(span, list) and len(span) == 2
                    and span[0] > span[1]):
                errors.append("%s.%s: ordered range required" % (label, key))
        span = target.get("range_mm_range")
        if (isinstance(span, list) and len(span) == 2
                and not all(_is_number(v) and v > 0 for v in span)):
            errors.append("%s.range_mm_range: positive distances" % label)
    return errors


def validate_view_request_semantic(body, now):
    """Time/order/geometry rules for a structurally valid ViewRequest."""
    errors = []
    created = body.get("created_at")
    expires = body.get("expires_at")
    if _is_moment(created) and _is_moment(expires):
        start = datetime.fromisoformat(created.replace("Z", "+00:00"))
        end = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if end <= start:
            errors.append("expires_at must follow created_at")
        if end <= now:
            errors.append("request TTL already expired at server now")
    if isinstance(body.get("target"), dict):
        validate_target_semantic(body["target"], errors)
    return errors


def _validate_input(body, label, required, checks):
    errors = []
    if not isinstance(body, dict):
        return ["%s: must be an object" % label]
    _unknown(errors, label, body, required)
    for key in required:
        if key not in body:
            errors.append("%s: missing %r" % (label, key))
    for key, check in checks:
        if key in body and not check(body[key]):
            errors.append("%s.%s: invalid" % (label, key))
    return errors


def validate_open_input(body):
    return _validate_input(
        body, "OpenInput",
        ("operation_id", "source_id", "max_images", "expires_at"),
        (("operation_id", _is_uuid), ("source_id", _is_uuid),
         ("max_images", lambda v: _is_int(v) and 1 <= v <= 20),
         ("expires_at", _is_moment)))


def validate_request_input(body):
    errors = _validate_input(
        body, "RequestInput",
        ("operation_id", "session_id", "expected_session_revision",
         "source_id", "target_label", "board_id", "calibration_id",
         "target", "quality_profile_id", "expires_at", "reason",
         "board_registration_epoch_id", "object_pose_epoch_id",
         "cad_context"),
        (("operation_id", _is_uuid), ("session_id", _is_uuid),
         ("expected_session_revision",
          lambda v: _is_int(v) and v >= 0),
         ("source_id", _is_uuid),
         ("target_label", _is_str),
         ("board_id", _is_uuid), ("calibration_id", _is_uuid),
         ("quality_profile_id", _is_str), ("expires_at", _is_moment),
         ("reason", lambda v: _is_str(v, 1, 500)),
         ("board_registration_epoch_id", _is_uuid),
         ("object_pose_epoch_id", _is_uuid)))
    if isinstance(body, dict) and "target" in body:
        _validate_target(errors, "RequestInput.target", body["target"])
    if isinstance(body, dict) and "cad_context" in body:
        _validate_cad_context(errors, "RequestInput.cad_context",
                              body["cad_context"])
    return errors


def validate_revise_input(body):
    errors = _validate_input(
        body, "ReviseInput",
        ("operation_id", "session_id", "expected_session_revision",
         "request_id", "target", "expires_at", "reason",
         "expected_request_revision", "cad_context"),
        (("operation_id", _is_uuid), ("session_id", _is_uuid),
         ("expected_session_revision",
          lambda v: _is_int(v) and v >= 0),
         ("request_id", _is_uuid), ("expires_at", _is_moment),
         ("reason", lambda v: _is_str(v, 1, 500)),
         ("expected_request_revision",
          lambda v: _is_int(v) and v >= 0)))
    if isinstance(body, dict) and "target" in body:
        _validate_target(errors, "ReviseInput.target", body["target"])
    if isinstance(body, dict) and "cad_context" in body:
        _validate_cad_context(errors, "ReviseInput.cad_context",
                              body["cad_context"])
    return errors


def validate_cancel_input(body):
    return _validate_input(
        body, "CancelInput",
        ("operation_id", "session_id", "expected_session_revision",
         "request_id", "reason", "expected_request_revision"),
        (("operation_id", _is_uuid), ("session_id", _is_uuid),
         ("expected_session_revision",
          lambda v: _is_int(v) and v >= 0),
         ("request_id", _is_uuid),
         ("reason", lambda v: _is_str(v, 1, 500)),
         ("expected_request_revision",
          lambda v: _is_int(v) and v >= 0)))


def validate_mutation_input(body):
    return _validate_input(
        body, "MutationInput",
        ("operation_id", "session_id", "expected_session_revision"),
        (("operation_id", _is_uuid), ("session_id", _is_uuid),
         ("expected_session_revision",
          lambda v: _is_int(v) and v >= 0)))


def validate_event_structural(body):
    errors = _validate_input(
        body, "Event",
        ("event_id", "session_id", "source_id", "request_id",
         "request_revision", "kind", "occurred_at", "monotonic_ns",
         "attempt_id", "reason"),
        (("event_id", _is_uuid), ("session_id", _is_uuid),
         ("source_id", _is_uuid),
         ("request_id", lambda v: v is None or _is_uuid(v)),
         ("request_revision",
          lambda v: v is None or (_is_int(v) and v >= 1)),
         ("occurred_at", _is_moment),
         ("monotonic_ns", lambda v: isinstance(v, str)
          and MONOTONIC_RE.match(v) is not None),
         ("attempt_id", lambda v: v is None or _is_uuid(v)),
         ("reason", lambda v: v is None or _is_str(v, 1, 500))))
    if isinstance(body, dict) and body.get("kind") not in EVENT_KINDS:
        errors.append("Event.kind: unknown kind")
    return errors


# -- delivery/receipt mapping (frozen rule 5) ---------------------------------


def delivery_state_for_receipt(receipt_status):
    """AttemptDelivery state implied by a Receipt status."""
    if receipt_status == "qualified":
        return "ACKNOWLEDGED"
    if receipt_status == "package_only":
        return "PACKAGE_ACCEPTED"
    return "REJECTED"


def receipt_for_delivery_state(delivery_state):
    """Receipt status implied by an AttemptDelivery state (None if none)."""
    if delivery_state == "ACKNOWLEDGED":
        return "qualified"
    if delivery_state in ("PACKAGE_ACCEPTED", "SPATIAL_PENDING"):
        return "package_only"
    if delivery_state == "REJECTED":
        return "rejected"
    return None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
    grant_id TEXT, revision INTEGER NOT NULL, state TEXT NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    lease_expires_at TEXT, lease_remaining_ms INTEGER NOT NULL,
    capabilities_json TEXT NOT NULL, active_request_id TEXT,
    consent_revision INTEGER NOT NULL, adapter_max_images INTEGER NOT NULL,
    event_seq INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT NOT NULL, revision INTEGER NOT NULL,
    session_id TEXT NOT NULL, source_id TEXT NOT NULL,
    operation_id TEXT NOT NULL, created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL, target_label TEXT NOT NULL,
    board_id TEXT NOT NULL, calibration_id TEXT NOT NULL,
    target_json TEXT NOT NULL, quality_profile_id TEXT NOT NULL,
    status TEXT NOT NULL, board_epoch TEXT NOT NULL,
    object_epoch TEXT NOT NULL, cad_context_json TEXT,
    reason TEXT NOT NULL,
    PRIMARY KEY (request_id, revision));
CREATE INDEX IF NOT EXISTS idx_requests_session ON requests (session_id);
CREATE TABLE IF NOT EXISTS idempotency (
    principal TEXT NOT NULL, session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL, operation_kind TEXT NOT NULL,
    body_sha256 TEXT NOT NULL, response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (principal, session_id, operation_id, operation_kind));
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    source_id TEXT NOT NULL, request_id TEXT, request_revision INTEGER,
    kind TEXT NOT NULL, occurred_at TEXT NOT NULL,
    monotonic_ns TEXT NOT NULL, attempt_id TEXT, reason TEXT,
    event_seq INTEGER NOT NULL, body_sha256 TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_session ON events (session_id);
CREATE TABLE IF NOT EXISTS changes (
    change_seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    kind TEXT NOT NULL, session_revision INTEGER NOT NULL,
    request_json TEXT, source_event_json TEXT, receipt_json TEXT);
CREATE INDEX IF NOT EXISTS idx_changes_session ON changes (session_id);
CREATE TABLE IF NOT EXISTS capture_sets (
    capture_set_id TEXT NOT NULL, revision INTEGER NOT NULL,
    session_id TEXT NOT NULL, created_at TEXT NOT NULL,
    status TEXT NOT NULL, required_json TEXT NOT NULL,
    receipts_json TEXT NOT NULL, omissions_json TEXT NOT NULL,
    calibration_assets_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
    PRIMARY KEY (capture_set_id, revision));
"""


def _utcnow_iso(now):
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


class ControlJournal:
    """Transactional control store: sessions, requests, events, seals."""

    def __init__(self, db_path, contract_sha, now_fn=None):
        self.db_path = Path(db_path)
        self.contract_sha = contract_sha
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._conn:
            self._conn.executescript(_SCHEMA)
        quick = self._conn.execute("PRAGMA quick_check").fetchone()
        if not quick or str(quick[0]).lower() != "ok":
            raise ValueError("control journal failed integrity check")
        stored = self._meta("contract_sha")
        if stored is None:
            self._set_meta("contract_sha", contract_sha)
            self._set_meta("schema_version", str(SCHEMA_VERSION))
        elif stored != contract_sha:
            raise ValueError("control journal contract mismatch: sealed "
                             "under another contract SHA")

    def close(self):
        with self._lock:
            self._conn.close()

    def _now(self):
        now = self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now

    def _meta(self, key):
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key, value):
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value))

    def _check_body_size(self, body):
        if len(canonical_bytes(body)) > MAX_CONTROL_BODY_BYTES:
            raise ControlError("invalid_input", "control body exceeds 64 KiB")

    def _idempotent_replay(self, principal, session_id, operation_id, kind,
                           body):
        row = self._conn.execute(
            "SELECT body_sha256, response_json FROM idempotency "
            "WHERE principal = ? AND session_id = ? AND operation_id = ? "
            "AND operation_kind = ?",
            (principal, session_id, operation_id, kind)).fetchone()
        if row is None:
            return None, body_sha256(body)
        digest = body_sha256(body)
        if row[0] != digest:
            raise ControlError("idempotency_conflict",
                               "operation_id reused with a different body")
        return json.loads(row[1]), digest

    def _remember(self, principal, session_id, operation_id, kind, digest,
                  response, now_iso):
        self._conn.execute(
            "INSERT INTO idempotency (principal, session_id, operation_id,"
            " operation_kind, body_sha256, response_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (principal, session_id, operation_id, kind, digest,
             json.dumps(response), now_iso))

    def _session_row(self, session_id):
        return self._conn.execute(
            "SELECT session_id, source_id, grant_id, revision, state,"
            " created_at, expires_at, lease_expires_at, lease_remaining_ms,"
            " capabilities_json, active_request_id, consent_revision,"
            " adapter_max_images, event_seq FROM sessions WHERE session_id = ?",
            (session_id,)).fetchone()

    @staticmethod
    def _session_of(row):
        return {
            "session_id": row[0], "source_id": row[1], "grant_id": row[2],
            "revision": row[3], "state": row[4], "created_at": row[5],
            "expires_at": row[6], "lease_expires_at": row[7],
            "lease_remaining_ms": row[8],
            "capabilities": json.loads(row[9]),
            "active_request_id": row[10], "consent_revision": row[11],
        }

    def _request_of(self, row):
        return {
            "request_id": row[0], "revision": row[1], "session_id": row[2],
            "source_id": row[3], "operation_id": row[4],
            "created_at": row[5], "expires_at": row[6],
            "target_label": row[7], "board_id": row[8],
            "calibration_id": row[9], "target": json.loads(row[10]),
            "quality_profile_id": row[11], "status": row[12],
            "board_registration_epoch_id": row[13],
            "object_pose_epoch_id": row[14],
            "cad_context": json.loads(row[15]) if row[15] else None,
            "reason": row[16],
        }

    def _latest_requests(self, session_id):
        rows = self._conn.execute(
            "SELECT request_id, revision, session_id, source_id, operation_id,"
            " created_at, expires_at, target_label, board_id, calibration_id,"
            " target_json, quality_profile_id, status, board_epoch,"
            " object_epoch, cad_context_json, reason, MIN(rowid)"
            " FROM requests WHERE session_id = ?"
            " GROUP BY request_id", (session_id,)).fetchall()
        # GROUP BY keeps one row per request; re-fetch each latest revision.
        latest = []
        for row in rows:
            full = self._conn.execute(
                "SELECT request_id, revision, session_id, source_id,"
                " operation_id, created_at, expires_at, target_label,"
                " board_id, calibration_id, target_json, quality_profile_id,"
                " status, board_epoch, object_epoch, cad_context_json, reason"
                " FROM requests WHERE request_id = ?"
                " ORDER BY revision DESC LIMIT 1", (row[0],)).fetchone()
            latest.append((row[17], self._request_of(full)))
        latest.sort(key=lambda item: item[0])
        return [item[1] for item in latest]

    def _record_change(self, session_id, kind, session_revision,
                       request=None, source_event=None, receipt=None):
        self._conn.execute(
            "INSERT INTO changes (session_id, kind, session_revision,"
            " request_json, source_event_json, receipt_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, kind, session_revision,
             json.dumps(request) if request is not None else None,
             json.dumps(source_event) if source_event is not None else None,
             json.dumps(receipt) if receipt is not None else None))

    # -- sessions ------------------------------------------------------

    def open_session(self, principal, body, bound_source_id):
        """Create a DISARMED session pending phone consent."""
        errors = validate_open_input(body)
        if errors:
            raise ControlError("invalid_input", "; ".join(errors))
        if not isinstance(principal, str) or not principal:
            raise ControlError("unauthorized", "principal required")
        if body["source_id"] != bound_source_id:
            raise ControlError("unauthorized",
                               "source_id is bound by authentication")
        self._check_body_size(body)
        now = self._now()
        expires = datetime.fromisoformat(
            body["expires_at"].replace("Z", "+00:00"))
        if expires <= now:
            raise ControlError("invalid_input",
                               "session expires_at must be in the future")
        with self._lock:
            replayed, digest = self._idempotent_replay(
                principal, "", body["operation_id"], "open_session", body)
            if replayed is not None:
                return replayed
            now_iso = _utcnow_iso(now)
            session = {
                "session_id": str(uuid.uuid4()),
                "source_id": bound_source_id, "grant_id": None,
                "revision": 1, "state": "DISARMED",
                "created_at": now_iso, "expires_at": body["expires_at"],
                "lease_expires_at": None, "lease_remaining_ms": 0,
                "capabilities": {
                    "source_id": bound_source_id, "updated_at": now_iso,
                    "stale": True, "camera_ids": [], "resolutions_px": [],
                    "calibration_ids": [], "target_kinds": [],
                    "quality_profile_ids": [], "app_version": "unpublished",
                    "protocol_version": 1,
                },
                "active_request_id": None, "consent_revision": 0,
            }
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "INSERT INTO sessions (session_id, source_id, grant_id,"
                    " revision, state, created_at, expires_at,"
                    " lease_expires_at, lease_remaining_ms, capabilities_json,"
                    " active_request_id, consent_revision, adapter_max_images,"
                    " event_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?, 0)",
                    (session["session_id"], bound_source_id, None, 1,
                     "DISARMED", now_iso, body["expires_at"], None, 0,
                     json.dumps(session["capabilities"]), None, 0,
                     body["max_images"]))
                self._remember(principal, "", body["operation_id"],
                               "open_session", digest, session, now_iso)
                self._record_change(session["session_id"], "session_changed",
                                    1)
            return session

    def get_session(self, session_id, bound_source_id=None):
        """Snapshot: session + latest request revisions + empty seal slots."""
        with self._lock:
            row = self._session_row(session_id)
            if row is None:
                raise ControlError("not_found", "unknown session")
            if bound_source_id is not None and row[1] != bound_source_id:
                raise ControlError("not_found", "unknown session")
            return {
                "session": self._session_of(row),
                "requests": self._latest_requests(session_id),
                "latest_status": None,
                "receipts": [],
            }

    # -- requests ------------------------------------------------------

    def _load_session_for_write(self, session_id, bound_source_id):
        row = self._session_row(session_id)
        if row is None or row[1] != bound_source_id:
            raise ControlError("not_found", "unknown session")
        return row

    def request_view(self, principal, bound_source_id, body):
        """Queue one authoritative ViewRequest (CAS on session revision)."""
        errors = validate_request_input(body)
        if errors:
            raise ControlError("invalid_input", "; ".join(errors))
        if body["source_id"] != bound_source_id:
            raise ControlError("unauthorized",
                               "source_id is bound by authentication")
        self._check_body_size(body)
        target_errors = validate_target_semantic(
            body["target"], [], "RequestInput.target")
        if target_errors:
            raise ControlError("invalid_input", "; ".join(target_errors))
        now = self._now()
        expires = datetime.fromisoformat(
            body["expires_at"].replace("Z", "+00:00"))
        if expires <= now:
            raise ControlError("invalid_input",
                               "request TTL already expired at server now")
        with self._lock:
            replayed, digest = self._idempotent_replay(
                principal, body["session_id"], body["operation_id"],
                "request_view", body)
            if replayed is not None:
                return replayed
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                session = self._load_session_for_write(
                    body["session_id"], bound_source_id)
                if session[4] == "EXPIRED":
                    raise ControlError("expired", "session expired")
                if session[4] not in MUTABLE_SESSION:
                    raise ControlError("invalid_input",
                                       "session is %s" % session[4].lower())
                if body["expected_session_revision"] != session[3]:
                    raise ControlError(
                        "revision_conflict", "stale session revision",
                        current_revision=session[3])
                queued = self._conn.execute(
                    "SELECT COUNT(*) FROM (SELECT request_id FROM requests"
                    " WHERE session_id = ? GROUP BY request_id"
                    " HAVING MAX(CASE WHEN status IN"
                    " ('CANCELLED','EXPIRED','SUPERSEDED') THEN 0 ELSE 1 END)"
                    " = 1)",
                    (body["session_id"],)).fetchone()[0]
                if queued >= MAX_QUEUED_PER_SESSION:
                    raise ControlError("busy", "request queue full (20)")
                now_iso = _utcnow_iso(now)
                new_revision = session[3] + 1
                request = {
                    "request_id": str(uuid.uuid4()),
                    "session_id": body["session_id"],
                    "source_id": bound_source_id, "revision": 1,
                    "operation_id": body["operation_id"],
                    "created_at": now_iso, "expires_at": body["expires_at"],
                    "target_label": body["target_label"],
                    "board_id": body["board_id"],
                    "calibration_id": body["calibration_id"],
                    "target": body["target"],
                    "quality_profile_id": body["quality_profile_id"],
                    "status": "QUEUED",
                    "board_registration_epoch_id":
                        body["board_registration_epoch_id"],
                    "object_pose_epoch_id": body["object_pose_epoch_id"],
                    "cad_context": body["cad_context"],
                    "reason": body["reason"],
                }
                self._conn.execute(
                    "INSERT INTO requests (request_id, revision, session_id,"
                    " source_id, operation_id, created_at, expires_at,"
                    " target_label, board_id, calibration_id, target_json,"
                    " quality_profile_id, status, board_epoch, object_epoch,"
                    " cad_context_json, reason)"
                    " VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?)",
                    (request["request_id"], body["session_id"],
                     bound_source_id, body["operation_id"], now_iso,
                     body["expires_at"], body["target_label"], body["board_id"],
                     body["calibration_id"], json.dumps(body["target"]),
                     body["quality_profile_id"], "QUEUED",
                     body["board_registration_epoch_id"],
                     body["object_pose_epoch_id"],
                     json.dumps(body["cad_context"])
                     if body["cad_context"] is not None else None,
                     body["reason"]))
                self._conn.execute(
                    "UPDATE sessions SET revision = ? WHERE session_id = ?",
                    (new_revision, body["session_id"]))
                response = {"request": request,
                            "session_revision": new_revision}
                self._remember(principal, body["session_id"],
                               body["operation_id"], "request_view", digest,
                               response, now_iso)
                self._record_change(body["session_id"], "request_created",
                                    new_revision, request=request)
            return response

    def _latest_request_row(self, request_id, session_id):
        return self._conn.execute(
            "SELECT request_id, revision, session_id, source_id, operation_id,"
            " created_at, expires_at, target_label, board_id, calibration_id,"
            " target_json, quality_profile_id, status, board_epoch,"
            " object_epoch, cad_context_json, reason FROM requests"
            " WHERE request_id = ? AND session_id = ?"
            " ORDER BY revision DESC LIMIT 1",
            (request_id, session_id)).fetchone()

    def revise_view(self, principal, bound_source_id, body):
        """Replace a request target as a new immutable revision (dual CAS)."""
        errors = validate_revise_input(body)
        if errors:
            raise ControlError("invalid_input", "; ".join(errors))
        self._check_body_size(body)
        target_errors = validate_target_semantic(
            body["target"], [], "ReviseInput.target")
        if target_errors:
            raise ControlError("invalid_input", "; ".join(target_errors))
        now = self._now()
        expires = datetime.fromisoformat(
            body["expires_at"].replace("Z", "+00:00"))
        if expires <= now:
            raise ControlError("invalid_input",
                               "request TTL already expired at server now")
        with self._lock:
            replayed, digest = self._idempotent_replay(
                principal, body["session_id"], body["operation_id"],
                "revise_view", body)
            if replayed is not None:
                return replayed
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                session = self._load_session_for_write(
                    body["session_id"], bound_source_id)
                latest = self._latest_request_row(
                    body["request_id"], body["session_id"])
                if latest is None:
                    raise ControlError("not_found", "unknown request")
                if (body["expected_session_revision"] != session[3]
                        or body["expected_request_revision"] != latest[1]):
                    raise ControlError(
                        "revision_conflict", "stale revision",
                        current_revision=session[3])
                if latest[12] == "EXPIRED":
                    raise ControlError("expired", "request expired")
                if latest[12] in ("CANCELLED", "SUPERSEDED"):
                    raise ControlError("invalid_input",
                                       "request is %s" % latest[12].lower())
                if session[4] == "EXPIRED":
                    raise ControlError("expired", "session expired")
                if session[4] not in MUTABLE_SESSION:
                    raise ControlError("invalid_input",
                                       "session is %s" % session[4].lower())
                now_iso = _utcnow_iso(now)
                new_revision = session[3] + 1
                new_request_revision = latest[1] + 1
                request = {
                    "request_id": body["request_id"],
                    "session_id": body["session_id"],
                    "source_id": bound_source_id,
                    "revision": new_request_revision,
                    "operation_id": body["operation_id"],
                    "created_at": now_iso, "expires_at": body["expires_at"],
                    "target_label": latest[7], "board_id": latest[8],
                    "calibration_id": latest[9], "target": body["target"],
                    "quality_profile_id": latest[11], "status": "QUEUED",
                    "board_registration_epoch_id": latest[13],
                    "object_pose_epoch_id": latest[14],
                    "cad_context": body["cad_context"],
                    "reason": body["reason"],
                }
                self._conn.execute(
                    "UPDATE requests SET status = 'SUPERSEDED'"
                    " WHERE request_id = ? AND revision = ?",
                    (body["request_id"], latest[1]))
                self._conn.execute(
                    "INSERT INTO requests (request_id, revision, session_id,"
                    " source_id, operation_id, created_at, expires_at,"
                    " target_label, board_id, calibration_id, target_json,"
                    " quality_profile_id, status, board_epoch, object_epoch,"
                    " cad_context_json, reason)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?)",
                    (body["request_id"], new_request_revision,
                     body["session_id"], bound_source_id,
                     body["operation_id"], now_iso, body["expires_at"],
                     latest[7], latest[8], latest[9],
                     json.dumps(body["target"]), latest[11], "QUEUED",
                     latest[13], latest[14],
                     json.dumps(body["cad_context"])
                     if body["cad_context"] is not None else None,
                     body["reason"]))
                self._conn.execute(
                    "UPDATE sessions SET revision = ? WHERE session_id = ?",
                    (new_revision, body["session_id"]))
                response = {"request": request,
                            "session_revision": new_revision}
                self._remember(principal, body["session_id"],
                               body["operation_id"], "revise_view", digest,
                               response, now_iso)
                self._record_change(body["session_id"], "request_revised",
                                    new_revision, request=request)
            return response

    def cancel_view(self, principal, bound_source_id, body):
        """Cancel one request; terminal stays terminal, evidence kept."""
        errors = validate_cancel_input(body)
        if errors:
            raise ControlError("invalid_input", "; ".join(errors))
        self._check_body_size(body)
        with self._lock:
            replayed, digest = self._idempotent_replay(
                principal, body["session_id"], body["operation_id"],
                "cancel_view", body)
            if replayed is not None:
                return replayed
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                session = self._load_session_for_write(
                    body["session_id"], bound_source_id)
                latest = self._latest_request_row(
                    body["request_id"], body["session_id"])
                if latest is None:
                    raise ControlError("not_found", "unknown request")
                if (body["expected_session_revision"] != session[3]
                        or body["expected_request_revision"] != latest[1]):
                    raise ControlError(
                        "revision_conflict", "stale revision",
                        current_revision=session[3])
                now_iso = _utcnow_iso(self._now())
                if latest[12] in TERMINAL_REQUEST:
                    response = {"request": self._request_of(latest),
                                "session_revision": session[3]}
                    self._remember(principal, body["session_id"],
                                   body["operation_id"], "cancel_view",
                                   digest, response, now_iso)
                    return response
                new_revision = session[3] + 1
                self._conn.execute(
                    "UPDATE requests SET status = 'CANCELLED'"
                    " WHERE request_id = ? AND revision = ?",
                    (body["request_id"], latest[1]))
                updated = self._latest_request_row(
                    body["request_id"], body["session_id"])
                self._conn.execute(
                    "UPDATE sessions SET revision = ? WHERE session_id = ?",
                    (new_revision, body["session_id"]))
                response = {"request": self._request_of(updated),
                            "session_revision": new_revision}
                self._remember(principal, body["session_id"],
                               body["operation_id"], "cancel_view", digest,
                               response, now_iso)
                self._record_change(body["session_id"], "request_cancelled",
                                    new_revision,
                                    request=self._request_of(updated))
            return response

    # -- source events --------------------------------------------------------

    @staticmethod
    def _reduce_event(session_state, request_status, kind):
        """Minimal forward-only state reducer; terminal states stick."""
        new_session = session_state
        if kind == "armed" and session_state in ("DISARMED", "PAUSED"):
            new_session = "ACTIVE"
        elif kind == "disarmed" and session_state != "CANCELLED":
            new_session = "DISARMED"
        elif kind == "paused" and session_state == "ACTIVE":
            new_session = "PAUSED"
        elif kind == "resumed" and session_state == "PAUSED":
            new_session = "ACTIVE"
        new_status = request_status
        if request_status is not None and request_status not in TERMINAL_REQUEST:
            mapping = {
                "accepted": "ACCEPTED", "unreachable": "UNREACHABLE",
                "captured_local": "CAPTURED_LOCAL",
                "retake_required": "RETAKE_REQUIRED",
                "cancelled": "CANCELLED", "expired": "EXPIRED",
            }
            if kind in mapping:
                new_status = mapping[kind]
        return new_session, new_status

    def record_event(self, bound_source_id, event):
        """Append a source event exactly once (``event_id`` dedupe)."""
        errors = validate_event_structural(event)
        if errors:
            raise ControlError("invalid_input", "; ".join(errors))
        if event["source_id"] != bound_source_id:
            raise ControlError("unauthorized",
                               "source_id is bound by authentication")
        self._check_body_size(event)
        digest = body_sha256(event)
        with self._lock:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                stored = self._conn.execute(
                    "SELECT body_sha256, event_seq, session_id FROM events"
                    " WHERE event_id = ?", (event["event_id"],)).fetchone()
                if stored is not None:
                    if stored[0] != digest:
                        raise ControlError(
                            "idempotency_conflict",
                            "event_id reused with a different body")
                    session = self._session_row(stored[2])
                    return {"accepted_event_id": event["event_id"],
                            "event_seq": stored[1],
                            "session_revision": session[3]}
                session = self._load_session_for_write(
                    event["session_id"], bound_source_id)
                request_status = None
                if event["request_id"] is not None:
                    if event["request_revision"] is not None:
                        row = self._conn.execute(
                            "SELECT status FROM requests"
                            " WHERE request_id = ? AND revision = ?"
                            " AND session_id = ?",
                            (event["request_id"], event["request_revision"],
                             event["session_id"])).fetchone()
                        if row is None:
                            raise ControlError("not_found",
                                               "unknown request revision")
                        request_status = row[0]
                    else:
                        latest = self._latest_request_row(
                            event["request_id"], event["session_id"])
                        if latest is None:
                            raise ControlError("not_found",
                                               "unknown request")
                        request_status = latest[12]
                new_session_state, new_status = self._reduce_event(
                    session[4], request_status, event["kind"])
                new_revision = session[3] + 1
                event_seq = session[13] + 1
                self._conn.execute(
                    "INSERT INTO events (event_id, session_id, source_id,"
                    " request_id, request_revision, kind, occurred_at,"
                    " monotonic_ns, attempt_id, reason, event_seq,"
                    " body_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (event["event_id"], event["session_id"], bound_source_id,
                     event["request_id"], event["request_revision"],
                     event["kind"], event["occurred_at"],
                     event["monotonic_ns"], event["attempt_id"],
                     event["reason"], event_seq, digest))
                if (event["request_id"] is not None and new_status is not None
                        and new_status != request_status):
                    if event["request_revision"] is not None:
                        self._conn.execute(
                            "UPDATE requests SET status = ?"
                            " WHERE request_id = ? AND revision = ?",
                            (new_status, event["request_id"],
                             event["request_revision"]))
                    else:
                        latest = self._latest_request_row(
                            event["request_id"], event["session_id"])
                        self._conn.execute(
                            "UPDATE requests SET status = ?"
                            " WHERE request_id = ? AND revision = ?",
                            (new_status, event["request_id"], latest[1]))
                self._conn.execute(
                    "UPDATE sessions SET revision = ?, event_seq = ?,"
                    " state = ? WHERE session_id = ?",
                    (new_revision, event_seq, new_session_state,
                     event["session_id"]))
                self._record_change(event["session_id"], "source_event",
                                    new_revision, source_event=event)
            return {"accepted_event_id": event["event_id"],
                    "event_seq": event_seq,
                    "session_revision": new_revision}

    # -- change cursor --------------------------------------------------------

    def get_changes(self, session_id, after_cursor, bound_source_id=None):
        """Bounded change page; cursor is the last seen ``change_seq``."""
        try:
            after = int(after_cursor)
        except (TypeError, ValueError):
            raise ControlError("invalid_input", "malformed cursor")
        if after < 0:
            raise ControlError("invalid_input", "malformed cursor")
        with self._lock:
            row = self._session_row(session_id)
            if row is None:
                raise ControlError("not_found", "unknown session")
            if bound_source_id is not None and row[1] != bound_source_id:
                raise ControlError("not_found", "unknown session")
            rows = self._conn.execute(
                "SELECT change_seq, kind, session_revision, request_json,"
                " source_event_json, receipt_json FROM changes"
                " WHERE session_id = ? AND change_seq > ?"
                " ORDER BY change_seq ASC LIMIT ?",
                (session_id, after, MAX_CHANGES_PAGE)).fetchall()
            events = [{
                "change_seq": seq, "kind": kind,
                "session_revision": revision,
                "request": json.loads(req) if req else None,
                "source_event": json.loads(evt) if evt else None,
                "receipt": json.loads(rec) if rec else None,
            } for seq, kind, revision, req, evt, rec in rows]
            cursor = str(rows[-1][0]) if rows else str(after)
            return {
                "events": events, "next_cursor": cursor,
                "snapshot_required": False,
                "server_time": _utcnow_iso(self._now()),
                "lease_expires_at": row[7], "lease_remaining_ms": row[8],
            }

    # -- sealing ---------------------------------------------------------------

    def finalize(self, principal, body):
        """Seal one immutable capture-set revision (single transaction).

        Wave-1a seals ``(request_id, latest_revision)`` pairs with empty
        receipts: qualification needs package/sidecar binding (Wave-1b),
        so every required pair lands in ``omissions`` with its request
        status as the reason. Grant-scoped adapter authorization is also
        Wave-1b; the principal is recorded for idempotency only.
        """
        errors = validate_mutation_input(body)
        if errors:
            raise ControlError("invalid_input", "; ".join(errors))
        if not isinstance(principal, str) or not principal:
            raise ControlError("unauthorized", "principal required")
        self._check_body_size(body)
        with self._lock:
            replayed, digest = self._idempotent_replay(
                principal, body["session_id"], body["operation_id"],
                "finalize", body)
            if replayed is not None:
                return replayed
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                session = self._conn.execute(
                    "SELECT session_id, source_id, revision, state,"
                    " created_at FROM sessions WHERE session_id = ?",
                    (body["session_id"],)).fetchone()
                if session is None:
                    raise ControlError("not_found", "unknown session")
                if body["expected_session_revision"] != session[2]:
                    raise ControlError(
                        "revision_conflict", "stale session revision",
                        current_revision=session[2])
                if session[3] in ("COMPLETE", "COMPLETE_WITH_SKIPS"):
                    raise ControlError("invalid_input",
                                       "capture set already sealed; resume"
                                       " is required for a new revision")
                sealed = self._conn.execute(
                    "SELECT COUNT(*) FROM capture_sets WHERE session_id = ?",
                    (body["session_id"],)).fetchone()[0]
                if sealed:
                    raise ControlError("invalid_input",
                                       "capture set already sealed; resume"
                                       " is required for a new revision")
                latest = self._latest_requests(body["session_id"])
                required = [{"request_id": item["request_id"],
                             "request_revision": item["revision"]}
                            for item in latest]
                omissions = [{
                    "request_id": item["request_id"],
                    "request_revision": item["revision"],
                    "reason": "not_captured:%s" % item["status"].lower(),
                } for item in latest]
                status = "complete" if not latest else "incomplete"
                now_iso = _utcnow_iso(self._now())
                capture_set = {
                    "capture_set_id": str(uuid.uuid4()),
                    "session_id": body["session_id"], "revision": 1,
                    "created_at": now_iso, "status": status,
                    "required_requests": required, "receipts": [],
                    "omissions": omissions, "calibration_assets": [],
                }
                manifest = hashlib.sha256(
                    canonical_bytes(capture_set)).hexdigest()
                capture_set["manifest_sha256"] = manifest
                new_revision = session[2] + 1
                new_state = ("COMPLETE" if status == "complete"
                             else "COMPLETE_WITH_SKIPS")
                self._conn.execute(
                    "INSERT INTO capture_sets (capture_set_id, revision,"
                    " session_id, created_at, status, required_json,"
                    " receipts_json, omissions_json, calibration_assets_json,"
                    " manifest_sha256) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (capture_set["capture_set_id"], body["session_id"],
                     now_iso, status, json.dumps(required),
                     json.dumps([]), json.dumps(omissions),
                     json.dumps([]), manifest))
                self._conn.execute(
                    "UPDATE sessions SET revision = ?, state = ?"
                    " WHERE session_id = ?",
                    (new_revision, new_state, body["session_id"]))
                self._remember(principal, body["session_id"],
                               body["operation_id"], "finalize", digest,
                               capture_set, now_iso)
                self._record_change(body["session_id"], "session_changed",
                                    new_revision)
            return capture_set
