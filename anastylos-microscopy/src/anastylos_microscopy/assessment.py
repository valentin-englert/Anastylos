"""Core-side evidence assessment for reader verdicts (v0.1.2, QA-03).

The readers stay unchanged. This module only asks whether the evidence a
reader *itself reported* (plus the source pixels inside the regions it
reported) is internally consistent enough to keep a clear ``ok`` verdict as a
``candidate``. When it is not, the Core holds the verdict as ``ambiguous``:
the native result, every candidate and the reason stay in the record, and
operator review remains the only way to confirm anything.

Only the resistor reader is assessed in v0.1.2. The checks are general,
physically motivated properties of colour-band evidence, fixed a priori
(not fitted to any fixture):

* ``evidence_complete`` - an ``ok`` verdict must come with one band box per
  decoded band, in-bounds boxes and per-band colour candidates. Otherwise the
  verdict cannot be traced to evidence.
* ``band_geometry`` - IEC 60062 colour bands are printed with comparable
  widths. A detected band narrower than ``MIN_BAND_WIDTH_RATIO`` of the
  median band width is more likely a segmentation artefact (edge of a
  specular highlight, shadow, lead, label) than a band; a decode that relies
  on it is not supported.
* ``band_color_determinacy`` - every decoded band colour must be the band's
  own top-ranked colour and hold a majority (> ``MIN_BAND_COLOR_SHARE``) of
  the reader's normalized colour score for that band. A decode that needs a
  non-preferred colour, or a band whose colour has no majority, is an
  interpretation the band evidence does not determine.
* ``band_color_consistency`` - a printed band has one colour along its whole
  length. The band is split into three segments along its length (the
  region the reader itself sampled); each segment's median colour is
  classified with the reader's own public ``classify`` function. If the
  decoded colour is not the top colour in at least two of the three segments,
  the band colour is not stable (partial highlight, shadow, dirt, wrong
  segmentation) and the decode is not supported.
* ``band_color_fidelity`` - band colours are classified from hue, saturation
  *and* value. Where a colour channel is clipped (>= ``CLIP_LEVEL``), that
  channel no longer distinguishes colours. If at least 10 % of one decoded
  band's pixels are clipped, that band's colour evidence is weakened and the
  whole code is held as ambiguous because one band can change the decoded
  value.

Thresholds are conservative engineering choices, not calibrated
probabilities; they have been exercised only on synthetic renders (see
tools/qa03_resistor_sweep.py and TEST_RESULTS.md). Real-image validation is
pending.
"""

from __future__ import annotations

import math

ASSESSOR = "anastylos-microscopy-resistor-evidence/2"
MIN_BAND_WIDTH_RATIO = 0.40
CLIP_LEVEL = 250
MAX_CLIPPED_FRACTION_PER_BAND = 0.10
MAX_EVALUABLE_ANGLE_DEG = 35.0
MIN_BAND_COLOR_SHARE = 0.50


def _check(name, status, detail, **values):
    return {"check": name, "status": status, "detail": detail, **values}


def _valid_box(box, width, height):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)
            and box[2] > 0 and box[3] > 0
            and box[0] < width and box[1] < height and box[0] + box[2] > 0 and box[1] + box[3] > 0)


def _axial_width(box, angle_deg):
    """Band width along the body axis from the axis-aligned source-frame box.

    The reader maps a deskewed (w x h) band rectangle back to the source frame
    as its axis-aligned bounding box: W = w|c| + h|s|, H = w|s| + h|c|.
    """
    _, _, big_w, big_h = box
    theta = math.radians(angle_deg or 0.0)
    c, s = abs(math.cos(theta)), abs(math.sin(theta))
    denom = c * c - s * s
    if abs(denom) < 1e-6:
        return None
    return max(0.0, (big_w * c - big_h * s) / denom)


