#!/usr/bin/env python3
"""ANASTYLOS CAPTURE workstation receiver. Standard library only.

Receives validated evidence packages from the Capture Companion PWA over a
private LAN, validates every upload with the capture validator
(``../python/anastylos_capture_validate.py``), and stages accepted evidence
for operator review. It never appends to ``memory.jsonl`` and never invents
identity: received evidence stays staged until a human reviews it.

Security model (see ``README.md`` and ``../SYNC_PROTOCOL.md`` for the full
analysis):

- TLS in production (operator-provided certificate). Plaintext exists only
  behind ``--plaintext-localhost`` for loopback development/tests and loudly
  refuses non-loopback binds.
- One-time pairing codes (single-use, short-lived, rate-limited) bootstrap
  a 256-bit bearer token per device. No shipped credentials, no
  unauthenticated upload endpoint.
- Browsers cannot pin certificates from JavaScript, so this receiver does
  not pretend they can: the operator compares the printed certificate
  fingerprint manually (trust-on-first-use) or installs the workstation CA
  on the phone.

Data layout under ``--data-dir``::

    config.json                 # { workstation_id, created_at }
    pairing.json                # single-use code verifier + rate-limit state
    tokens.json                 # { sha256(token): { created_at, label } }
    index.json                  # { packages: { package_id: summary } }
    packages/<package_id>/      # package.zip + report.json + staged members

Exit codes: 0 ok, 1 operational failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import re
import secrets
import ssl
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RECEIVER_DIR = Path(__file__).resolve().parent
VALIDATOR_DIR = RECEIVER_DIR.parent / "python"
sys.path.insert(0, str(VALIDATOR_DIR))

import anastylos_capture_validate as validator  # noqa: E402  (sibling import by design)
import anastylos_qr as qr_encode  # noqa: E402  (same directory by design)
import anastylos_guided_routes as guided  # noqa: E402  (same directory by design)
import anastylos_voice_routes as voice  # noqa: E402  (same directory by design)
import anastylos_video_routes as video  # noqa: E402  (same directory by design)

SERVICE_NAME = "anastylos-capture-receiver"
API_VERSION = 1

# Pairing codes: 6 words from a 256-word list -> 48 bits of entropy.
# Single-use, short-lived and rate-limited (5 attempts, then lockout), so
# online guessing is infeasible; see README.md for the analysis.
PAIRING_WORDS = 6
PAIRING_TTL_SECONDS = 5 * 60
PAIRING_MAX_ATTEMPTS = 5
PAIRING_LOCKOUT_SECONDS = 15 * 60

_TOKEN_BYTES = 32  # 256-bit bearer tokens.

# The upload body must fit the validator's package cap.
MAX_UPLOAD_BYTES = validator.MAX_PACKAGE_BYTES

# QR pairing payload, version 1: compact JSON the phone scans.
# {"p":"anastylos-pair","v":1,"w":workstation_id,"u":base_url,
#  "s":spki_sha256_hex,"c":pairing_code,"e":expires_at_unix}
# The Android parser (QrPayload.kt) validates this strictly; keep the two
# in sync. Must fit the encoder's largest symbol (V13-M byte mode).
QR_PAIR_PROTOCOL = "anastylos-pair"
QR_PAIR_VERSION = 1
QR_REQUEST_ID_BYTES = 16  # 128-bit poll capability.
QR_DEVICE_LABEL_MAX = 64
QR_BASE_URL_MAX = 120


def _wordlist() -> tuple[str, ...]:
    first = (
        "amber", "basalt", "cedar", "drift", "ember", "flint", "grove",
        "harbor", "indigo", "juniper", "kelp", "linen", "moss", "north",
        "oasis", "prairie",
    )
    second = (
        "fox", "heron", "otter", "wren", "badger", "cove", "dune", "elm",
        "finch", "gull", "hare", "ibex", "jay", "koala", "lark", "mole",
    )
    return tuple(a + b for a in first for b in second)


WORDLIST = _wordlist()
assert len(set(WORDLIST)) == 256, "pairing wordlist must hold 256 unique words"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_loopback_host(host: str) -> bool:
    text = host.strip().lower().strip("[]")
    if text in ("localhost", "127.0.0.1", "::1"):
        return True
    if text.startswith("127."):
        return True
    return False


def normalize_pairing_code(raw: str) -> str:
    """Canonicalize operator-entered codes: case/whitespace tolerant."""
    parts = raw.lower().replace("-", " ").split()
    return " ".join(parts)


def mint_pairing_code() -> str:
    return " ".join(secrets.choice(WORDLIST) for _ in range(PAIRING_WORDS))


_CERT_BLOCK_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL
)


def cert_fingerprint_sha256(cert_path: Path) -> str:
    """SHA-256 fingerprint of the first certificate in a PEM file.

    Returned as lowercase hex pairs (``ab:cd:...``) for the operator to
    compare on the phone. The PWA cannot check this programmatically —
    browsers never expose server certificates to JavaScript — so the
    comparison is a manual trust-on-first-use step.
    """
    pem = cert_path.read_text(encoding="utf-8")
    match = _CERT_BLOCK_RE.search(pem)
    if not match:
        raise ValueError(f"no certificate found in {cert_path}")
    der = ssl.PEM_cert_to_DER_cert(match.group(0))
    digest = hashlib.sha256(der).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def _der_split(value: bytes) -> list[tuple[int, bytes, bytes]]:
    """Split constructed DER content into (tag, full, value) children."""
    children = []
    offset = 0
    while offset < len(value):
        if offset + 2 > len(value):
            raise ValueError("truncated DER node")
        tag = value[offset]
        first = value[offset + 1]
        if first & 0x80 == 0:
            length, header = first, 2
        else:
            size_len = first & 0x7F
            if size_len == 0 or size_len > 4 or offset + 2 + size_len > len(value):
                raise ValueError("bad DER length")
            length = int.from_bytes(value[offset + 2 : offset + 2 + size_len], "big")
            header = 2 + size_len
        end = offset + header + length
        if end > len(value):
            raise ValueError("DER node overruns its parent")
        children.append((tag, value[offset:end], value[offset + header : end]))
        offset = end
    return children


def spki_sha256_hex(cert_path: Path) -> str:
    """SHA-256 over the certificate's SubjectPublicKeyInfo DER (lowercase hex).

    This is the machine-checked pin the QR pairing bootstrap carries: it
    survives certificate renewal as long as the key pair is kept, unlike
    the whole-certificate fingerprint above. Extracted with a minimal DER
    walker that understands just enough X.509 to find the SPKI child
    (version?, serial, algorithm, issuer, validity, subject, spki, ...);
    anything surprising raises instead of guessing. Key-type agnostic —
    the SPKI bytes are never parsed, only hashed.
    """
    pem = cert_path.read_text(encoding="utf-8")
    match = _CERT_BLOCK_RE.search(pem)
    if not match:
        raise ValueError(f"no certificate found in {cert_path}")
    try:
        der = ssl.PEM_cert_to_DER_cert(match.group(0))
        top = _der_split(der)
        if len(top) != 1 or top[0][0] != 0x30:
            raise ValueError("not a DER certificate")
        body = _der_split(top[0][2])
        if len(body) != 3 or body[0][0] != 0x30:
            raise ValueError("not a DER certificate")
        tbs = _der_split(body[0][2])
        index = 1 if tbs and tbs[0][0] == 0xA0 else 0
        want = [0x02, 0x30, 0x30, 0x30, 0x30, 0x30]
        got = [tag for tag, _, _ in tbs[index : index + 6]]
        if got != want:
            raise ValueError("unexpected tbsCertificate shape")
        spki_der = tbs[index + 5][1]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"cannot parse certificate in {cert_path}: {exc}") from exc
    return hashlib.sha256(spki_der).hexdigest()


_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def clean_device_label(raw: object) -> str | None:
    """Validate a phone-supplied device label; None when unacceptable."""
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.split())
    if not text or len(text) > QR_DEVICE_LABEL_MAX:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    return text


def build_qr_payload(
    *,
    workstation_id: str,
    base_url: str,
    spki_hex: str,
    code: str,
    expires_at: float,
) -> str:
    """Build the versioned QR pairing payload (compact ASCII JSON).

    Raises ValueError on any field the phone would reject, so a bad QR is
    never printed. Carries the single-use pairing code and the SPKI pin —
    never a Bearer [REDACTED] (issued only after operator approval).
    """
    if not isinstance(workstation_id, str) or not workstation_id.strip() \
            or len(workstation_id) > 128:
        raise ValueError("workstation_id must be 1..128 characters")
    if not isinstance(base_url, str) or len(base_url) > QR_BASE_URL_MAX:
        raise ValueError(f"base_url must fit {QR_BASE_URL_MAX} characters")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("base_url must be an https:// URL with a host")
    if not isinstance(spki_hex, str) or _HEX64_RE.match(spki_hex) is None:
        raise ValueError("spki must be 64 lowercase hex characters")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("pairing code is required")
    if not isinstance(expires_at, (int, float)) or expires_at <= 0:
        raise ValueError("expires_at must be a positive unix time")
    payload = json.dumps(
        {
            "p": QR_PAIR_PROTOCOL,
            "v": QR_PAIR_VERSION,
            "w": workstation_id,
            "u": base_url,
            "s": spki_hex,
            "c": " ".join(code.split()),
            "e": int(expires_at),
        },
        separators=(",", ":"),
    )
    if not payload.isascii():
        raise ValueError("payload must be ASCII")
    capacity = qr_encode.BYTE_CAPACITY_M[13]
    if len(payload) > capacity:
        raise ValueError(f"payload is {len(payload)} bytes; V13-M holds {capacity}")
    return payload


# ---------------------------------------------------------------------------
# Persistent state (JSON files under the data dir, guarded by one lock)
# ---------------------------------------------------------------------------


class ReceiverState:
    """Workstation identity, pairing codes, tokens and the staged index."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.packages_dir = data_dir / "packages"
        self.lock = threading.Lock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)

    # -- small JSON helpers -------------------------------------------------

    def _read_json(self, name: str) -> dict | None:
        path = self.data_dir / name
        if not path.is_file():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def _write_json(self, name: str, payload: dict) -> None:
        path = self.data_dir / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    # -- workstation identity -----------------------------------------------

    def workstation_id(self) -> str:
        with self.lock:
            config = self._read_json("config.json") or {}
            ident = config.get("workstation_id")
            if not isinstance(ident, str) or not ident:
                ident = uuid.uuid4().hex
                config = {"workstation_id": ident, "created_at": utcnow_iso()}
                self._write_json("config.json", config)
            return ident

    # -- pairing --------------------------------------------------------------

    def mint_code(self, ttl_seconds: int = PAIRING_TTL_SECONDS) -> tuple[str, dict]:
        """Create a fresh single-use code, invalidating any previous one."""
        code = mint_pairing_code()
        now = time.time()
        record = {
            "code_sha256": sha256_hex(normalize_pairing_code(code).encode("utf-8")),
            "created_at": utcnow_iso(),
            "expires_at": now + ttl_seconds,
            "attempts": [],
            "locked_until": 0.0,
            "consumed": False,
        }
        with self.lock:
            self._write_json("pairing.json", record)
        return code, record

    def pairing_status(self) -> dict:
        with self.lock:
            record = self._read_json("pairing.json") or {}
        now = time.time()
        return {
            "active": bool(record)
            and not record.get("consumed", False)
            and float(record.get("expires_at", 0)) > now
            and float(record.get("locked_until", 0)) <= now,
            "expires_at": record.get("expires_at", 0),
            "locked_until": record.get("locked_until", 0),
        }

    def _redeem_locked(self, presented: str, now: float) -> tuple[bool, str, float, dict | None]:
        """Check a pairing code; caller must hold the lock.

        Returns (ok, reason, retry_after, record). On success the code is
        marked consumed and the record returned for the caller to act on.
        Legacy `/v1/pair` and QR `/v1/pair/qr` share this: one code pool,
        one lockout state, single-use across both endpoints.
        """
        record = self._read_json("pairing.json")
        if not record:
            return False, "no active pairing code", 0.0, None
        if record.get("consumed", False):
            return False, "pairing code already used", 0.0, None
        if float(record.get("expires_at", 0)) <= now:
            return False, "pairing code expired", 0.0, None
        locked_until = float(record.get("locked_until", 0))
        if locked_until > now:
            return False, "too many attempts", locked_until - now, None
        attempts = [t for t in record.get("attempts", []) if now - float(t) < 3600]
        candidate = sha256_hex(normalize_pairing_code(presented).encode("utf-8"))
        if hmac.compare_digest(candidate, str(record.get("code_sha256", ""))):
            record["consumed"] = True
            record["attempts"] = []
            self._write_json("pairing.json", record)
            return True, "paired", 0.0, record
        attempts.append(now)
        record["attempts"] = attempts
        if len(attempts) >= PAIRING_MAX_ATTEMPTS:
            record["locked_until"] = now + PAIRING_LOCKOUT_SECONDS
            self._write_json("pairing.json", record)
            return False, "too many attempts", float(PAIRING_LOCKOUT_SECONDS), None
        self._write_json("pairing.json", record)
        return False, "pairing code rejected", 0.0, None

    def redeem_code(self, presented: str) -> tuple[bool, str, float]:
        """Check a pairing code. Returns (ok, reason, retry_after)."""
        with self.lock:
            ok, reason, retry_after, _record = self._redeem_locked(presented, time.time())
        return ok, reason, retry_after

    # -- QR pairing requests (two-phase: redeem, then operator approval) -----

    def _pair_requests_locked(self, now: float) -> dict:
        """Unexpired requests; caller must hold the lock. Sweeps expired."""
        stored = self._read_json("pairing-requests.json") or {}
        requests = stored.get("requests")
        if not isinstance(requests, dict):
            return {}
        live = {
            request_id: entry
            for request_id, entry in requests.items()
            if isinstance(entry, dict) and float(entry.get("expires_at", 0)) > now
        }
        if len(live) != len(requests):
            self._write_json("pairing-requests.json", {"requests": live})
        return live

    def redeem_code_request(
        self, presented: str, device_label: str
    ) -> tuple[bool, str, float, dict | None]:
        """Redeem a code into a pending approval request.

        Returns (ok, reason, retry_after, summary). The code is consumed
        and the request created atomically: no orphan requests, no double
        redeem. The summary carries request_id + expires_at only — the
        token is issued at approval time, never here.
        """
        now = time.time()
        with self.lock:
            ok, reason, retry_after, record = self._redeem_locked(presented, now)
            if not ok:
                return ok, reason, retry_after, None
            requests = self._pair_requests_locked(now)
            request_id = secrets.token_hex(QR_REQUEST_ID_BYTES)
            requests[request_id] = {
                "device_label": device_label,
                "created_at": utcnow_iso(),
                "expires_at": float(record.get("expires_at", 0)),
                "status": "pending",
                "token": None,
            }
            self._write_json("pairing-requests.json", {"requests": requests})
            return True, "pending", 0.0, {
                "request_id": request_id,
                "expires_at": float(record.get("expires_at", 0)),
            }

    def get_pair_request(self, request_id: str) -> dict | None:
        """Fetch one unexpired request (sweeps first); None when unknown."""
        with self.lock:
            return self._pair_requests_locked(time.time()).get(request_id)

    def list_pair_requests(self) -> dict:
        """All unexpired requests for the operator (sweeps first)."""
        with self.lock:
            return dict(self._pair_requests_locked(time.time()))

    def approve_pair_request(self, request_id: str) -> dict | None:
        """Approve a pending request, issuing its token. Idempotent.

        Returns the stored entry (with token) or None when the request is
        unknown or expired. Approving twice issues one token: the stored
        one is returned, nothing is re-minted.
        """
        now = time.time()
        with self.lock:
            requests = self._pair_requests_locked(now)
            entry = requests.get(request_id)
            if not isinstance(entry, dict) or entry.get("status") not in ("pending", "approved"):
                return None
            if entry.get("status") == "approved" and entry.get("token"):
                return dict(entry)
            token = self._issue_token_locked(f"qr {entry.get('device_label', '?')} {utcnow_iso()}")
            entry["status"] = "approved"
            entry["approved_at"] = utcnow_iso()
            entry["token"] = token
            self._write_json("pairing-requests.json", {"requests": requests})
            return dict(entry)

    def reject_pair_request(self, request_id: str) -> bool:
        """Delete a request so its poll answers like an unknown id."""
        with self.lock:
            requests = self._pair_requests_locked(time.time())
            if request_id not in requests:
                return False
            del requests[request_id]
            self._write_json("pairing-requests.json", {"requests": requests})
            return True

    def resolve_request_prefix(self, prefix: str) -> tuple[str | None, int]:
        """Resolve an id prefix to one request: (id or None, match count)."""
        with self.lock:
            matches = sorted(
                rid for rid in self._pair_requests_locked(time.time()) if rid.startswith(prefix)
            )
        if len(matches) == 1:
            return matches[0], 1
        return None, len(matches)

    # -- bearer tokens ----------------------------------------------------------

    def _issue_token_locked(self, label: str) -> str:
        """Mint + store a token; caller must hold the lock."""
        token = secrets.token_hex(_TOKEN_BYTES)
        tokens = self._read_json("tokens.json") or {}
        tokens[sha256_hex(token.encode("utf-8"))] = {
            "created_at": utcnow_iso(),
            "label": label,
        }
        self._write_json("tokens.json", tokens)
        return token

    def issue_token(self, label: str) -> str:
        with self.lock:
            return self._issue_token_locked(label)

    def check_token(self, presented: str | None) -> bool:
        if not presented:
            return False
        candidate = sha256_hex(presented.encode("utf-8"))
        with self.lock:
            tokens = self._read_json("tokens.json") or {}
        for stored in tokens:
            if hmac.compare_digest(candidate, stored):
                return True
        return False

    def revoke_tokens(self) -> int:
        with self.lock:
            tokens = self._read_json("tokens.json") or {}
            count = len(tokens)
            self._write_json("tokens.json", {})
        return count

    # -- staged index -------------------------------------------------------------

    def read_index(self) -> dict:
        with self.lock:
            index = self._read_json("index.json") or {}
        packages = index.get("packages")
        return packages if isinstance(packages, dict) else {}

    def record_package(self, package_id: str, summary: dict) -> None:
        with self.lock:
            index = self._read_json("index.json") or {}
            packages = index.get("packages")
            if not isinstance(packages, dict):
                packages = {}
            packages[package_id] = summary
            index["packages"] = packages
            self._write_json("index.json", index)

    def note_duplicate(self, package_id: str) -> dict | None:
        """Count a repeat delivery; returns the stored summary or None."""
        with self.lock:
            index = self._read_json("index.json") or {}
            packages = index.get("packages")
            if not isinstance(packages, dict) or package_id not in packages:
                return None
            summary = packages[package_id]
            summary["duplicate_deliveries"] = int(summary.get("duplicate_deliveries", 0)) + 1
            summary["last_seen_at"] = utcnow_iso()
            self._write_json("index.json", index)
            return summary


