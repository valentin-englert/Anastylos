#!/usr/bin/env python3
"""Guided control HTTP routes for the capture receiver. Stdlib only.

Thin translation between the frozen G0 wire contract and
:class:`anastylos_control_journal.ControlJournal`: scoped
authentication via :mod:`anastylos_guided_auth`, strict JSON bodies,
URL/body ID matching, and journal-error → HTTP mapping. No business
rules live here; the journal owns them.

Error mapping (documented, frozen-shape ``{"error": {...}}`` envelope
is the legacy one; guided errors use ``code``/``message`` plus
``current_revision`` where the journal supplies one):

- ``invalid_input`` → 400, ``revision_conflict``/``idempotency_conflict``
  /``unarmed`` → 409, ``expired`` → 410, ``busy`` → 429,
  ``unauthorized``/``not_found`` → one generic 404 after authentication
  (no oracle), missing/invalid Bearer → 401.

Legacy tokens get 401 on every guided route except ``POST
/v1/guided/enroll`` (the sole exchange); guided tokens get 401 on
legacy routes because the stores are separate.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import zipfile
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import anastylos_control_journal as journal
import anastylos_guided_auth as auth

GUIDED_BODY_MAX = 64 * 1024
SPATIAL_BODY_MAX = 320 * 1024

_GENERIC_404 = "Unknown session, grant, request, or attempt."


def get_journal(state):
    """One journal per data-dir, created under the state lock."""
    cached = vars(state).get("_guided_journal")
    if cached is not None:
        return cached
    with state.lock:
        cached = vars(state).get("_guided_journal")
        if cached is None:
            import anastylos_validator_seam as seam

            def _runner(payload):
                return seam.run_validator(payload, state.data_dir)

            cached = journal.ControlJournal(
                state.data_dir / "control.db",
                contract_sha=auth.CONTRACT_SHA,
                validator_runner=_runner)
            vars(state)["_guided_journal"] = cached
        return cached


def get_auth(state):
    cached = vars(state).get("_guided_auth")
    if cached is None:
        with state.lock:
            cached = vars(state).get("_guided_auth")
            if cached is None:
                cached = auth.GuidedAuth(state.data_dir)
                vars(state)["_guided_auth"] = cached
    return cached


def _bearer(headers):
    value = headers.get("Authorization") or ""
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


class GuidedRoutes:
    """Route handlers bound to one live request handler."""

    def __init__(self, handler):
        self.h = handler
        self.state = handler.state
        self.journal = get_journal(self.state)
        self.auth = get_auth(self.state)

    # -- plumbing ------------------------------------------------------

    def _send(self, status, payload, extra=None):
        self.h._send_json(status, payload, extra)

    def _fail(self, status, code, message, extra=None):
        self.h._send_json(status, {"error": {"code": code,
                                             "message": message}}, extra)

    def _unauthenticated(self):
        self._fail(HTTPStatus.UNAUTHORIZED, "unauthorized",
                   "Valid guided Bearer [REDACTED] required.",
                   {"WWW-Authenticate": "Bearer"})

    def _require_guided(self, *roles):
        record = self.auth.check(_bearer(self.h.headers))
        if record is None:
            self._unauthenticated()
            return None
        if roles and record.get("role") not in roles:
            self._fail(HTTPStatus.NOT_FOUND, "not_found", _GENERIC_404)
            return None
        return record

    def _require_legacy(self):
        token = _bearer(self.h.headers)
        if not token or not self.state.check_token(token):
            self._unauthenticated()
            return None
        return token

    def _read_json(self, limit):
        try:
            length = int(self.h.headers.get("Content-Length", "0"))
        except ValueError:
            self._fail(HTTPStatus.BAD_REQUEST, "bad_request",
                       "Content-Length must be a number.")
            return None
        if length <= 0 or length > limit:
            self._fail(HTTPStatus.BAD_REQUEST, "bad_request",
                       "Control body must be small JSON.")
            return None
        try:
            raw = self.h.rfile.read(length)
        except OSError:
            self._fail(HTTPStatus.BAD_REQUEST, "interrupted",
                       "Request body was interrupted.")
            return None
        try:
            return journal.parse_strict_json(raw)
        except journal.ControlError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, "invalid_input", exc.message)
            return None

    def _journal_errors(self, func, *args, **kwargs):
        try:
            return func(*args, **kwargs), None
        except journal.ControlError as exc:
            mapping = {
                "invalid_input": (HTTPStatus.BAD_REQUEST, exc.message, None),
                "revision_conflict": (HTTPStatus.CONFLICT, exc.message, None),
                "idempotency_conflict": (HTTPStatus.CONFLICT, exc.message,
                                         None),
                "unarmed": (HTTPStatus.CONFLICT, exc.message, None),
                "expired": (HTTPStatus.GONE, exc.message, None),
                "busy": (HTTPStatus.TOO_MANY_REQUESTS, exc.message,
                         {"Retry-After": "1"}),
                "unauthorized": (HTTPStatus.NOT_FOUND, _GENERIC_404, None),
                "not_found": (HTTPStatus.NOT_FOUND, _GENERIC_404, None),
            }
            status, message, extra = mapping.get(
                exc.code, (HTTPStatus.INTERNAL_SERVER_ERROR,
                           "control failure", None))
            payload = {"error": {"code": exc.code, "message": message}}
            if exc.current_revision is not None:
                payload["error"]["current_revision"] = exc.current_revision
            self._send(status, payload, extra)
            return None, exc

    def _scope_session(self, record, session_id):
        role = record.get("role")
        if role == "source":
            return True
        if record.get("session_id") != session_id:
            self._fail(HTTPStatus.NOT_FOUND, "not_found", _GENERIC_404)
            return False
        return True

    # -- GET ---------------------------------------------------------------

    def dispatch_get(self, parsed):
        path = parsed.path
        if path == "/v1/guided/capabilities":
            return self._get_capabilities(parsed)
        if path.startswith("/v1/guided/sources/") and path.endswith(
                "/pending-sessions"):
            return self._get_pending(path)
        if path.startswith("/v1/guided/sessions/") and path.endswith(
                "/changes"):
            return self._get_changes(parsed)
        if "/v1/guided/sessions/" in path and "/assets/" in path:
            return self._get_asset(path)
        if path.startswith("/v1/guided/sessions/") and path.endswith(
                "/capture-set"):
            return self._get_capture_set(path)
        if path.startswith("/v1/guided/sessions/"):
            return self._get_session(path)
        return False

    def _session_id_from(self, path, suffix=""):
        rest = path[len("/v1/guided/sessions/"):]
        if suffix:
            if not rest.endswith(suffix):
                return None
            rest = rest[: -len(suffix)]
        if "/" in rest or not rest:
            return None
        return rest

    def _get_session(self, path):
        session_id = self._session_id_from(path)
        if session_id is None:
            return False
        record = self._require_guided("source", "adapter_session", "spatial",
                                      "reader")
        if record is None:
            return True
        if not self._scope_session(record, session_id):
            return True
        bound = (record.get("source_id")
                 if record.get("role") == "source" else None)
        snapshot, exc = self._journal_errors(self.journal.get_session,
                                             session_id, bound)
        if exc is not None:
            return True
        self._send(HTTPStatus.OK, snapshot)
        return True

    def _get_changes(self, parsed):
        session_id = self._session_id_from(parsed.path, "/changes")
        if session_id is None:
            return False
        record = self._require_guided("source", "adapter_session")
        if record is None:
            return True
        if not self._scope_session(record, session_id):
            return True
        query = parse_qs(parsed.query, keep_blank_values=True)
        after = query.get("after", ["0"])
        if len(after) != 1:
            self._fail(HTTPStatus.BAD_REQUEST, "bad_request",
                       "after must be a single cursor.")
            return True
        bound = (record.get("source_id")
                 if record.get("role") == "source" else None)
        page, exc = self._journal_errors(self.journal.get_changes,
                                         session_id, after[0], bound)
        if exc is not None:
            return True
        self._send(HTTPStatus.OK, page)
        return True

    def _get_capabilities(self, parsed):
        record = self._require_guided("source", "adapter_session",
                                      "adapter_bootstrap")
        if record is None:
            return True
        query = parse_qs(parsed.query, keep_blank_values=True)
        values = query.get("source_id", [])
        if len(values) != 1:
            self._fail(HTTPStatus.BAD_REQUEST, "bad_request",
                       "source_id must be a single UUID.")
            return True
        source_id = values[0]
        if record.get("role") == "source" and (
                record.get("source_id") != source_id):
            self._fail(HTTPStatus.NOT_FOUND, "not_found", _GENERIC_404)
            return True
        caps = self.journal.get_capabilities(source_id)
        if caps is None:
            self._send(HTTPStatus.OK, {
                "source_id": source_id, "updated_at": None, "stale": True,
                "camera_ids": [], "resolutions_px": [],
                "calibration_ids": [], "target_kinds": [],
                "quality_profile_ids": [], "app_version": "unpublished",
                "protocol_version": 1})
            return True
        self._send(HTTPStatus.OK, caps)
        return True

    def _get_pending(self, path):
        rest = path[len("/v1/guided/sources/"):]
        source_id = rest[: -len("/pending-sessions")]
        if not source_id or "/" in source_id:
            return False
        record = self._require_guided("source")
        if record is None:
            return True
        if record.get("source_id") != source_id:
            self._fail(HTTPStatus.NOT_FOUND, "not_found", _GENERIC_404)
            return True
        sessions = self.journal.list_pending_sessions(source_id)
        self._send(HTTPStatus.OK, {"sessions": sessions})
        return True

    def _get_capture_set(self, path):
        session_id = self._session_id_from(path, "/capture-set")
        if session_id is None:
            return False
        record = self._require_guided("source", "adapter_session", "spatial",
                                      "reader")
        if record is None:
            return True
        if not self._scope_session(record, session_id):
            return True
        capture_set = self.journal.get_capture_set(session_id)
        if capture_set is None:
            self._fail(HTTPStatus.NOT_FOUND, "not_found", _GENERIC_404)
            return True
        self._send(HTTPStatus.OK, capture_set)
        return True

    def _get_asset(self, path):
        rest = path[len("/v1/guided/sessions/"):]
        session_id, _, image_id = rest.partition("/assets/")
        if not session_id or not image_id or "/" in image_id:
            return False
        record = self._require_guided("adapter_session", "spatial", "reader")
        if record is None:
            return True
        if not self._scope_session(record, session_id):
            return True
        snapshot, exc = self._journal_errors(self.journal.get_session,
                                             session_id)
        if exc is not None:
            return True
        receipt = None
        for item in snapshot["receipts"]:
            if item["image_id"] == image_id and item["status"] in (
                    "package_only", "qualified"):
                receipt = item
                break
        if receipt is None:
            self._fail(HTTPStatus.NOT_FOUND, "not_found", _GENERIC_404)
            return True
        package_dir = self.state.packages_dir / receipt["package_id"]
        try:
            manifest = json.loads(
                (package_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "unreadable",
                       "Staged package is unreadable.")
            return True
        target = None
        for entry in manifest.get("files", []) or []:
            if (isinstance(entry, dict)
                    and entry.get("image_id") == image_id):
                target = entry
                break
        if target is None:
            self._fail(HTTPStatus.NOT_FOUND, "not_found", _GENERIC_404)
            return True
        try:
            resolved = (package_dir / target["path"]).resolve()
            base = self.state.packages_dir.resolve()
        except OSError:
            self._fail(HTTPStatus.NOT_FOUND, "not_found", _GENERIC_404)
            return True
        if base not in resolved.parents or not resolved.is_file():
            self._fail(HTTPStatus.NOT_FOUND, "not_found", _GENERIC_404)
            return True
        try:
            data = resolved.read_bytes()
        except OSError:
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "unreadable",
                       "Staged asset is unreadable.")
            return True
        import hashlib

        if hashlib.sha256(data).hexdigest() != receipt["image_sha256"]:
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "checksum_mismatch",
                       "Staged bytes fail re-hash.")
            return True
        self.h.send_response(HTTPStatus.OK)
        self.h.send_header("Content-Type", target.get("mime", "image/jpeg"))
        self.h.send_header("Content-Length", str(len(data)))
        self.h.send_header("X-Image-Sha256", receipt["image_sha256"])
        self.h.end_headers()
        self.h.wfile.write(data)
        return True

    # -- POST --------------------------------------------------------------

    def dispatch_post(self, parsed):
        path = parsed.path
        if path == "/v1/guided/enroll":
            return self._post_enroll()
        if path == "/v1/guided/sessions":
            return self._post_open_session()
        if path.startswith("/v1/guided/sources/") and path.endswith(
                "/capabilities"):
            return self._post_capabilities(path)
        if path.startswith("/v1/guided/grants/") and path.endswith("/claim"):
            return self._post_claim(path)
        if path.startswith("/v1/guided/requests/") and path.endswith(
                "/revise"):
            return self._post_revise(path)
        if path.startswith("/v1/guided/requests/") and path.endswith(
                "/cancel"):
            return self._post_cancel(path)
        if path.startswith("/v1/guided/attempts/") and path.endswith(
                "/asset-binding"):
            return self._post_binding(path)
        if path.startswith("/v1/guided/attempts/") and path.endswith(
                "/spatial"):
            return self._post_spatial(path)
        for suffix, method in (
                ("/requests", self._post_request),
                ("/events", self._post_event),
                ("/status", self._post_status),
                ("/consent", self._post_consent),
                ("/attempts", self._post_attempt),
                ("/lease", self._post_lease),
                ("/finalize", self._post_finalize),
                ("/resume", self._post_resume)):
            if path.startswith("/v1/guided/sessions/") and path.endswith(
                    suffix):
                session_id = self._session_id_from(path, suffix)
                if session_id is None:
                    return False
                return method(session_id)
        return False

    def _match_body_session(self, body, session_id):
        if body.get("session_id") != session_id:
            self._fail(HTTPStatus.BAD_REQUEST, "bad_request",
                       "URL and body session_id must match.")
            return False
        return True

    def _post_enroll(self):
        token = self._require_legacy()
        if token is None:
            return True
        import hashlib

        legacy_sha = hashlib.sha256(token.encode("utf-8")).hexdigest()
        found = self.auth.enrollment_for_legacy(legacy_sha)
        if found[0] is None:
            self._fail(HTTPStatus.FORBIDDEN, "not_enrolled",
                       "This device is not enrolled for guided capture.")
            return True
        source_id, record = found
        if record.get("exchanged"):
            self._fail(HTTPStatus.FORBIDDEN, "already_exchanged",
                       "This enrollment was already exchanged.")
            return True
        capability, entry = self.auth.mint(
            "source", auth.SOURCE_TOKEN_DAYS * 86400, source_id=source_id)
        self.auth.mark_exchanged(source_id)
        from datetime import datetime, timezone

        self._send(HTTPStatus.CREATED, {
            "source_id": source_id, "token": capability,
            "expires_at": datetime.fromtimestamp(
                entry["expires_at"], timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")})
        return True

    def _post_capabilities(self, path):
        rest = path[len("/v1/guided/sources/"):]
        source_id = rest[: -len("/capabilities")]
        if not source_id or "/" in source_id:
            return False
        record = self._require_guided("source")
        if record is None:
            return True
        if record.get("source_id") != source_id:
            self._fail(HTTPStatus.NOT_FOUND, "not_found", _GENERIC_404)
            return True
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        try:
            caps = self.journal.publish_capabilities(source_id, body)
        except journal.ControlError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, exc.code, exc.message)
            return True
        self._send(HTTPStatus.OK, caps)
        return True

    def _post_open_session(self):
        record = self._require_guided("adapter_bootstrap")
        if record is None:
            return True
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        session, exc = self._journal_errors(
            self.journal.open_session, record["principal"], body,
            body.get("source_id"))
        if exc is not None:
            return True
        self._send(HTTPStatus.CREATED, session)
        return True

    def _post_claim(self, path):
        grant_id = path[len("/v1/guided/grants/"): -len("/claim")]
        if not grant_id or "/" in grant_id:
            return False
        record = self._require_guided("adapter_bootstrap")
        if record is None:
            return True
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        if set(body) != {"operation_id"}:
            self._fail(HTTPStatus.BAD_REQUEST, "bad_request",
                       "claim body carries operation_id only.")
            return True
        claimed, exc = self._journal_errors(
            self.journal.claim_grant, record["principal"], grant_id,
            body["operation_id"])
        if exc is not None:
            return True
        token, entry = self.auth.mint(
            "adapter_session", auth.SESSION_TOKEN_HOURS * 3600,
            principal=record["principal"],
            session_id=claimed["session"]["session_id"], grant_id=grant_id)
        from datetime import datetime, timezone

        self._send(HTTPStatus.OK, {
            "token": token,
            "expires_at": datetime.fromtimestamp(
                entry["expires_at"], timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
            "session": claimed["session"], "grant": claimed["grant"]})
        return True

    def _post_request(self, session_id):
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        if not self._match_body_session(body, session_id):
            return True
        record = self._require_guided("adapter_session")
        if record is None:
            return True
        if not self._scope_session(record, session_id):
            return True
        snapshot, exc = self._journal_errors(self.journal.get_session,
                                             session_id)
        if exc is not None:
            return True
        out, exc = self._journal_errors(
            self.journal.request_view, record["principal"],
            snapshot["session"]["source_id"], body)
        if exc is not None:
            return True
        self._send(HTTPStatus.CREATED, out)
        return True

    def _post_revise(self, path):
        request_id = path[len("/v1/guided/requests/"): -len("/revise")]
        return self._mutate_request(request_id, "revise")

    def _post_cancel(self, path):
        request_id = path[len("/v1/guided/requests/"): -len("/cancel")]
        return self._mutate_request(request_id, "cancel")

    def _mutate_request(self, request_id, kind):
        if not request_id or "/" in request_id:
            return False
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        if body.get("request_id") != request_id:
            self._fail(HTTPStatus.BAD_REQUEST, "bad_request",
                       "URL and body request_id must match.")
            return True
        record = self._require_guided("adapter_session")
        if record is None:
            return True
        if not self._scope_session(record, body.get("session_id")):
            return True
        snapshot, exc = self._journal_errors(self.journal.get_session,
                                             body["session_id"])
        if exc is not None:
            return True
        func = (self.journal.revise_view if kind == "revise"
                else self.journal.cancel_view)
        out, exc = self._journal_errors(
            func, record["principal"],
            snapshot["session"]["source_id"], body)
        if exc is not None:
            return True
        self._send(HTTPStatus.OK, out)
        return True

    def _post_event(self, session_id):
        record = self._require_guided("source")
        if record is None:
            return True
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        if not self._match_body_session(body, session_id):
            return True
        ack, exc = self._journal_errors(self.journal.record_event,
                                        record["source_id"], body)
        if exc is not None:
            return True
        self._send(HTTPStatus.OK, ack)
        return True

    def _post_status(self, session_id):
        record = self._require_guided("source")
        if record is None:
            return True
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        if not self._match_body_session(body, session_id):
            return True
        ack, exc = self._journal_errors(self.journal.post_status,
                                        record["source_id"], body)
        if exc is not None:
            return True
        self._send(HTTPStatus.OK, ack)
        return True

    def _post_consent(self, session_id):
        record = self._require_guided("source")
        if record is None:
            return True
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        if not self._match_body_session(body, session_id):
            return True
        session, exc = self._journal_errors(self.journal.consent,
                                            record["source_id"], body)
        if exc is not None:
            return True
        self._send(HTTPStatus.OK, session)
        return True

    def _post_attempt(self, session_id):
        record = self._require_guided("source")
        if record is None:
            return True
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        if not self._match_body_session(body, session_id):
            return True
        ack, exc = self._journal_errors(self.journal.register_attempt,
                                        record["source_id"], body)
        if exc is not None:
            return True
        self._send(HTTPStatus.CREATED, ack)
        return True

    def _post_binding(self, path):
        attempt_id = path[len("/v1/guided/attempts/"):
                          -len("/asset-binding")]
        if not attempt_id or "/" in attempt_id:
            return False
        record = self._require_guided("source")
        if record is None:
            return True
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        if body.get("attempt_id") != attempt_id:
            self._fail(HTTPStatus.BAD_REQUEST, "bad_request",
                       "URL and body attempt_id must match.")
            return True
        ack, exc = self._journal_errors(self.journal.bind_asset,
                                        record["source_id"], body)
        if exc is not None:
            return True
        self._send(HTTPStatus.OK, ack)
        return True

    def _post_spatial(self, path):
        attempt_id = path[len("/v1/guided/attempts/"): -len("/spatial")]
        if not attempt_id or "/" in attempt_id:
            return False
        record = self._require_guided("source", "spatial")
        if record is None:
            return True
        body = self._read_json(SPATIAL_BODY_MAX)
        if body is None:
            return True
        if record["role"] == "source":
            out, exc = self._journal_errors(
                self.journal.upload_sidecar, record["source_id"], None,
                attempt_id, body, "source")
        else:
            out, exc = self._journal_errors(
                self.journal.upload_sidecar, None, record["session_id"],
                attempt_id, body, "spatial")
        if exc is not None:
            return True
        if isinstance(out, dict) and "receipt_id" in out:
            out = self.journal.maybe_qualify(out["receipt_id"])
        self._send(HTTPStatus.OK, out)
        return True

    def _post_lease(self, session_id):
        record = self._require_guided("adapter_session")
        if record is None:
            return True
        if not self._scope_session(record, session_id):
            return True
        lease, exc = self._journal_errors(self.journal.renew_lease,
                                          session_id, record["grant_id"])
        if exc is not None:
            return True
        self._send(HTTPStatus.OK, lease)
        return True

    def _post_finalize(self, session_id):
        record = self._require_guided("adapter_session")
        if record is None:
            return True
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        if not self._match_body_session(body, session_id):
            return True
        if not self._scope_session(record, session_id):
            return True
        capture_set, exc = self._journal_errors(
            self.journal.finalize, record["principal"], body)
        if exc is not None:
            return True
        self._send(HTTPStatus.OK, capture_set)
        return True

    def _post_resume(self, session_id):
        record = self._require_guided("adapter_session")
        if record is None:
            return True
        body = self._read_json(GUIDED_BODY_MAX)
        if body is None:
            return True
        if not self._match_body_session(body, session_id):
            return True
        if not self._scope_session(record, session_id):
            return True
        snapshot, exc = self._journal_errors(
            self.journal.resume, record["principal"], body)
        if exc is not None:
            return True
        self._send(HTTPStatus.OK, snapshot)
        return True

    # -- guided upload (POST /v1/packages with X-Guided-* headers) ----------

    def handle_guided_upload(self):
        import anastylos_capture_receiver as receiver

        record = self._require_guided("source")
        if record is None:
            return
        headers = self.h.headers
        package_id = (headers.get("X-Package-Id") or "").strip()
        expected_sha = (headers.get("X-Package-Sha256") or "").strip().lower()
        session_id = (headers.get("X-Guided-Session-Id") or "").strip()
        request_id = (headers.get("X-Guided-Request-Id") or "").strip()
        revision_raw = (headers.get("X-Guided-Request-Revision") or "").strip()
        attempt_id = (headers.get("X-Guided-Attempt-Id") or "").strip()
        import re as _re

        uuid_re = _re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$")
        hex_re = _re.compile(r"^[0-9a-f]{64}$")
        try:
            revision = int(revision_raw)
        except ValueError:
            revision = -1
        if not (uuid_re.match(package_id) and hex_re.match(expected_sha)
                and uuid_re.match(session_id) and uuid_re.match(request_id)
                and revision >= 1 and uuid_re.match(attempt_id)):
            self._fail(HTTPStatus.BAD_REQUEST, "bad_metadata",
                       "Guided upload needs valid X-Package-* and "
                       "X-Guided-Session/Request/Revision/Attempt headers.")
            return
        attempt = self.journal.describe_attempt(attempt_id)
        if attempt is None or attempt["session_id"] != session_id:
            self._fail(HTTPStatus.CONFLICT, "revision_conflict",
                       "Unknown attempt; reconcile and retry.")
            return
        if (attempt["request_id"], attempt["request_revision"]) != (
                request_id, revision):
            self._fail(HTTPStatus.CONFLICT, "revision_conflict",
                       "Headers do not match the attempt reservation.")
            return
        if attempt["status"] != "asset_bound":
            self._fail(HTTPStatus.CONFLICT, "revision_conflict",
                       "Bind assets before uploading.")
            return
        if (attempt["package_id"], attempt["package_sha256"]) != (
                package_id, expected_sha):
            self._fail(HTTPStatus.CONFLICT, "idempotency_conflict",
                       "Package does not match its binding.")
            return
        try:
            length = int(headers.get("Content-Length", "0"))
        except ValueError:
            self._fail(HTTPStatus.BAD_REQUEST, "bad_request",
                       "Content-Length must be a number.")
            return
        if length <= 0:
            self._fail(HTTPStatus.BAD_REQUEST, "bad_request",
                       "Upload body is empty.")
            return
        if length > receiver.MAX_UPLOAD_BYTES:
            self._fail(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large",
                       "Upload exceeds the package size cap.")
            return
        try:
            received = self.h.rfile.read(length)
        except OSError:
            self._fail(HTTPStatus.BAD_REQUEST, "interrupted",
                       "Upload was interrupted; retry the whole package.")
            return
        if len(received) != length:
            self._fail(HTTPStatus.BAD_REQUEST, "interrupted",
                       "Upload was interrupted; retry the whole package.")
            return
        import hashlib
        import hmac

        if not hmac.compare_digest(hashlib.sha256(received).hexdigest(),
                                   expected_sha):
            self._fail(HTTPStatus.BAD_REQUEST, "checksum_mismatch",
                       "Upload bytes do not match X-Package-Sha256.")
            return

        def _join_receipt():
            return self.journal.accept_package(
                record["source_id"], attempt_id,
                {"package_id": attempt["package_id"],
                 "package_sha256": attempt["package_sha256"],
                 "observation_id": attempt["observation_id"],
                 "image_id": attempt["image_id"],
                 "image_sha256": attempt["image_sha256"]})

        known = self.state.note_duplicate(package_id)
        staged_report = self.state.packages_dir / package_id / "report.json"
        if known is not None or staged_report.is_file():
            report = self.h._stored_report(package_id)
            if report is None:
                self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "unreadable",
                           "Stored report is unreadable.")
                return
            try:
                receipt = _join_receipt()
            except journal.ControlError as exc:
                self._fail(HTTPStatus.CONFLICT, exc.code, exc.message)
                return
            self._send(HTTPStatus.OK, {"duplicate": True, "report": report,
                                       "receipt": receipt})
            return
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                    dir=str(self.state.packages_dir), prefix=".upload-",
                    suffix=".zip", delete=False) as tmp:
                tmp.write(received)
                tmp_path = Path(tmp.name)
            try:
                report = receiver.validator.validate_package(tmp_path)
            except Exception as exc:
                self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "io_error",
                           "Cannot validate the upload: %s." % exc)
                return
        except OSError as exc:
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "io_error",
                       "Cannot buffer the upload: %s." % exc)
            return
        assert tmp_path is not None
        try:
            manifest_id = report.get("package_id")
            if manifest_id is not None and manifest_id != package_id:
                self._fail(HTTPStatus.BAD_REQUEST, "bad_metadata",
                           "Manifest package_id disagrees with X-Package-Id.")
                return
            if report.get("status") != "valid":
                self._send(HTTPStatus.UNPROCESSABLE_ENTITY,
                           {"duplicate": False, "report": report})
                return
            try:
                with zipfile.ZipFile(tmp_path, "r") as zf:
                    manifest = json.loads(
                        zf.read("manifest.json").decode("utf-8"))
            except (KeyError, ValueError, zipfile.BadZipFile):
                self._fail(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid",
                           "Upload manifest is unreadable.")
                return
            sessions = [s.get("session_id") for s in
                        manifest.get("sessions", [])]
            if sessions != [session_id]:
                self._fail(HTTPStatus.CONFLICT, "idempotency_conflict",
                           "Package must contain only its granted session.")
                return
            joined = False
            for entry in manifest.get("files", []) or []:
                if (isinstance(entry, dict)
                        and entry.get("observation_id")
                        == attempt["observation_id"]
                        and entry.get("image_id") == attempt["image_id"]
                        and entry.get("sha256") == attempt["image_sha256"]):
                    joined = True
                    break
            if not joined:
                self._fail(HTTPStatus.CONFLICT, "idempotency_conflict",
                           "Package lacks its bound observation/image.")
                return
            target = self.state.packages_dir / package_id
            try:
                staged = receiver.validator.stage_package(
                    tmp_path, target, report)
            except ValueError:
                if target.is_dir():
                    stored = self.h._stored_report(package_id)
                    if stored is not None:
                        self.state.note_duplicate(package_id)
                        try:
                            receipt = _join_receipt()
                        except journal.ControlError as exc:
                            self._fail(HTTPStatus.CONFLICT, exc.code,
                                       exc.message)
                            return
                        self._send(HTTPStatus.OK,
                                   {"duplicate": True, "report": stored,
                                    "receipt": receipt})
                        return
                self._fail(HTTPStatus.CONFLICT, "staging_failed",
                           "Cannot stage the package.")
                return
            (target / "report.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8")
            (target / "package.zip").write_bytes(received)
            summary = {
                "staged_at": receiver.utcnow_iso(),
                "zip_sha256": expected_sha,
                "zip_bytes": len(received),
                "status": report.get("status"),
                "accepted_for_review": report.get("accepted_for_review", []),
                "observation_ids": [o.get("observation_id") for o in
                                    report.get("observations", [])],
                "session_ids": receiver.manifest_session_ids(received),
                "staged_files": staged,
                "duplicate_deliveries": 0,
                "last_seen_at": receiver.utcnow_iso(),
                "guided_attempt_id": attempt_id,
            }
            self.state.record_package(package_id, summary)
            try:
                receipt = _join_receipt()
            except journal.ControlError as exc:
                self._fail(HTTPStatus.CONFLICT, exc.code, exc.message)
                return
            self._send(HTTPStatus.CREATED,
                       {"duplicate": False, "report": report,
                        "receipt": receipt})
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def is_guided_upload(headers):
    return bool((headers.get("X-Guided-Attempt-Id") or "").strip()
                or (headers.get("X-Guided-Session-Id") or "").strip())


# -- operator CLI --------------------------------------------------------------


def cmd_guided_enroll(args):
    import anastylos_capture_receiver as receiver

    state = receiver.ReceiverState(args.data_dir)
    request_id, matches = state.resolve_request_prefix(args.request)
    if matches != 1 or request_id is None:
        print("error: prefix matches %d requests; be specific."
              % matches, file=sys.stderr)
        return 1
    entry = state.list_pair_requests().get(request_id)
    if entry is None or entry.get("status") != "approved" or not entry.get(
            "token"):
        print("error: request is not an approved pairing.", file=sys.stderr)
        return 1
    import hashlib

    legacy_sha = hashlib.sha256(entry["token"].encode("utf-8")).hexdigest()
    tokens = state._read_json("tokens.json") or {}
    if legacy_sha not in tokens:
        print("error: pairing token is unknown or revoked.", file=sys.stderr)
        return 1
    source_id, _record = get_auth(
        state).enroll_source(args.label or entry.get("device_label", "?"),
                             legacy_sha)
    print("source_id: %s" % source_id)
    print("device may now POST /v1/guided/enroll with its legacy token.")
    return 0


def cmd_guided_bootstrap(args):
    import anastylos_capture_receiver as receiver

    state = receiver.ReceiverState(args.data_dir)
    token, entry = get_auth(state).mint(
        "adapter_bootstrap", max(1, args.ttl_hours) * 3600,
        principal=args.principal)
    print("principal: %s" % args.principal)
    print("adapter bootstrap token (show once; export, never commit):")
    print(token)
    print("valid for %d hour(s)" % args.ttl_hours)
    return 0


def cmd_guided_reader(args):
    import anastylos_capture_receiver as receiver

    if args.role not in ("spatial", "reader"):
        print("error: role must be spatial or reader.", file=sys.stderr)
        return 2
    state = receiver.ReceiverState(args.data_dir)
    handle = get_journal(state)
    try:
        handle.get_session(args.session)
    except journal.ControlError:
        print("error: unknown session.", file=sys.stderr)
        return 1
    finally:
        handle.close()
    token, _entry = get_auth(state).mint(
        args.role, auth.READER_TOKEN_HOURS * 3600,
        session_id=args.session)
    print("%s token for %s (show once):" % (args.role, args.session))
    print(token)
    return 0


def cmd_guided_revoke_grant(args):
    import anastylos_capture_receiver as receiver

    state = receiver.ReceiverState(args.data_dir)
    handle = get_journal(state)
    try:
        grant = handle.revoke_grant(args.grant)
    except journal.ControlError:
        print("error: unknown grant.", file=sys.stderr)
        return 1
    finally:
        handle.close()
    print("grant %s revoked: %s" % (grant["grant_id"], grant["revoked"]))
    return 0


def cmd_guided_revoke_tokens(args):
    import anastylos_capture_receiver as receiver

    state = receiver.ReceiverState(args.data_dir)
    count = get_auth(state).revoke_session_tokens(args.session)
    print("revoked %d token(s) for session %s" % (count, args.session))
    return 0


def cmd_guided_status(args):
    import anastylos_capture_receiver as receiver

    state = receiver.ReceiverState(args.data_dir)
    payload = {"sources": get_auth(state).list_sources(),
               "tokens": get_auth(state).list_tokens()}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("guided sources: %d" % len(payload["sources"]))
        for source_id, info in sorted(payload["sources"].items()):
            print("  %s label=%s exchanged=%s" % (
                source_id, info["label"], info["exchanged"]))
        print("guided tokens: %d" % len(payload["tokens"]))
        for entry in payload["tokens"]:
            print("  role=%s session=%s revoked=%s" % (
                entry["role"], entry["session_id"], entry["revoked"]))
    return 0
