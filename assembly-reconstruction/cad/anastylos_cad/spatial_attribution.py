"""Spatial -> CAD attribution mapping (Wave 1.5 integration).

Standard library only. Maps one spatial validator outcome onto the
attribution fields a CAD feature needs (``basis``, ``evidence_ids``,
``uncertainty_mm``, ``dataset_sha256``) without inventing geometry:
parameters, frames, and the mm-uncertainty scalar need a dimension
estimator and an owner-approved reduction rule, both Wave-2 work.

Rules (fail closed):

- ``basis`` is ``geometric_measurement`` only when the validator says
  ``accept`` with ``metric_ready`` *and* a sidecar ``evidence_id`` is
  citable; anything else yields ``None`` and the caller must not
  claim measured geometry.
- ``evidence_ids`` cites the sidecar's ``evidence_id`` only on
  ``accept``; rejected or unreviewed sidecars contribute nothing.
- ``uncertainty_mm`` is always ``None`` (unknown): no scalar rule for
  reducing pose uncertainty into mm exists yet.
- ``dataset_sha256`` passes through only when it is plausible
  (64 hex chars), else ``None``.
"""

import re

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_METRIC_SOLVES = ("saved_image", "timestamp_interpolated")


def _sidecar_get(sidecar, *path):
    current = sidecar
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def spatial_attribution(sidecar, spatial_result, dataset_sha256=None):
    """Attribution fields for CAD features from one spatial outcome.

    ``sidecar`` is the accepted spatial-evidence document (or anything
    else, which fails closed); ``spatial_result`` is the validator's
    ``{verdict, reasons, reviews, notes, metric_ready}`` (or anything
    else, which fails closed). Never raises on malformed input.
    """
    reasons = []
    verdict = None
    metric_ready = False
    if isinstance(spatial_result, dict):
        verdict = spatial_result.get("verdict")
        metric_ready = spatial_result.get("metric_ready") is True
    doc = sidecar if isinstance(sidecar, dict) else {}
    accepted = verdict == "accept"
    if not accepted:
        reasons.append(f"spatial verdict is {verdict!r}, not accept")
    if accepted and not metric_ready:
        reasons.append("spatial result is not metric_ready")
        for label, ok in (
                ("metric gates", _metric_gates_hint(doc)),
                ("quality accepted",
                 _sidecar_get(doc, "quality", "overall") == "accepted"),
                ("calibration validated",
                 _sidecar_get(doc, "calibration", "metric_validity")
                 == "validated"),
                ("geometric basis",
                 "geometric_measurement" in (
                     _sidecar_get(doc, "provenance", "basis") or [])),
                ("metric solve source",
                 _sidecar_get(doc, "pose", "solve_source")
                 in _METRIC_SOLVES)):
            if not ok:
                reasons.append(f"{label} not satisfied")
    evidence_ids = []
    if accepted:
        evidence_id = doc.get("evidence_id")
        if isinstance(evidence_id, str) and evidence_id:
            evidence_ids.append(evidence_id)
        else:
            reasons.append("accepted sidecar lacks evidence_id")
    basis = ("geometric_measurement"
             if accepted and metric_ready and evidence_ids else None)
    if accepted and metric_ready and not evidence_ids:
        reasons.append("no citable evidence for geometric basis")
    dataset = None
    if isinstance(dataset_sha256, str) and _HEX64.match(dataset_sha256):
        dataset = dataset_sha256
    elif dataset_sha256 is not None:
        reasons.append("dataset_sha256 implausible, dropped")
    return {"evidence_ids": evidence_ids,
            "basis": basis,
            "uncertainty_mm": None,
            "dataset_sha256": dataset,
            "metric_qualified": basis is not None,
            "reasons": reasons}


def _metric_gates_hint(doc):
    """Best-effort explanation hint only; the validator decides."""
    metrics = _sidecar_get(doc, "pose", "metrics") or {}
    return (metrics.get("position_uncertainty_mm") is not None
            and metrics.get("angle_uncertainty_deg") is not None)