def manifest_session_ids(package_bytes: bytes) -> list[str]:
    """Session ids referenced by a validated package's manifest.

    Defensive by design: the manifest passed the validator a moment ago,
    so anything unexpected yields an empty list rather than failing the
    staging. Only well-formed UUID session ids are reported.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(package_bytes)) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except Exception:
        return []
    if not isinstance(manifest, dict):
        return []
    observations = manifest.get("observations")
    if not isinstance(observations, list):
        return []
    ids = set()
    for obs in observations:
        if isinstance(obs, dict) and validator.is_id(obs.get("session_id")):
            ids.add(obs.get("session_id"))
    return sorted(ids)


# ---------------------------------------------------------------------------
# HTTP service
# ---------------------------------------------------------------------------


def bearer_token(headers) -> str | None:
    value = headers.get("Authorization")
    if not value:
        return None
    scheme, _, credentials = value.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return None
    return credentials.strip()


class ReceiverHandler(BaseHTTPRequestHandler):
    """Route handler. Only /v1/info, /v1/pair and /v1/pair/qr* skip the token."""

    server_version = "AnastylosCaptureReceiver/1"

    @property
    def state(self) -> ReceiverState:
        server = self.server
        assert isinstance(server, ReceiverServer)
        return server.state

    # -- helpers ------------------------------------------------------------

    def _send_json(self, status: int, payload: dict, extra: dict[str, str] | None = None) -> None:
        body = (json.dumps(payload) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str, extra: dict[str, str] | None = None) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}}, extra)

    def _require_auth(self) -> bool:
        if self.state.check_token(bearer_token(self.headers)):
            return True
        self._error(
            HTTPStatus.UNAUTHORIZED,
            "unauthorized",
            "Valid bearer token required.",
            {"WWW-Authenticate": "Bearer"},
        )
        return False

    def log_message(self, fmt: str, *args) -> None:  # noqa: D102 (stdlib override)
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # -- routing ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: D102 (stdlib override)
        parsed = urlparse(self.path)
        if parsed.path == "/v1/info":
            self._send_json(
                HTTPStatus.OK,
                {"service": SERVICE_NAME, "version": API_VERSION},
            )
            return
        if parsed.path == "/v1/status":
            if not self._require_auth():
                return
            index = self.state.read_index()
            self._send_json(
                HTTPStatus.OK,
                {
                    "workstation_id": self.state.workstation_id(),
                    "staged_packages": len(index),
                    "server_time": utcnow_iso(),
                },
            )
            return
        if parsed.path == "/v1/packages":
            if not self._require_auth():
                return
            session_filter: str | None = None
            query = parse_qs(parsed.query, keep_blank_values=True)
            if "session_id" in query:
                values = query["session_id"]
                if len(values) != 1 or not validator.is_id(values[0]):
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "bad_metadata",
                        "session_id must be a single UUID.",
                    )
                    return
                session_filter = values[0]
            index = self.state.read_index()
            summaries = [
                {
                    "package_id": package_id,
                    "staged_at": summary.get("staged_at"),
                    "zip_sha256": summary.get("zip_sha256"),
                    "status": summary.get("status"),
                    "session_ids": summary.get("session_ids", []),
                    "observation_ids": summary.get("observation_ids", []),
                    "accepted_for_review": summary.get("accepted_for_review", []),
                    "duplicate_deliveries": summary.get("duplicate_deliveries", 0),
                }
                for package_id, summary in sorted(index.items())
                if session_filter is None or session_filter in summary.get("session_ids", [])
            ]
            self._send_json(HTTPStatus.OK, {"packages": summaries})
            return
        if parsed.path.startswith("/v1/pair/qr/"):
            self._handle_pair_poll(parsed.path[len("/v1/pair/qr/"):])
            return
        if parsed.path.startswith("/v1/packages/"):
            if not self._require_auth():
                return
            package_id = parsed.path[len("/v1/packages/") :]
            report_path = self.state.packages_dir / package_id / "report.json"
            try:
                resolved = report_path.resolve()
                base = self.state.packages_dir.resolve()
            except OSError:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown package.")
                return
            if base not in resolved.parents or not resolved.is_file():
                self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown package.")
                return
            try:
                report = json.loads(resolved.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "unreadable", "Stored report is unreadable.")
                return
            self._send_json(HTTPStatus.OK, {"duplicate": True, "report": report})
            return
        if video.is_video_path(parsed.path):
            if video.VideoRoutes(self).dispatch("GET", parsed):
                return
        if parsed.path.startswith("/v1/guided/"):
            if guided.GuidedRoutes(self).dispatch_get(parsed):
                return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown endpoint.")

    def do_PUT(self) -> None:  # noqa: D102 (stdlib override)
        parsed = urlparse(self.path)
        if video.is_video_path(parsed.path):
            if video.VideoRoutes(self).dispatch("PUT", parsed):
                return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown endpoint.")

    def do_POST(self) -> None:  # noqa: D102 (stdlib override)
        parsed = urlparse(self.path)
        if parsed.path == "/v1/pair":
            self._handle_pair()
            return
        if parsed.path == "/v1/pair/qr":
            self._handle_pair_qr()
            return
        if parsed.path == "/v1/packages":
            if guided.is_guided_upload(self.headers):
                guided.GuidedRoutes(self).handle_guided_upload()
                return
            self._handle_upload()
            return
        if voice.is_voice_path(parsed.path):
            if voice.VoiceRoutes(self).dispatch_post(parsed):
                return
        if video.is_video_path(parsed.path):
            if video.VideoRoutes(self).dispatch("POST", parsed):
                return
        if parsed.path.startswith("/v1/guided/"):
            if guided.GuidedRoutes(self).dispatch_post(parsed):
                return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown endpoint.")

    # -- pairing ------------------------------------------------------------------

    def _handle_pair(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", "Content-Length must be a number.")
            return
        if length <= 0 or length > 64 * 1024:
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", "Pairing body must be small JSON.")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", "Pairing body must be JSON.")
            return
        code = payload.get("pairing_code") if isinstance(payload, dict) else None
        if not isinstance(code, str) or not code.strip():
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", "pairing_code is required.")
            return
        ok, reason, retry_after = self.state.redeem_code(code)
        if not ok:
            if reason == "too many attempts":
                self._error(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    "rate_limited",
                    "Too many pairing attempts; the code is locked for a while.",
                    {"Retry-After": str(int(retry_after) + 1)},
                )
            else:
                # One generic message: no oracle for code vs expiry vs reuse.
                self._error(HTTPStatus.FORBIDDEN, "pairing_failed", "Pairing failed.")
            return
        token = self.state.issue_token(f"paired {utcnow_iso()}")
        self._send_json(
            HTTPStatus.OK,
            {"workstation_id": self.state.workstation_id(), "token": token},
        )

    def _handle_pair_qr(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", "Content-Length must be a number.")
            return
        if length <= 0 or length > 64 * 1024:
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", "Pairing body must be small JSON.")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", "Pairing body must be JSON.")
            return
        code = payload.get("pairing_code") if isinstance(payload, dict) else None
        if not isinstance(code, str) or not code.strip():
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", "pairing_code is required.")
            return
        label = clean_device_label(payload.get("device_label") if isinstance(payload, dict) else None)
        if label is None:
            self._error(
                HTTPStatus.BAD_REQUEST,
                "bad_request",
                "device_label must be 1..64 printable characters.",
            )
            return
        ok, reason, retry_after, summary = self.state.redeem_code_request(code, label)
        if not ok:
            if reason == "too many attempts":
                self._error(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    "rate_limited",
                    "Too many pairing attempts; the code is locked for a while.",
                    {"Retry-After": str(int(retry_after) + 1)},
                )
            else:
                # One generic message: no oracle for code vs expiry vs reuse.
                self._error(HTTPStatus.FORBIDDEN, "pairing_failed", "Pairing failed.")
            return
        assert summary is not None
        self._send_json(
            HTTPStatus.ACCEPTED,
            {
                "status": "pending",
                "request_id": summary["request_id"],
                "expires_at": summary["expires_at"],
            },
        )

    def _handle_pair_poll(self, request_id: str) -> None:
        if _REQUEST_ID_RE.match(request_id) is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown or expired pairing request.")
            return
        entry = self.state.get_pair_request(request_id)
        if entry is None:
            # Unknown, rejected, and expired all answer identically: the
            # 128-bit id is unguessable, and no state leaks through here.
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown or expired pairing request.")
            return
        if entry.get("status") == "approved" and entry.get("token"):
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "approved",
                    "workstation_id": self.state.workstation_id(),
                    "token": entry["token"],
                },
            )
            return
        self._send_json(HTTPStatus.OK, {"status": "pending"})

    # -- uploads ---------------------------------------------------------------------

    def _handle_upload(self) -> None:
        if not self._require_auth():
            return
        package_id = (self.headers.get("X-Package-Id") or "").strip()
        if not validator.is_id(package_id):
            self._error(HTTPStatus.BAD_REQUEST, "bad_metadata", "X-Package-Id must be a UUID.")
            return
        expected_sha = (self.headers.get("X-Package-Sha256") or "").strip().lower()
        if not validator.HEX64_RE.match(expected_sha):
            self._error(HTTPStatus.BAD_REQUEST, "bad_metadata", "X-Package-Sha256 must be lowercase hex.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", "Content-Length must be a number.")
            return
        if length <= 0:
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", "Upload body is empty.")
            return
        if length > MAX_UPLOAD_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large", "Upload exceeds the package size cap.")
            return
        try:
            received = self.rfile.read(length)
        except OSError:
            self._error(HTTPStatus.BAD_REQUEST, "interrupted", "Upload was interrupted; retry the whole package.")
            return
        if len(received) != length:
            self._error(HTTPStatus.BAD_REQUEST, "interrupted", "Upload was interrupted; retry the whole package.")
            return
        digest = sha256_hex(received)
        if not hmac.compare_digest(digest, expected_sha):
            self._error(
                HTTPStatus.BAD_REQUEST,
                "checksum_mismatch",
                "Upload bytes do not match X-Package-Sha256; retry the whole package.",
            )
            return

        # Idempotency first: a known package_id never stages twice.
        known = self.state.note_duplicate(package_id)
        staged_report = self.state.packages_dir / package_id / "report.json"
        if known is not None or staged_report.is_file():
            report = self._stored_report(package_id)
            if report is None:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "unreadable", "Stored report is unreadable.")
                return
            self._send_json(HTTPStatus.OK, {"duplicate": True, "report": report})
            return

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=str(self.state.packages_dir), prefix=".upload-", suffix=".zip", delete=False
            ) as tmp:
                tmp.write(received)
                tmp_path = Path(tmp.name)
            try:
                report = validator.validate_package(tmp_path)
            except Exception as exc:  # noqa: BLE001 (validator must never kill the connection)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "io_error", f"Cannot validate the upload: {exc}.")
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return
        except OSError as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "io_error", f"Cannot buffer the upload: {exc}.")
            return
        assert tmp_path is not None
        try:
            manifest_id = report.get("package_id")
            if manifest_id is not None and manifest_id != package_id:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "bad_metadata",
                    "Manifest package_id disagrees with X-Package-Id.",
                )
                return
            if report.get("status") != "valid":
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"duplicate": False, "report": report})
                return
            target = self.state.packages_dir / package_id
            try:
                staged = validator.stage_package(tmp_path, target, report)
            except ValueError as exc:
                # Lost a race with a parallel duplicate delivery: report it so.
                if target.is_dir():
                    stored = self._stored_report(package_id)
                    if stored is not None:
                        self.state.note_duplicate(package_id)
                        self._send_json(HTTPStatus.OK, {"duplicate": True, "report": stored})
                        return
                self._error(HTTPStatus.CONFLICT, "staging_failed", f"Cannot stage the package: {exc}.")
                return
            (target / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            (target / "package.zip").write_bytes(received)
            summary = {
                "staged_at": utcnow_iso(),
                "zip_sha256": digest,
                "zip_bytes": len(received),
                "status": report.get("status"),
                "accepted_for_review": report.get("accepted_for_review", []),
                "observation_ids": [o.get("observation_id") for o in report.get("observations", [])],
                "session_ids": manifest_session_ids(received),
                "staged_files": staged,
                "duplicate_deliveries": 0,
                "last_seen_at": utcnow_iso(),
            }
            self.state.record_package(package_id, summary)
            self._send_json(HTTPStatus.CREATED, {"duplicate": False, "report": report})
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _stored_report(self, package_id: str) -> dict | None:
        try:
            text = (self.state.packages_dir / package_id / "report.json").read_text(encoding="utf-8")
            report = json.loads(text)
        except (OSError, json.JSONDecodeError):
            return None
        return report if isinstance(report, dict) else None


class ReceiverServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: ReceiverState) -> None:
        self.state = state
        super().__init__(address, ReceiverHandler)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ANASTYLOS CAPTURE workstation receiver.")
    parser.add_argument("--data-dir", type=Path, required=True, help="Operator-owned state directory.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create state, workstation id and a first pairing code.")
    init.add_argument("--ttl-minutes", type=int, default=5, help="Pairing-code lifetime (default 5).")

    code = sub.add_parser("pair-code", help="Mint a fresh single-use pairing code.")
    code.add_argument("--ttl-minutes", type=int, default=5, help="Pairing-code lifetime (default 5).")
    code.add_argument("--qr", action="store_true", help="Also print a QR pairing code (needs --host/--cert).")
    code.add_argument("--host", default=None, help="LAN host the phone reaches (for --qr).")
    code.add_argument("--port", type=int, default=8443, help="Receiver port (for --qr; default 8443).")
    code.add_argument("--cert", type=Path, default=None, help="PEM certificate (for --qr SPKI pin).")
    code.add_argument("--qr-out", type=Path, default=None, help="PNG path (default <data-dir>/pair-qr.png).")

    pstatus = sub.add_parser("pair-status", help="List pending QR pairing requests.")
    pstatus.add_argument("--json", action="store_true", help="Print requests as JSON (tokens redacted).")

    approve = sub.add_parser("pair-approve", help="Approve one QR pairing request.")
    approve.add_argument("request", help="Request id or unique id prefix.")

    reject = sub.add_parser("pair-reject", help="Reject (delete) one QR pairing request.")
    reject.add_argument("request", help="Request id or unique id prefix.")

    serve = sub.add_parser("serve", help="Run the HTTPS receiver on the LAN.")
    serve.add_argument("--host", default="127.0.0.1", help="Interface to bind (default 127.0.0.1).")
    serve.add_argument("--port", type=int, default=8443, help="Port to listen on (default 8443).")
    serve.add_argument("--cert", type=Path, default=None, help="PEM certificate chain for TLS.")
    serve.add_argument("--key", type=Path, default=None, help="PEM private key for TLS.")
    serve.add_argument(
        "--plaintext-localhost",
        action="store_true",
        help="Serve plaintext HTTP on loopback only (development/tests; never a LAN mode).",
    )
    serve.add_argument("--voice-transcriber", type=Path, default=None,
                       help="tools/live/providers/transcribe.py; enables voice transcription (VOICE-01 R9).")
    serve.add_argument("--session-ui-port", type=int, default=None,
                       help="Port of the session UI on 127.0.0.1; enables voice input (VOICE-01 R10).")

    status = sub.add_parser("status", help="List staged packages for operator review.")
    status.add_argument("--json", action="store_true", help="Print the staged index as JSON.")

    revoke = sub.add_parser("revoke-tokens", help="Revoke every issued device token.")
    revoke.add_argument("--yes", action="store_true", help="Confirm revocation.")

    enroll = sub.add_parser("guided-enroll", help="Enroll an approved phone for guided capture.")
    enroll.add_argument("request", help="Approved QR pairing request id or unique prefix.")
    enroll.add_argument("--label", default=None, help="Source label (default: device label).")

    bootstrap = sub.add_parser("guided-bootstrap", help="Mint an adapter bootstrap token (show once).")
    bootstrap.add_argument("--principal", required=True, help="Adapter principal name.")
    bootstrap.add_argument("--ttl-hours", type=int, default=24, help="Token lifetime in hours (default 24).")

    reader = sub.add_parser("guided-reader", help="Mint a session reader token (show once).")
    reader.add_argument("session", help="Session id.")
    reader.add_argument("role", help="spatial or reader.")

    revokeg = sub.add_parser("guided-revoke-grant", help="Revoke one operator grant immediately.")
    revokeg.add_argument("grant", help="Grant id.")

    revoket = sub.add_parser("guided-revoke-tokens", help="Revoke guided tokens for one session.")
    revoket.add_argument("--session", required=True, help="Session id.")

    gstatus = sub.add_parser("guided-status", help="List guided sources and tokens (redacted).")
    gstatus.add_argument("--json", action="store_true", help="Print as JSON.")

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    state = ReceiverState(args.data_dir)
    workstation = state.workstation_id()
    code, _record = state.mint_code(ttl_seconds=max(1, args.ttl_minutes) * 60)
    print(f"workstation_id: {workstation}")
    print(f"data dir: {args.data_dir}")
    print(f"pairing code (single-use, {args.ttl_minutes} min): {code}")
    print("Enter the code in the Capture PWA Sync screen to pair this workstation.")
    return 0


def _qr_base_url(host: str, port: int) -> str:
    """Validate CLI host/port into the https base URL the phone dials."""
    cleaned = (host or "").strip()
    if not cleaned or any(ch.isspace() or ord(ch) < 0x20 for ch in cleaned):
        raise ValueError("--host must be a LAN hostname or IP address")
    if any(ch in cleaned for ch in "/?#@"):
        raise ValueError("--host must be a bare hostname or IP address")
    if ":" in cleaned and not cleaned.startswith("["):
        cleaned = f"[{cleaned}]"
    try:
        port_number = int(port)
    except (TypeError, ValueError):
        raise ValueError("--port must be a number") from None
    if not 1 <= port_number <= 65535:
        raise ValueError("--port must be within 1..65535")
    return f"https://{cleaned}:{port_number}"


def cmd_pair_code(args: argparse.Namespace) -> int:
    state = ReceiverState(args.data_dir)
    code, record = state.mint_code(ttl_seconds=max(1, args.ttl_minutes) * 60)
    print(f"workstation_id: {state.workstation_id()}")
    print(f"pairing code (single-use, {args.ttl_minutes} min): {code}")
    if not args.qr:
        return 0
    if not args.host or not args.cert:
        print("error: --qr needs --host and --cert", file=sys.stderr)
        return 2
    try:
        base_url = _qr_base_url(args.host, args.port)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if is_loopback_host(args.host):
        print("warning: --host is loopback; phones reach it only via adb reverse.",
              file=sys.stderr)
    try:
        spki = spki_sha256_hex(args.cert)
        fingerprint = cert_fingerprint_sha256(args.cert)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read certificate: {exc}", file=sys.stderr)
        return 2
    try:
        payload = build_qr_payload(
            workstation_id=state.workstation_id(),
            base_url=base_url,
            spki_hex=spki,
            code=code,
            expires_at=float(record["expires_at"]),
        )
        symbol = qr_encode.encode_qr(payload.encode("ascii"))
    except ValueError as exc:
        print(f"error: cannot build QR payload: {exc}", file=sys.stderr)
        return 1
    out_path = args.qr_out or (state.data_dir / "pair-qr.png")
    try:
        out_path.write_bytes(qr_encode.qr_png_bytes(symbol))
    except OSError as exc:
        print(f"error: cannot write {out_path}: {exc}", file=sys.stderr)
        return 1
    expires = datetime.fromtimestamp(float(record["expires_at"]), timezone.utc)
    print(f"QR pairing (expires {expires.strftime('%Y-%m-%dT%H:%M:%SZ')}):")
    print(qr_encode.qr_ascii(symbol))
    print(f"endpoint: {base_url}")
    print(f"spki pin: {spki}")
    print(f"cert fingerprint: {fingerprint}")
    print(f"qr png: {out_path}")
    print(f"payload: {payload}")
    print("fallback: type the endpoint, pairing code and fingerprints by hand.")
    return 0


def cmd_pair_status(args: argparse.Namespace) -> int:
    state = ReceiverState(args.data_dir)
    requests = state.list_pair_requests()
    if args.json:
        redacted = {
            request_id: {**entry, "token": bool(entry.get("token"))}
            for request_id, entry in requests.items()
        }
        print(json.dumps(
            {"workstation_id": state.workstation_id(), "requests": redacted}, indent=2
        ))
        return 0
    now = time.time()
    pending = sum(1 for entry in requests.values() if entry.get("status") == "pending")
    print(f"pairing requests: {len(requests)} ({pending} pending)")
    for request_id in sorted(requests):
        entry = requests[request_id]
        left = max(0, int(float(entry.get("expires_at", 0)) - now))
        print(
            f"  - {request_id[:12]}… {entry.get('status')}"
            f" label={entry.get('device_label')} expires_in={left}s"
        )
    return 0


def cmd_pair_approve(args: argparse.Namespace) -> int:
    state = ReceiverState(args.data_dir)
    request_id, count = state.resolve_request_prefix(args.request)
    if count == 0:
        print(f"no unexpired pairing request matches {args.request!r}", file=sys.stderr)
        return 1
    if request_id is None:
        print(f"ambiguous request prefix ({count} match); be more specific", file=sys.stderr)
        return 1
    entry = state.approve_pair_request(request_id)
    if entry is None:
        print("request expired before approval", file=sys.stderr)
        return 1
    print(f"approved {entry.get('device_label')} ({request_id[:12]}…); token issued to the phone.")
    return 0


def cmd_pair_reject(args: argparse.Namespace) -> int:
    state = ReceiverState(args.data_dir)
    request_id, count = state.resolve_request_prefix(args.request)
    if count == 0:
        print(f"no unexpired pairing request matches {args.request!r}", file=sys.stderr)
        return 1
    if request_id is None:
        print(f"ambiguous request prefix ({count} match); be more specific", file=sys.stderr)
        return 1
    state.reject_pair_request(request_id)
    print(f"rejected {request_id[:12]}…")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = ReceiverState(args.data_dir)
    index = state.read_index()
    if args.json:
        print(json.dumps({"workstation_id": state.workstation_id(), "packages": index}, indent=2))
        return 0
    print(f"workstation_id: {state.workstation_id()}")
    print(f"staged packages: {len(index)}")
    for package_id in sorted(index):
        summary = index[package_id]
        accepted = summary.get("accepted_for_review", [])
        print(f"  - {package_id} staged_at={summary.get('staged_at')} accepted={len(accepted)}")
    print("Staged evidence awaits operator review; nothing was appended to memory.")
    return 0


def cmd_revoke_tokens(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing to revoke tokens without --yes.", file=sys.stderr)
        return 2
    state = ReceiverState(args.data_dir)
    print(f"revoked {state.revoke_tokens()} token(s)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        port = int(args.port)
    except (TypeError, ValueError):
        print("error: --port must be a number", file=sys.stderr)
        return 2
    if not 1 <= port <= 65535:
        print("error: --port must be within 1..65535", file=sys.stderr)
        return 2
    state = ReceiverState(args.data_dir)
    transcriber = getattr(args, "voice_transcriber", None)
    state.voice_config = voice.VoiceConfig(
        transcriber=[sys.executable, "-B", str(transcriber), "--stdin"] if transcriber else None,
        session_ui_port=getattr(args, "session_ui_port", None),
        bindings_path=args.data_dir / voice.BINDINGS_FILE,
    )
    host = args.host
    plaintext = bool(args.plaintext_localhost)

    context: ssl.SSLContext | None = None
    fingerprint = ""
    if plaintext:
        if not is_loopback_host(host):
            print(
                "error: --plaintext-localhost refuses non-loopback binds; "
                "serve the LAN over TLS with --cert/--key.",
                file=sys.stderr,
            )
            return 2
        print("WARNING: plaintext HTTP on loopback only (development/test mode).", file=sys.stderr)
    else:
        if args.cert is None or args.key is None:
            print("error: TLS needs --cert and --key (or --plaintext-localhost for loopback tests).", file=sys.stderr)
            return 2
        if not args.cert.is_file() or not args.key.is_file():
            print("error: --cert/--key must be readable files.", file=sys.stderr)
            return 2
        try:
            fingerprint = cert_fingerprint_sha256(args.cert)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read certificate: {exc}", file=sys.stderr)
            return 2
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            context.load_cert_chain(str(args.cert), str(args.key))
        except (OSError, ssl.SSLError) as exc:
            print(f"error: cannot load certificate/key: {exc}", file=sys.stderr)
            return 2

    try:
        server = ReceiverServer((host, port), state)
    except OSError as exc:
        print(f"error: cannot bind {host}:{port}: {exc}", file=sys.stderr)
        return 1
    if context is not None:
        server.socket = context.wrap_socket(server.socket, server_side=True)

    bound_host, bound_port = server.socket.getsockname()[:2]
    print(f"{SERVICE_NAME} v{API_VERSION} on {bound_host}:{bound_port}")
    print(f"workstation_id: {state.workstation_id()}")
    if fingerprint:
        print(f"certificate SHA-256: {fingerprint}")
        print("Compare this fingerprint on the phone before pairing (manual check).")
    pairing = state.pairing_status()
    if pairing["active"]:
        print("pairing code: active (run `pair-code` to mint a fresh one)")
    else:
        print("pairing code: none active (run `pair-code` before pairing a phone)")
    print("Staging into:", state.packages_dir)
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return cmd_init(args)
    if args.command == "pair-code":
        return cmd_pair_code(args)
    if args.command == "pair-status":
        return cmd_pair_status(args)
    if args.command == "pair-approve":
        return cmd_pair_approve(args)
    if args.command == "pair-reject":
        return cmd_pair_reject(args)
    if args.command == "serve":
        return cmd_serve(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "revoke-tokens":
        return cmd_revoke_tokens(args)
    if args.command == "guided-enroll":
        return guided.cmd_guided_enroll(args)
    if args.command == "guided-bootstrap":
        return guided.cmd_guided_bootstrap(args)
    if args.command == "guided-reader":
        return guided.cmd_guided_reader(args)
    if args.command == "guided-revoke-grant":
        return guided.cmd_guided_revoke_grant(args)
    if args.command == "guided-revoke-tokens":
        return guided.cmd_guided_revoke_tokens(args)
    if args.command == "guided-status":
        return guided.cmd_guided_status(args)
    parser.print_help()
    return 2


def serve_in_background(
    state: ReceiverState, host: str = "127.0.0.1", port: int = 0
) -> tuple[ReceiverServer, threading.Thread]:
    """Start a loopback plaintext server for tests. Never a LAN mode."""
    if not is_loopback_host(host):
        raise ValueError("background test servers bind loopback only")
    server = ReceiverServer((host, port), state)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return server, thread


if __name__ == "__main__":
    raise SystemExit(main())
