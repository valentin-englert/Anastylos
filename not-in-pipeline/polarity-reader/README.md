# ANASTYLOS polarity and orientation recognition

Detects visible polarity and orientation markers on electronic
components from tight crops, with evidence and uncertainty.
Deterministic OpenCV plus NumPy baseline: component-body fitting,
geometric marker detectors, and a standard-library-only rule
interpreter. No pretrained model, cloud API, or downloads.

```powershell
..\.venv\Scripts\python polarity.py read --image path\to\crop.png --family diode --out polarity_out
..\.venv\Scripts\python polarity.py read --image path\to\crop.png --roi 20,20,360,360 --family ic --reference-kind silkscreen --reference-angle 90 --out polarity_out
..\.venv\Scripts\python polarity.py read --image path\to\crop.png --frame-metadata '{"pixels_per_mm": 4.0}' --out polarity_out
..\.venv\Scripts\python polarity.py eval --labels labels.json --images-dir crops\ --out polarity_eval_out
```

## Three separate concepts

- **Observed marker**: geometry in the source frame (type, box,
  centroid, orientation vector/angle). Family-independent.
- **Interpretation**: what the marker means electrically. Depends on
  the component family, which the operator declares via `--family`
  (`diode`, `aluminum_electrolytic`, `tantalum`, `ic`, `connector`,
  `unknown`).
- **Installation orientation**: claimed only with an explicit
  reference observation (`--reference-kind` plus `--reference-angle`,
  e.g. a silkscreen mark or pre-disassembly photo). A visual marker
  alone never proves how the part was originally installed.

## Markers

- `band`: transverse bar near one body end (diode cathode band,
  tantalum end bar). Without a declared family the diode-cathode
  and tantalum-anode readings stay ranked rivals.
- `stripe`: longitudinal side bar (electrolytic sleeve stripe).
  Stripes are never equated with one polarity: unhinted readings
  rank aluminum-negative against tantalum-positive.
- `dot` / `notch`: IC pin-1 dot and package notch. A lone cue stays
  ambiguous against cosmetic lookalikes; agreeing dot and notch
  clear the margin. Pin 1 sits counter-clockwise from the notch.
- `key`: connector keying (latch, edge tab, corner cut).
- `topmark`: printed top text. Always ambiguous: upright 0/180 deg
  hypotheses need OCR or an operator read.

Verdicts: `ok` (winner clears the floor with margin), `ambiguous`
(weak evidence or rivals), `unknown` (no body or no marker),
`unavailable` (crop too small or no detector observation).

## Geometry

Source-frame pixels; origin top-left; +x right; +y down; angles in
degrees clockwise from +x in [0, 360). Millimetre centroids are
reported only when `--frame-metadata` carries a finite
`pixels_per_mm` inside [0.05, 500]; anything else yields a
`calibration_error` and pixel-only output.

`analyze()` accepts `detector` and `ranker` callables so a future
segmentation or ranking provider can plug in without touching the
pipeline.

## Evaluation

Labels: `{"images": [{"file": "crop.png", "marker": "band",
"family": "diode", "angle_deg": 12.0}]}`. `family` is an optional
operator hint (the normal workflow: the operator knows the part);
`angle_deg` checks the marker direction within 25 deg. Ambiguity is
a valid finding, so the report also names `truth_kept` per
undecided row. Synthetic fixtures prove the harness only.

Real-photo workflow: photograph one component per tight crop,
record the file, marker type, declared family, and measured marker
direction in `labels.json`, then run `eval`. No real-hardware
validation has been performed yet; see Limitations.

## Test

```powershell
cd polarity-reader
..\.venv\Scripts\python -m unittest discover -s tests -v
```

60 deterministic tests: every marker family, rotation, scale,
brightness, shadow, glare, occlusion, damage, polarity ambiguity,
absent markers, false positives, installation claims, calibration
validity, adapters, eval accounting, CLI, annotation.

## Limitations

- Validated on deterministic synthetic renders only. No
  real-hardware accuracy is claimed and no real component photos
  exist in this environment yet.
- Single tight crops with one centered body; neighbours and busy
  backgrounds are out of scope.
- Heavy glare, strong shadow gradients, and large occlusions
  report `ambiguous`/`unknown` by design; damaged markers can
  surface lookalike features that stay non-`ok` either way.
- A confident spurious marker of the expected kind under a family
  hint is the main false-confidence risk on real photos.
- Printed text is detected, never read (no OCR here).
- Millimetre output needs operator-supplied frame metadata.
- No Assembly Memory writes in this iteration.
