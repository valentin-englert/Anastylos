# ANASTYLOS automatic tray counter

Reports evidence-backed addition/removal candidates inside configured
tray regions. Consumes change-detection pair output (`result.json` plus
rectified before/after images); it never re-runs detection and never
writes to memory. Use the calibration virtualenv (OpenCV, NumPy).
No new dependencies.

```powershell
..\.venv\Scripts\python count.py --config trays.json --pairs-dir ..\change-detection\selftest_output\results --out tray_out
..\.venv\Scripts\python count.py --config trays.json --result-json ..\change-detection\selftest_output\results\single_01\result.json --out tray_out
```

The `selftest_output` paths above are generated, not committed: run the
change-detection synthetic selftest first (see
[`change-detection/README.md`](../change-detection/README.md)).

## Configuration

Trays are named millimetre rectangles in calibrated frame coordinates
(same `[x, y, w, h]` convention as the kit `tray_cell` zones). Copy the
rectangles from `calibration/calibration-config.json` for the sheet you
photograph:

```json
{
  "trays": [
    {"id": "T1", "frame": "surface_a3", "rect_frame_mm": [318, 50, 26, 26]}
  ],
  "device_area": {"frame": "surface_a3", "rect_frame_mm": [10, 45, 40, 20]},
  "params": {"min_overlap": 0.5, "delta_threshold": 8.0, "surface": "light"}
}
```

Geometry is validated strictly: unique ids, known frames as plain names,
finite positive rectangles, overlap in (0, 1], positive threshold,
surface `light` or `dark` (dark inverts the brightness polarity).
`device_area` is optional and stages device removals for pairing.

## Output

Per pair, `tray_result.json` plus `tray_overlay.png` (zones and
candidates drawn on the rectified after-image), and a run `summary.json`.
Each tray reports a verdict:

- `addition` / `removal` — every candidate inside the tray agrees, with
  measured brightness delta, overlap, quality, and image references.
- `unchanged` — no region touches the tray.
- `ambiguous` — boundary crossings, weak signals, or mixed events.
- `unknown` — unusable detection, missing images, or unclassifiable data.
- `not_observed` — the pair shows a different calibrated view.

Observations and inference stay separate: `candidates` are observed
regions; `count_estimate` is always null because one region may hold
several overlapping objects. When a device removal and a tray addition
co-occur in one pair, `proposed_links` suggests the pairing with an
area ratio and `requires_confirmation: true` — it never asserts identity.

## Limits

- Pairing only works within a single pair (one view). Device-on-fixture
  to tray-on-surface pairing across views needs session-level matching
  and is unresolved in this iteration.
- Brightness polarity assumes a uniform tray surface; textured or
  reflective trays raise false `ambiguous` verdicts.
- Occlusion is reported only as a weak-signal `ambiguous` case; the
  counter cannot see what detection missed.

## Test

```powershell
cd tray-counter
..\.venv\Scripts\python -m unittest discover -s tests -v
```

14 deterministic tests build fake detection pairs (exact-brightness
rectified images, no camera): addition, removal, multiple and
overlapping objects, boundary crossing, unchanged, weak signal,
registration failure, frame mismatch, pairing proposal, missing
images, JPEG inputs, config validation, and CLI/overlay output.