def _segment_colors(image, box, angle_deg) -> list[str]:
    """Top colour (reader's classify) of three segments along one band."""
    import cv2
    import numpy as np
    from anastylos_resistor.vision import classify

    x, y, big_w, big_h = box
    theta = math.radians(angle_deg or 0.0)
    c, s = math.cos(theta), math.sin(theta)
    denom = c * c - s * s
    w_axis = max(1.0, (big_w * abs(c) - big_h * abs(s)) / denom)
    h_len = max(3.0, (big_h * abs(c) - big_w * abs(s)) / denom)
    cx, cy = x + big_w / 2.0, y + big_h / 2.0
    # Deskew rotates the source by +angle; a band (vertical when straight) runs along
    # (-sin, cos) in the source frame and the body axis along (cos, sin).
    along, across = (-s, c), (c, s)
    height, width = image.shape[:2]
    tops = []
    for lo, hi in ((-0.5, -1 / 6), (-1 / 6, 1 / 6), (1 / 6, 0.5)):
        pts = []
        for t in np.linspace(lo * h_len, hi * h_len, 7):
            for u in np.linspace(-w_axis / 4, w_axis / 4, 5):
                px = int(round(cx + along[0] * t + across[0] * u))
                py = int(round(cy + along[1] * t + across[1] * u))
                if 0 <= px < width and 0 <= py < height:
                    pts.append(image[py, px])
        if not pts:
            continue
        median = np.median(np.array(pts, dtype=np.float32), axis=0).astype(np.uint8)
        hsv = cv2.cvtColor(median.reshape(1, 1, -1)[:, :, :3], cv2.COLOR_BGR2HSV)[0, 0]
        tops.append(classify(hsv)[0][0])
    return tops


