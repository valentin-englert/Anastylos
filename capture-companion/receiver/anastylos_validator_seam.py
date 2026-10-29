#!/usr/bin/env python3
"""Spatial-validator seam for receipt qualification. Stdlib only.

CORE owns the delivery receipt and the atomic ``qualified`` promotion;
SPATIAL owns the deterministic validator executable. This module is
the boundary: :func:`run_validator` invokes the approved offline
validator on staged immutable bytes in a bounded subprocess and
normalizes its report. Until the SPATIAL lane delivers that
executable, the seam reports unavailable and every receipt stays
``package_only`` — phone assertions alone never qualify.

Delivery contract for the future executable (SPATIAL lane):

- Command: ``python <tools>/spatial-validator/validate.py`` (path
  published by SPATIAL; this seam execs it, never imports it).
- Stdin: JSON ``{"package_dir": ..., "sidecar": {...},
  "profile_id": ...}`` with absolute staged paths only.
- Stdout: one JSON report ``{"valid": bool, "package_sha256": ...,
  "image_sha256": ..., "sidecar_sha256": ..., "profile_id": ...,
  "tool_id": ..., "reason_codes": [...]}``.
- Bounds: 60 s timeout, no network, read-only inputs; nonzero exit or
  timeout means ``valid: false`` with reason ``validator_failed``.

The journal calls the seam through an injected runner so unit tests
use synthetic reports without a subprocess.
"""

from __future__ import annotations

VALIDATOR_TIMEOUT_SECONDS = 60


def run_validator(request, data_dir):
    """Run the approved validator; unavailable until SPATIAL delivers."""
    return {"available": False,
            "reason": "spatial validator not delivered (SPATIAL lane)",
            "receipt_id": (request or {}).get("receipt_id")}
