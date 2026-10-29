#!/usr/bin/env python3
"""Local stdio MCP adapter for guided capture (official `mcp` SDK).

Exposes exactly the nine ``capture.*`` tools — no filesystem, shell,
or provider tool. Configuration arrives via protected process
environment (never CLI args or repository files):

- ``ANASTYLOS_RECEIVER_URL`` (e.g. ``https://192.168.1.20:8443``)
- ``ANASTYLOS_ADAPTER_TOKEN`` (bootstrap token, then the claimed
  session token once ``capture.claim`` … note: claiming uses the
  bootstrap token via :mod:`handlers` + :mod:`control_client` and the
  operator rotates the variable to the session token)
- ``ANASTYLOS_RECEIVER_CA`` (PEM CA bundle for TLS verification)
- ``ANASTYLOS_RECEIVER_SPKI_PIN`` (optional SPKI hex pin)
- ``ANASTYLOS_PROVIDER_IMAGES`` (``authorized`` only under a separate
  provider data-use approval; anything else keeps bytes out)

Requires the pinned SDK (``mcp==2.3.0``, MIT); install and SPDX
inventory need CONTRACT-OWNER approval (see README.md). Run under a
local MCP host over stdio; never expose the receiver publicly.
"""

from __future__ import annotations

import os

from mcp.server import MCPServer

from control_client import ControlClient
from handlers import (
    capture_cancel_view,
    capture_capabilities,
    capture_evidence,
    capture_finish_set,
    capture_open_session,
    capture_request_view,
    capture_resume_session,
    capture_revise_view,
    capture_status,
)

mcp = MCPServer("anastylos-capture")


def _client():
    base_url = os.environ.get("ANASTYLOS_RECEIVER_URL", "")
    if not base_url:
        raise RuntimeError("ANASTYLOS_RECEIVER_URL is not set")
    return ControlClient(
        base_url,
        ca_file=os.environ.get("ANASTYLOS_RECEIVER_CA"),
        spki_pin_sha256=os.environ.get("ANASTYLOS_RECEIVER_SPKI_PIN"))


def _token():
    token = os.environ.get("ANASTYLOS_ADAPTER_TOKEN", "")
    if not token:
        raise RuntimeError("ANASTYLOS_ADAPTER_TOKEN is not set")
    return token


@mcp.tool()
def capabilities(source_id: str) -> dict:
    """Scoped source capabilities (read-only)."""
    return capture_capabilities(_client(), _token(), source_id)


@mcp.tool()
def open_session(source_id: str, max_images: int, expires_at: str,
                 operation_id: str | None = None) -> dict:
    """Create a DISARMED session pending phone consent."""
    return capture_open_session(_client(), _token(), source_id, max_images,
                                expires_at, operation_id)


@mcp.tool()
def resume_session(session_id: str,
                   expected_session_revision: int,
                   operation_id: str | None = None) -> dict:
    """Recover a session snapshot (never auto-rearms)."""
    return capture_resume_session(_client(), _token(), session_id,
                                  expected_session_revision, operation_id)


@mcp.tool()
def request_view(body: dict) -> dict:
    """Queue one authoritative view request."""
    return capture_request_view(_client(), _token(), body)


@mcp.tool()
def status(session_id: str, after: str = "0") -> dict:
    """Session snapshot plus the next change page."""
    return capture_status(_client(), _token(), session_id, after)


@mcp.tool()
def revise_view(body: dict) -> dict:
    """Replace a request target as a new immutable revision."""
    return capture_revise_view(_client(), _token(), body)


@mcp.tool()
def cancel_view(body: dict) -> dict:
    """Cancel one request."""
    return capture_cancel_view(_client(), _token(), body)


@mcp.tool()
def evidence(session_id: str, request_id: str | None = None,
             include_images: bool = False, limit: int = 20) -> dict:
    """Scoped receipt/asset listing (bytes need explicit opt-in)."""
    return capture_evidence(_client(), _token(), session_id, request_id,
                            include_images, limit)


@mcp.tool()
def finish_set(session_id: str, expected_session_revision: int,
               operation_id: str | None = None) -> dict:
    """Seal the capture set."""
    return capture_finish_set(_client(), _token(), session_id,
                              expected_session_revision, operation_id)


if __name__ == "__main__":
    mcp.run()
