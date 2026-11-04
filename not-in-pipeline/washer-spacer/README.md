# ANASTYLOS washer and spacer recognition

Detects and geometrically characterizes small fastening parts —
flat washers, split lock washers, cylindrical spacers, hexagonal
standoffs — from top-view or side-view crops, with evidence and
uncertainty. Deterministic OpenCV plus NumPy baseline: contour
segmentation, inner/outer boundary analysis, ellipse fitting,
shape descriptors, and a standard-library-only rule classifier.
No pretrained model, cloud API, or downloads.

```powershell
..\.venv\Scripts\python washer.py read --image path\to\crop.png --view top --family flat_washer --out washer_out
..\.venv\Scripts\python washer.py read --image path\to\crop.png --view side --frame-metadata '{"pixels_per_mm": 10.0}' --out washer_out
..\.venv\Scripts\python washer.py eval --labels labels.json --images-dir crops\ --out washer_eval_out
..\.venv\Scripts\python washer.py compare --baseline a\result.json --candidate b\result.json --out washer_out
```

## Separate concepts

- **Observable geometry**: boxes, diameters, hole sizes,
  concentricity, split gaps, thickness, openings, bands.
- **Candidate family**: a visual hypothesis (`flat_washer`,
  `split_washer`, `spacer`, `standoff`, `unclassified`), ranked
  with alternatives, never a verified part identity. The operator
  may declare the family via `--family`.
- **Measured dimensions**: pixels always; millimetres only with
  valid frame metadata. Sizes are reported, never mapped to
  nominal hardware standards.
- **Possible matching hardware**: the `compare` command matches
  observations across two results by dimension similarity with an
  explicitly uncalibrated heuristic score.
- **Verified assembly relationship**: never asserted here.
  Similar dimensions never prove two parts belong together.

Hidden geometry, internal threading, materials, and standards are
never inferred from appearance.

## Views

Top view measures outer diameter (ellipse fit), inner hole
diameter, concentricity, circular-versus-hexagonal outline
(hexagons also report across-corners/across-flats calipers), and
split-gap angle and direction. A circular ring reads
flat-washer/spacer ambiguous without a declared family: a spacer
end looks identical from above.

Side view measures visible length and thickness, end openings,
flat bands, and seam interfaces. Thin profiles read as washers;
slits read as splits; tall blocks with both end openings read as
spacers, or standoffs when flat bands are visible.

Verdicts per observation: `ok`, `ambiguous` (rivals, grouped
parts, partial outlines), `unknown` (unmeasurable), plus
`unavailable` for unusable crops or a silent segmenter.

## Multiple parts and stacks

Each result assigns stable observation IDs (`o1`… sorted
top-to-bottom, left-to-right). IDs never persist across images.
Touching parts that share one contour, and merged stacks with
seam interfaces, report one grouped observation with unknown
exact count. Separable stack layers stay separate and gain
`stacked_with` cross-references.

## Geometry

Source-frame pixels; origin top-left; +x right; +y down; angles
clockwise from +x in [0, 360). Millimetres appear only when
`--frame-metadata` carries a finite `pixels_per_mm` inside
[0.05, 500]; anything else yields a `calibration_error` and
pixel-only output. Every dimension carries an uncertainty
estimate covering fit residuals and quantization, widened when
perspective is suspected.

`analyze()` accepts `segmenter` and `ranker` callables so future
segmentation or classification providers can plug in without
touching the pipeline.

## Evaluation

Labels: `{"images": [{"file": "crop.png", "view": "top",
"family": "flat_washer", "class": "flat_washer", "count": 1,
"pixels_per_mm": 10.0, "outer_mm": 22.0, "inner_mm": 11.0}]}`.
Supported reference dimensions: `outer_mm`, `inner_mm`,
`thickness_mm`, `length_mm`, `across_corners_mm`,
`across_flats_mm`. The report counts classification outcomes and
mean/max absolute dimensional errors. Synthetic fixtures prove
the harness only.

Real-photo workflow: photograph parts against a plain
background, one view per crop, record file, view, declared
family, class, count, scale, and caliper-measured dimensions in
`labels.json`, then run `eval`. No real-hardware validation has
been performed yet; see Limitations.

## Test

```powershell
cd washer-spacer
..\.venv\Scripts\python -m unittest discover -s tests -v
```

82 deterministic tests: diameters and holes, flat versus split,
round versus hex, top and side views, rotation, scale,
calibration valid/absent/invalid, multiple and touching parts,
separated and merged stacks, occlusion, glare, shadows,
perspective, lookalikes, resolution floors, uncertainty
coverage, comparison, eval accounting, CLI, annotation.

## Limitations

- Validated on deterministic synthetic renders only. No
  real-hardware accuracy is claimed and no real part photos
  exist in this environment yet.
- Tight crops with plain backgrounds; busy scenes and
  overlapping piles beyond tangent pairs are out of scope.
- Threading, materials, finishes, and standards are never
  identified; end openings only note visible dark regions.
- Heavy occlusion and strong tilt report `ambiguous`/`unknown`
  by design; confident spurious segments under a family hint
  are the main false-confidence risk on real photos.
- Millimetre output needs operator-supplied frame metadata.
- No Assembly Memory writes in this iteration.
