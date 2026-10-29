#!/usr/bin/env python3
"""Scoped authentication for guided control routes. Standard library only.

Legacy device tokens (``tokens.json``) keep their existing behavior on
legacy routes and grant zero guided privileges: guided routes consult
only ``guided-tokens.json`` here, and legacy routes never consult it.
The single exception is ``POST /v1/guided/enroll``, which exchanges an
operator-enrolled legacy token for a narrow source capability.

Stores (under ``--data-dir``, alongside the existing JSON state)::

    guided-sources.json   # { source_id: enrollment record }
    guided-tokens.json    # { sha256(token): capability record }

Roles: ``source`` (one phone identity), ``adapter_bootstrap`` (pending
sessions + claim only), ``adapter_session`` (one session), ``spatial``
and ``reader`` (sidecar upload / scoped reads for one session).
Only hashes are stored; plaintext tokens are shown once at mint time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

CONTRACT_SHA = "ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663"

SOURCE_TOKEN_DAYS = 30
BOOTSTRAP_TOKEN_HOURS = 24
SESSION_TOKEN_HOURS = 24
READER_TOKEN_HOURS = 24
_TOKEN_BYTES = 32


def _utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class GuidedAuth:
    """Enrollment registry plus hashed scoped-token store."""

    def __init__(self, data_dir):
        from threading import Lock
        self.data_dir = Path(data_dir)
        self._lock = Lock()

    # -- enrollment --------------------------------------------------------

    def _read_sources(self):
        path = self.data_dir / "guided-sources.json"
        if not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _write_sources(self, payload):
        path = self.data_dir / "guided-sources.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def enroll_source(self, label, legacy_token_sha256):
        """Bind a fresh source_id to one enrolled legacy token (idempotent)."""
        with self._lock:
            sources = self._read_sources()
            for source_id, record in sources.items():
                if (isinstance(record, dict) and record.get("legacy_sha256")
                        == legacy_token_sha256):
                    return source_id, record
            source_id = str(uuid.uuid4())
            record = {
                "label": label, "legacy_sha256": legacy_token_sha256,
                "enrolled_at": _utcnow_iso(), "exchanged": False,
            }
            sources[source_id] = record
            self._write_sources(sources)
            return source_id, record

    def enrollment_for_legacy(self, legacy_token_sha256):
        with self._lock:
            for source_id, record in self._read_sources().items():
                if (isinstance(record, dict) and record.get("legacy_sha256")
                        == legacy_token_sha256):
                    return source_id, dict(record)
        return None, None

    def mark_exchanged(self, source_id):
        with self._lock:
            sources = self._read_sources()
            record = sources.get(source_id)
            if not isinstance(record, dict):
                return False
            record["exchanged"] = True
            self._write_sources(sources)
            return True

    def list_sources(self):
        with self._lock:
            return {key: {"label": value.get("label"),
                          "enrolled_at": value.get("enrolled_at"),
                          "exchanged": bool(value.get("exchanged"))}
                    for key, value in self._read_sources().items()
                    if isinstance(value, dict)}

    # -- tokens --------------------------------------------------------------

    def _read_tokens(self):
        path = self.data_dir / "guided-tokens.json"
        if not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _write_tokens(self, payload):
        path = self.data_dir / "guided-tokens.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def mint(self, role, ttl_seconds, principal=None, source_id=None,
             session_id=None, grant_id=None):
        """Mint one capability; returns (plaintext token, record)."""
        token = secrets.token_hex(_TOKEN_BYTES)
        now = time.time()
        record = {
            "role": role, "principal": principal, "source_id": source_id,
            "session_id": session_id, "grant_id": grant_id,
            "created_at": _utcnow_iso(), "expires_at": now + ttl_seconds,
            "revoked": False,
        }
        with self._lock:
            tokens = self._read_tokens()
            tokens[_sha(token)] = record
            self._write_tokens(tokens)
        return token, dict(record)

    def check(self, presented):
        """Validate a guided token; returns its record or None."""
        if not presented:
            return None
        candidate = _sha(presented)
        now = time.time()
        with self._lock:
            tokens = self._read_tokens()
            for stored, record in tokens.items():
                if not hmac.compare_digest(candidate, stored):
                    continue
                if not isinstance(record, dict) or record.get("revoked"):
                    return None
                if float(record.get("expires_at", 0)) <= now:
                    return None
                return dict(record)
        return None

    def revoke_session_tokens(self, session_id):
        with self._lock:
            tokens = self._read_tokens()
            count = 0
            for record in tokens.values():
                if (isinstance(record, dict)
                        and record.get("session_id") == session_id
                        and not record.get("revoked")):
                    record["revoked"] = True
                    count += 1
            self._write_tokens(tokens)
            return count

    def list_tokens(self):
        """Redacted inventory for the operator (no hashes, no secrets)."""
        with self._lock:
            return [{"role": record.get("role"),
                     "principal": record.get("principal"),
                     "source_id": record.get("source_id"),
                     "session_id": record.get("session_id"),
                     "created_at": record.get("created_at"),
                     "revoked": bool(record.get("revoked"))}
                    for record in self._read_tokens().values()
                    if isinstance(record, dict)]
