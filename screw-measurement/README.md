# ANASTYLOS calibrated screw measurement

Measures visible screw geometry from photographs through verified spatial
calibration. Deterministic OpenCV plus NumPy pipeline: image
normalization, contour extraction, geometric fitting, and explicit
measurement-quality checks. No pretrained model, cloud API, or downloads.

```powershell
..\.venv\Scripts\python measure.py measure --image path\to\top.png --mode top --out measure_out
..\.venv\Scripts\python measure.py measure --image path\to\side.png --mode side --calibration calib.json --out measure_out
..\.venv\Scripts\python measure.py measure --image path\to\side.png --mode side --roi 20,20,360,360 --calibration calib.json
..\.venv\Scripts\python measure.py eval --labels labels.json --images-dir photos\ --calibration calib.json --out measure_eval_out
..\.venv\Scripts\python measure.py compare --a run_a\result.json --b run_b\result.json --out measure_compare_out
```

## Modes

Two explicitly different views; `--mode` is required.

- `top`: visible head diameter (pixels, millimetres when calibrated;
  across-flats for hex heads, with across-corners reported alongside),
  head outline measurements (area, perimeter, circularity, vertices,
  solidity), head shape (`round`, `hex`, `irregular`, `unknown`),
  geometric center, and candidate comparison via `compare`.
- `side`: visible overall length, approximate shaft length and outer
  diameter where both long edges segment reliably, and the head/shaft
  boundary when a width step is observable.

Shaft length is never inferred from a top-down photograph: top-view
results carry `visible_shaft_length` with status `unsupported`.

## Calibration

Millimetres appear only with a valid calibration file. Without one (or
with an invalid one) the pipeline returns pixel-only measurements and
`value_mm: null` with reasons. Two equivalent file forms:

```json
{"pixels_per_mm": 12.5, "pixels_per_mm_std": 0.05,
 "frame": "surface_a3", "provenance": "rectified 2026-10-26 run 4"}
```

```json
{"reference": {"length_mm": 20.0, "pixels": 250.0,
               "label": "check line H, photo 3"},
 "frame": "surface_a3", "provenance": "ruler in screw plane, photo 3"}
```

Rules: scale must be finite and in (0, 1000]; `provenance` must name the
source; a file holding both forms must agree within 1% or it is rejected.
Frame names reuse the calibration-kit vocabulary (`surface_a3`,
`surface_a4`, `fixture_front`, `fixture_back`, `charuco_board`); anything
else is kept as `custom:<name>`. Millimetre values share the calibration
plane; off-plane parts are not corrected.

## Result

`measure` writes `result.json` plus `annotated.png` (the fitted circle /
silhouette boundary and every measurement line). Each result holds the
source image, mode, crop, frame (x right, y down, kit axis convention),
calibration provenance, detected geometry, measurements with units and
uncertainty, quality indicators, verdict `ok` / `ambiguous` / `unknown`,
reasons, and evidence references.

Uncertainty intervals combine contour variation (radial/profile spread
with a 0.5 px quantization floor) and calibration uncertainty; tilt or
taper widens them to span the observed spread. Precision is never
invented from image resolution alone.

Never estimated: nominal screw specifications, thread pitch/type, and
drive size. They are listed under `not_estimated` in every result.

Perspective or occlusion that invalidates a metric value reports that
value as `unknown` (millimetres null) instead of guessing; partial
evidence reports `ambiguous` with the visible portion flagged.

## Evaluation

Labels: `{"images": [{"file": "s.png", "mode": "side",
"truth_mm": {"visible_overall_length": 26.0}}]}`. The report gives
absolute millimetre errors where calibration is valid, pixel errors
where `truth_px` is given, the verdict mix, and one overlay plus result
per image. Synthetic fixtures prove the harness only.

`compare` judges two head diameters (or another shared measurement):
`consistent` when millimetre intervals overlap, `different` when they do
not, `uncertain` without two-sided calibration or with unknown inputs.

## Test

```powershell
cd screw-measurement
..\.venv\Scripts\python -m unittest discover -s tests -v
```

## Provider seam

`analyze(..., segmenter)` accepts a `(gray, context) -> mask` callable so
a future segmentation provider can plug in without touching the
pipeline; `None` keeps the built-in segmentation.

## Limitations

- Validated on deterministic synthetic renders only: nominal geometries,
  scale, in-plane rotation, mild/strong perspective, glare, shadow,
  touching objects, and occlusion. No real-hardware accuracy is claimed
  and no real screw images exist in this environment yet.
- Out-of-plane tilt that foreshortens a side view without converging
  edges cannot be detected from one photo; keep the screw in the
  calibration plane.
- Heavy glare, strong perspective, and large occlusions report
  `ambiguous` or `unknown` by design.
- No Assembly Memory writes in this iteration.
