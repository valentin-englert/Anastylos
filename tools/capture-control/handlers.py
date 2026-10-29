#!/usr/bin/env python3
"""Narrow capture tool handlers for the MCP adapter. Stdlib only.

Each ``capture_*`` function validates its input envelope, performs
exactly one receiver call through :class:`ControlClient`, and returns
a JSON-serializable structured result. No filesystem, shell, or
provider access lives here. Raw image bytes are never returned unless
the caller passes ``include_images=True`` *and* the operator enabled
``ANASTYLOS_PROVIDER_IMAGES=authorized``; otherwise only IDs/hashes
cross the tool boundary.
"""

from __future__ import annotations

import os
import re
import uuid

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$")


class ToolInputError(ValueError):
    """Caller-side input rejection (before any receiver call)."""


def _uuid(value, name):
    if not isinstance(value, str) or UUID_RE.match(value) is None:
        raise ToolInputError("%s must be a UUID" % name)
    return value


def _operation_id(value):
    if value is None:
        return str(uuid.uuid4())
    return _uuid(value, "operation_id")


def _images_allowed():
    return os.environ.get("ANASTYLOS_PROVIDER_IMAGES") == "authorized"


def capture_capabilities(client, token, source_id):
    """Scoped source capabilities (read-only)."""
    _uuid(source_id, "source_id")
    return client.capabilities(token, source_id)


def capture_open_session(client, bootstrap_token, source_id, max_images,
                         expires_at, operation_id=None):
    """Create a DISARMED session pending phone consent."""
    _uuid(source_id, "source_id")
    if not isinstance(max_images, int) or not 1 <= max_images <= 20:
        raise ToolInputError("max_images must be 1..20")
    return client.open_session(
        bootstrap_token,
        {"operation_id": _operation_id(operation_id),
         "source_id": source_id, "max_images": max_images,
         "expires_at": expires_at})


def capture_resume_session(client, token, session_id,
                           expected_session_revision, operation_id=None):
    """Recover a session snapshot (never auto-rearms the phone)."""
    _uuid(session_id, "session_id")
    return client.resume(
        token, session_id,
        {"operation_id": _operation_id(operation_id),
         "session_id": session_id,
         "expected_session_revision": expected_session_revision})


def capture_request_view(client, token, body):
    """Queue one authoritative view request (CAS on the session)."""
    if not isinstance(body, dict):
        raise ToolInputError("request body must be an object")
    for key in ("session_id", "source_id", "operation_id"):
        if key in body:
            _uuid(body[key], key)
    session_id = _uuid(body.get("session_id"), "session_id")
    return client.request_view(token, session_id, body)


def capture_status(client, token, session_id, after="0"):
    """Session snapshot plus the next change page (bounded poll)."""
    _uuid(session_id, "session_id")
    snapshot = client.get_session(token, session_id)
    page = client.changes(token, session_id, after=after)
    return {"session": snapshot["session"], "requests": snapshot["requests"],
            "latest_status": snapshot["latest_status"],
            "receipts": snapshot["receipts"], "changes": page}


def capture_revise_view(client, token, body):
    """Replace a request target as a new immutable revision."""
    if not isinstance(body, dict):
        raise ToolInputError("request body must be an object")
    request_id = _uuid(body.get("request_id"), "request_id")
    return client.revise_view(token, request_id, body)


def capture_cancel_view(client, token, body):
    """Cancel one request (terminal stays terminal)."""
    if not isinstance(body, dict):
        raise ToolInputError("request body must be an object")
    request_id = _uuid(body.get("request_id"), "request_id")
    return client.cancel_view(token, request_id, body)


def capture_evidence(client, token, session_id, request_id=None,
                     include_images=False, limit=20):
    """Scoped receipt/asset listing; bytes only when explicitly allowed."""
    _uuid(session_id, "session_id")
    if request_id is not None:
        _uuid(request_id, "request_id")
    if include_images and not _images_allowed():
        raise ToolInputError(
            "include_images needs operator opt-in "
            "(ANASTYLOS_PROVIDER_IMAGES=authorized)")
    snapshot = client.get_session(token, session_id)
    receipts = [item for item in snapshot["receipts"]
                if request_id is None or item["request_id"] == request_id]
    receipts = receipts[:max(0, min(limit, 60))]
    assets = []
    for item in receipts:
        entry = {"asset_id": item["image_id"],
                 "sha256": item["image_sha256"],
                 "receipt_status": item["status"],
                 "resource_uri": "anastylos://%s/assets/%s"
                 % (session_id, item["image_id"])}
        if include_images and item["status"] in ("package_only",
                                                 "qualified"):
            data, digest = client.asset_bytes(token, session_id,
                                              item["image_id"])
            entry["bytes_hex"] = data.hex()
            entry["byte_length"] = len(data)
            entry["sha256"] = digest
        assets.append(entry)
    return {"receipts": receipts, "assets": assets,
            "provider_image_authorized": bool(include_images)}


def capture_finish_set(client, token, session_id,
                       expected_session_revision, operation_id=None):
    """Seal the capture set (the only sealing operation)."""
    _uuid(session_id, "session_id")
    return client.finalize(
        token, session_id,
        {"operation_id": _operation_id(operation_id),
         "session_id": session_id,
         "expected_session_revision": expected_session_revision})


TOOL_NAMES = (
    "capture.capabilities", "capture.open_session",
    "capture.resume_session", "capture.request_view", "capture.status",
    "capture.revise_view", "capture.cancel_view", "capture.evidence",
    "capture.finish_set",
)
