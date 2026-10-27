"""Candidate comparison between two screw measurements.

Compares one named measurement (default: visible head diameter) from two
`analyze()` results. Pixels compare directly; millimetres compare only
when both sides carry valid calibration, and a frame mismatch is flagged
rather than silently mixed.
"""

from __future__ import annotations


def _entry(result: dict, name: str) -> dict | None:
    for item in result.get("measurements", []):
        if item.get("name") == name:
            return item
    return None


def _overlap(a: list | None, b: list | None) -> bool | None:
    if not a or not b or a[0] is None or b[0] is None:
        return None
    return not (a[1] < b[0] or b[1] < a[0])


def compare_measurements(a: dict, b: dict,
                         measurement: str = "visible_head_diameter") -> dict:
    """Compare one measurement across two results. JSON-serializable."""
    if a.get("mode") != b.get("mode"):
        return {"measurement": measurement, "verdict": "uncertain",
                "reasons": [f"modes differ ({a.get('mode')} vs {b.get('mode')}); "
                            "top and side views are not comparable"],
                "delta_px": None, "delta_mm": None, "mm_overlap": None}
    entry_a, entry_b = _entry(a, measurement), _entry(b, measurement)
    if entry_a is None or entry_b is None:
        return {"measurement": measurement, "verdict": "uncertain",
                "reasons": [f"{measurement!r} is missing on at least one side"],
                "delta_px": None, "delta_mm": None, "mm_overlap": None}
    reasons: list[str] = []
    for tag, entry in (("a", entry_a), ("b", entry_b)):
        if entry.get("status") in ("unknown", "unsupported"):
            reasons.append(f"side {tag}: {measurement} is {entry['status']}: "
                           + "; ".join(entry.get("reasons", [])))
    delta_px = None
    if entry_a.get("value_px") is not None and entry_b.get("value_px") is not None:
        delta_px = round(abs(entry_a["value_px"] - entry_b["value_px"]), 2)
    cal_a, cal_b = a.get("calibration") or {}, b.get("calibration") or {}
    delta_mm, overlap = None, None
    if entry_a.get("value_mm") is not None and entry_b.get("value_mm") is not None:
        delta_mm = round(abs(entry_a["value_mm"] - entry_b["value_mm"]), 3)
        overlap = _overlap(entry_a.get("mm_interval"), entry_b.get("mm_interval"))
        if cal_a.get("frame") != cal_b.get("frame"):
            reasons.append(f"frames differ ({cal_a.get('frame')} vs {cal_b.get('frame')}); "
                           "treat the millimetre delta as cross-setup")
    else:
        reasons.append("millimetre comparison needs valid calibration on both sides")
    if entry_a.get("status") in ("unknown", "unsupported") \
            or entry_b.get("status") in ("unknown", "unsupported"):
        verdict = "uncertain"
    elif overlap is True:
        verdict = "consistent"
    elif overlap is False:
        verdict = "different"
    elif delta_px is not None:
        verdict = "uncertain"
        reasons.append("pixel-only comparison; no calibrated verdict possible")
    else:
        verdict = "uncertain"
    return {"measurement": measurement, "verdict": verdict, "reasons": reasons,
            "delta_px": delta_px, "delta_mm": delta_mm, "mm_overlap": overlap,
            "a": {"value_px": entry_a.get("value_px"), "value_mm": entry_a.get("value_mm"),
                  "status": entry_a.get("status")},
            "b": {"value_px": entry_b.get("value_px"), "value_mm": entry_b.get("value_mm"),
                  "status": entry_b.get("status")}}
