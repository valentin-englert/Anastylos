#!/usr/bin/env python3
"""Stdlib HTTPS client for guided control routes. No third-party imports.

Used by the MCP adapter (``adapter.py``) and operator scripts. The
caller supplies tokens minted out-of-band: a bootstrap token for
``open_session``/``claim`` and the claimed session token afterwards.
TLS trust is explicit: either a CA bundle (``ca_file``) or a SPKI pin
(``spki_pin_sha256``); loopback ``http://`` is allowed for tests only.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse


class ControlClientError(Exception):
    def __init__(self, status, code, message):
        super().__init__("%s %s: %s" % (status, code, message))
        self.status = status
        self.code = code
        self.message = message


class ControlClient:
    def __init__(self, base_url, timeout=10, ca_file=None,
                 spki_pin_sha256=None):
        parsed = urlparse(base_url)
        if parsed.scheme == "http" and parsed.hostname not in (
                "127.0.0.1", "localhost", "::1"):
            raise ValueError("plaintext control is loopback-only")
        if parsed.scheme not in ("http", "https"):
            raise ValueError("base_url must be http(s)")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.ca_file = ca_file
        self.spki_pin = spki_pin_sha256

    def _context(self):
        context = ssl.create_default_context(cafile=self.ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def _call(self, method, path, token, body=None, raw=None,
              content_type="application/json", extra_headers=None):
        data = None
        headers = dict(extra_headers or {})
        if token is not None:
            headers["Authorization"] = "Bearer %s" % token
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = content_type
        elif raw is not None:
            data = raw
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method)
        context = (self._context()
                   if self.base_url.startswith("https://") else None)
        try:
            with urllib.request.urlopen(
                    request, timeout=self.timeout,
                    context=context) as response:
                payload = response.read()
                if self.spki_pin and self.base_url.startswith("https://"):
                    der = response.fp.raw._sock.getpeercert(binary_form=True)
                    import hashlib as _h

                    seen = _h.sha256(_spki_of(der)).hexdigest()
                    if seen != self.spki_pin.lower():
                        raise ControlClientError(
                            0, "pin_mismatch", "SPKI pin mismatch")
                return response.status, payload, dict(response.headers)
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read().decode("utf-8")).get(
                    "error", {})
            except ValueError:
                error = {}
            raise ControlClientError(
                exc.code, error.get("code", "http_error"),
                error.get("message", "request failed"))

    def _json(self, method, path, token, body=None):
        _status, payload, _headers = self._call(method, path, token,
                                                body=body)
        return json.loads(payload.decode("utf-8"))

    # -- adapter calls ---------------------------------------------------

    def open_session(self, bootstrap_token, body):
        return self._json("POST", "/v1/guided/sessions", bootstrap_token,
                          body)

    def claim(self, bootstrap_token, grant_id, operation_id):
        return self._json("POST", "/v1/guided/grants/%s/claim" % grant_id,
                          bootstrap_token, {"operation_id": operation_id})

    def get_session(self, token, session_id):
        return self._json("GET", "/v1/guided/sessions/%s" % session_id,
                          token)

    def request_view(self, token, session_id, body):
        return self._json("POST",
                          "/v1/guided/sessions/%s/requests" % session_id,
                          token, body)

    def revise_view(self, token, request_id, body):
        return self._json("POST",
                          "/v1/guided/requests/%s/revise" % request_id,
                          token, body)

    def cancel_view(self, token, request_id, body):
        return self._json("POST",
                          "/v1/guided/requests/%s/cancel" % request_id,
                          token, body)

    def changes(self, token, session_id, after="0"):
        return self._json("GET",
                          "/v1/guided/sessions/%s/changes?after=%s"
                          % (session_id, after), token)

    def renew_lease(self, token, session_id):
        _status, payload, _headers = self._call(
            "POST", "/v1/guided/sessions/%s/lease" % session_id, token,
            raw=b"")
        return json.loads(payload.decode("utf-8"))

    def finalize(self, token, session_id, body):
        return self._json("POST",
                          "/v1/guided/sessions/%s/finalize" % session_id,
                          token, body)

    def resume(self, token, session_id, body):
        return self._json("POST",
                          "/v1/guided/sessions/%s/resume" % session_id,
                          token, body)

    def capture_set(self, token, session_id):
        return self._json("GET",
                          "/v1/guided/sessions/%s/capture-set" % session_id,
                          token)

    def capabilities(self, token, source_id):
        return self._json("GET",
                          "/v1/guided/capabilities?source_id=%s" % source_id,
                          token)

    def asset_bytes(self, token, session_id, image_id):
        _status, payload, headers = self._call(
            "GET", "/v1/guided/sessions/%s/assets/%s" % (session_id,
                                                         image_id),
            token)
        digest = hashlib.sha256(payload).hexdigest()
        claimed = (headers.get("X-Image-Sha256") or "").lower()
        if claimed and claimed != digest:
            raise ControlClientError(0, "checksum_mismatch",
                                     "asset bytes fail re-hash")
        return payload, digest


def _spki_of(der_certificate):
    """Extract the SubjectPublicKeyInfo bytes from a DER certificate."""
    data = der_certificate
    if data[0] != 0x30:
        raise ValueError("not a DER certificate")
    index = 2
    if data[index] & 0x80:
        index += 1 + (data[index] & 0x7F)
    else:
        index += 1
    if data[index] != 0x30:
        raise ValueError("not a DER certificate")
    index += 1
    length = data[index]
    index += 1
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[index:index + count], "big")
        index += count
    tbs = data[index:index + length]
    pos = 0
    if tbs[pos] == 0xA0:
        pos += 1
        size = tbs[pos]
        pos += 1
        if size & 0x80:
            count = size & 0x7F
            size = int.from_bytes(tbs[pos:pos + count], "big")
            pos += count
        pos += size
    for _ in range(3):
        pos += 1
        size = tbs[pos]
        pos += 1
        if size & 0x80:
            count = size & 0x7F
            size = int.from_bytes(tbs[pos:pos + count], "big")
            pos += count
        pos += size
    if tbs[pos] != 0x30:
        raise ValueError("SPKI not found")
    pos += 1
    size = tbs[pos]
    pos += 1
    header = 2
    if size & 0x80:
        count = size & 0x7F
        size = int.from_bytes(tbs[pos:pos + count], "big")
        header += count
        pos += count
    return tbs[pos - header:pos + size]