def assess_resistor(native: dict, image) -> dict:
    """Assess a resistor-reader result. Returns an assessment dict.

    ``supports_ok`` is False when an ``ok`` verdict is not supported by the
    reported evidence; the caller then holds the verdict as ``ambiguous``.
    """
    import numpy as np

    status = native.get("status") if isinstance(native, dict) else None
    result = {"assessor": ASSESSOR, "applies_to_native_status": status, "checks": [],
              "supports_ok": True, "failed_checks": [],
              "thresholds": {"min_band_width_ratio": MIN_BAND_WIDTH_RATIO,
                             "clip_level": CLIP_LEVEL,
                             "max_clipped_fraction_per_band": MAX_CLIPPED_FRACTION_PER_BAND,
                             "max_evaluable_angle_deg": MAX_EVALUABLE_ANGLE_DEG,
                             "min_band_color_share": MIN_BAND_COLOR_SHARE},
              "note": "heuristic consistency checks on the reader's own evidence; uncalibrated, "
                      "synthetic-only validation; never a confirmation"}
    if status != "ok":
        result["checks"].append(_check("scope", "not_applicable",
                                       f"native status {status!r}: nothing to hold back"))
        return result

    height, width = image.shape[:2]
    bands = native.get("band_boxes")
    candidates = native.get("candidates")
    problems = []
    if not isinstance(bands, list) or not bands:
        problems.append("no band boxes reported")
        bands = []
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        problems.append("no decoded candidate reported")
        top = {}
    else:
        top = candidates[0]
    sequence = top.get("sequence") if isinstance(top.get("sequence"), list) else None
    if sequence is None:
        problems.append("top candidate has no colour sequence")
    elif bands and len(sequence) != len(bands):
        problems.append(f"decoded {len(sequence)} bands but {len(bands)} band boxes reported")
    if native.get("bands_detected") is not None and native.get("bands_detected") != len(bands):
        problems.append(f"bands_detected={native.get('bands_detected')} but {len(bands)} boxes")
    boxes = []
    for index, band in enumerate(bands):
        box = band.get("bbox_xywh") if isinstance(band, dict) else None
        if not _valid_box(box, width, height):
            problems.append(f"band {index}: missing or out-of-bounds bbox {box!r}")
            continue
        if not (isinstance(band.get("color_candidates"), list) and band["color_candidates"]):
            problems.append(f"band {index}: no colour candidates")
        boxes.append([int(round(v)) for v in box])
    result["checks"].append(_check("evidence_complete", "fail" if problems else "pass",
                                   "; ".join(problems) or "one in-bounds box with colour "
                                   "candidates per decoded band"))
    if problems:
        result["supports_ok"] = False
        result["failed_checks"].append("evidence_complete")
        return result

    # --- band geometry -------------------------------------------------
    angle = (native.get("diagnostics") or {}).get("deskew_angle_deg") or 0.0
    if abs(angle) > MAX_EVALUABLE_ANGLE_DEG:
        result["checks"].append(_check("band_geometry", "not_evaluable",
                                       f"deskew angle {angle} deg too large to recover band widths"))
    else:
        widths = [_axial_width(box, angle) for box in boxes]
        median = float(np.median(widths))
        ratios = [round(w / median, 3) if median > 0 else 0.0 for w in widths]
        narrow = [i for i, r in enumerate(ratios) if r < MIN_BAND_WIDTH_RATIO]
        result["checks"].append(_check(
            "band_geometry", "fail" if narrow else "pass",
            (f"band(s) {narrow} narrower than {MIN_BAND_WIDTH_RATIO:.0%} of the median band width "
             f"({median:.1f} px): likely segmentation artefact" if narrow else
             "band widths mutually consistent"),
            band_widths_px=[round(w, 1) for w in widths], width_ratios=ratios,
            narrow_bands=narrow))
        if narrow:
            result["failed_checks"].append("band_geometry")

    # --- colour determinacy (reader's own per-band colour ranking) -------
    in_band_order = list(sequence) if top.get("orientation") != "reversed" else list(sequence)[::-1]
    weak = []
    for index, (band, color) in enumerate(zip(bands, in_band_order)):
        ranked = band["color_candidates"]
        first = ranked[0] if isinstance(ranked[0], dict) else {}
        share = first.get("score")
        if first.get("color") != color:
            weak.append({"band": index, "decoded": color, "top_color": first.get("color"),
                         "top_share": share, "problem": "decoded colour is not the band's top colour"})
        elif not isinstance(share, (int, float)) or share <= MIN_BAND_COLOR_SHARE:
            weak.append({"band": index, "decoded": color, "top_color": first.get("color"),
                         "top_share": share, "problem": "top colour holds no majority"})
    result["checks"].append(_check(
        "band_color_determinacy", "fail" if weak else "pass",
        (f"{len(weak)} band colour(s) not determined by the band evidence" if weak else
         "each decoded colour is its band's majority top colour"), weak_bands=weak))
    if weak:
        result["failed_checks"].append("band_color_determinacy")

    # --- colour consistency along each band ----------------------------
    if abs(angle) > MAX_EVALUABLE_ANGLE_DEG:
        result["checks"].append(_check("band_color_consistency", "not_evaluable",
                                       f"deskew angle {angle} deg too large"))
    else:
        unstable, seg_report = [], []
        for index, (box, color) in enumerate(zip(boxes, in_band_order)):
            tops = _segment_colors(image, box, angle)
            agree = sum(1 for t in tops if t == color)
            seg_report.append({"band": index, "decoded": color, "segment_top_colors": tops})
            if len(tops) == 3 and agree < 2:
                unstable.append(index)
            elif len(tops) < 3:
                unstable.append(index)
        result["checks"].append(_check(
            "band_color_consistency", "fail" if unstable else "pass",
            (f"band(s) {unstable}: decoded colour is not the top colour in at least 2 of 3 "
             "segments along the band" if unstable else "band colours stable along each band"),
            segments=seg_report, unstable_bands=unstable))
        if unstable:
            result["failed_checks"].append("band_color_consistency")

    # --- colour fidelity (channel clipping inside the reported bands) ----
    clipped = []
    for box in boxes:
        x, y, w, h = box
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + w), min(height, y + h)
        region = image[y0:y1, x0:x1]
        if region.size == 0:
            clipped.append(1.0)
            continue
        peak = region.max(axis=2) if region.ndim == 3 else region
        clipped.append(round(float(np.mean(peak >= CLIP_LEVEL)), 3))
    clipped_bands = [i for i, fraction in enumerate(clipped)
                     if fraction >= MAX_CLIPPED_FRACTION_PER_BAND]
    fail = bool(clipped_bands)
    result["checks"].append(_check(
        "band_color_fidelity", "fail" if fail else "pass",
        (f"band(s) {clipped_bands} have >= {MAX_CLIPPED_FRACTION_PER_BAND:.0%} pixels with "
         f"a clipped colour channel (>= {CLIP_LEVEL}): colour evidence for that band is weakened"
         if fail else "band colours not dominated by channel clipping"),
        band_clipped_fraction=clipped, clipped_bands=clipped_bands))
    if fail:
        result["failed_checks"].append("band_color_fidelity")
    result["supports_ok"] = not result["failed_checks"]
    return result
