#!/usr/bin/env python3
"""Stdlib HTTPS client for guided control routes. No third-party imports.

Used by the MCP adapter (``adapter.py``) and operator scripts. The
caller supplies tokens minted out-of-band: a bootstrap token for
``open_session``/``claim`` and the claimed session token afterwards.
TLS trust is explicit: either a CA bundle (``ca_file``) or a SPKI pin
(``spki_pin_sha256``); loopback ``http://`` is allowed for tests only.
"""

from __future__ import annotations

import functools
import hashlib
import http.client
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
        if self.spki_pin and not self.ca_file:
            # The pin replaces CA trust for the receiver's self-signed certificate; it is checked right after the
            # handshake, before any request (and its bearer token) is sent.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        else:
            context = ssl.create_default_context(cafile=self.ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def _open(self, request):
        if self.base_url.startswith("https://") and self.spki_pin:
            opener = urllib.request.build_opener(_PinnedHTTPSHandler(self._context(), self.spki_pin.lower()))
            return opener.open(request, timeout=self.timeout)
        context = self._context() if self.base_url.startswith("https://") else None
        return urllib.request.urlopen(request, timeout=self.timeout, context=context)

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
        try:
            with self._open(request) as response:
                payload = response.read()
                return response.status, payload, dict(response.headers)
        except urllib.error.HTTPError as exc:
            self._raise_http(exc)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, PinMismatch):
                raise ControlClientError(0, "pin_mismatch", "SPKI pin mismatch") from exc
            raise

    @staticmethod
    def _raise_http(exc):
        try:
            error = json.loads(exc.read().decode("utf-8")).get("error", {})
        except ValueError:
            error = {}
        raise ControlClientError(exc.code, error.get("code", "http_error"),
                                 error.get("message", "request failed")) from exc

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


def _tlv(data, pos):
    """One DER element at ``pos``: (tag, content start, end)."""
    tag, length, head = data[pos], data[pos + 1], 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[pos + 2:pos + 2 + count], "big")
        head += count
    return tag, pos + head, pos + head + length


def _spki_of(der_certificate):
    """SubjectPublicKeyInfo element (header included) of a DER X.509 certificate."""
    data = der_certificate
    tag, cert_start, _ = _tlv(data, 0)
    if tag != 0x30:
        raise ValueError("not a DER certificate")
    tag, pos, _ = _tlv(data, cert_start)  # tbsCertificate
    if tag != 0x30:
        raise ValueError("not a DER certificate")
    if data[pos] == 0xA0:  # explicit version
        pos = _tlv(data, pos)[2]
    for _field in range(5):  # serialNumber, signature, issuer, validity, subject
        pos = _tlv(data, pos)[2]
    tag, _, end = _tlv(data, pos)
    if tag != 0x30:
        raise ValueError("SPKI not found")
    return data[pos:end]


class PinMismatch(OSError):
    """The server's key is not the pinned one; raised before any request byte is sent."""


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, spki_pin, **kwargs):
        super().__init__(*args, **kwargs)
        self._spki_pin = spki_pin

    def connect(self):
        super().connect()
        der = self.sock.getpeercert(binary_form=True)
        if der is None or hashlib.sha256(_spki_of(der)).hexdigest() != self._spki_pin:
            self.close()
            raise PinMismatch("SPKI pin mismatch")


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, context, spki_pin):
        super().__init__(context=context)
        self._pinned_context = context
        self._spki_pin = spki_pin

    def https_open(self, req):
        return self.do_open(functools.partial(_PinnedHTTPSConnection, spki_pin=self._spki_pin), req,
                            context=self._pinned_context)
