# HARDWARE-01 — first real-world validation (2026-10-27)

First reproducible, evidence-backed ANASTYLOS run on genuine smartphone
photos. Frozen config, no threshold tuning, no production-code changes.

## Outcome

7/7 pairs classified correctly, 0 false alarms, 0 misses. Registration
valid on all 14 images with all 7 A3 markers (IDs 10-16). All 6
ground-truth boxes hit, centroid error <= 2.4 mm. Tray counter 84/84
verdicts correct. Memory import + confirmation gate behave. Absolute
metric accuracy remains UNVERIFIED: the print was never measured
(`measured_print_scale` still unset), and a photo-based ruler
cross-check is not a substitute.

## Dataset

8 Samsung SM-A536B photos (3468x4624 px portrait after EXIF orientation,
see `inventory.json` for hashes, sizes, EXIF). Printed A3 surface at 100%
on 5 mm MDF, camera mounted above, transferred via Syncthing.
Originals in `C:\ANASTYLOS\incoming` were only read; byte-identical
copies live in `hardware-01/originals/` (SHA-256 verified per file).

## Benchmark R01-R07 (all `surface_a3`)

Labels verified by viewing each original; no discrepancies found.

- R01 144110->144119 none: out-of-sheet screw + leg at frame edge only
- R02 144119->144130 none: out-of-sheet screw + foot at frame edge only
- R03 144130->144138 none: out-of-sheet screw only
- R04 144138->144153 single: screw added to work area
- R05 144153->144201 multiple: screw work area -> tray 1
- R06 144201->144207 multiple: screw tray 1 -> tray 7
- R07 144207->144215 single: screw removed from tray 7

`manifest.csv` carries counts; `manifest_gt.csv` adds boxes measured from
single rectified images (brightness segmentation in constrained windows,
+1 mm margin; method in `measure_gt.py`). Each GT crop in `gt_check/`
was visually confirmed to wrap the screw body. GT shares only the
calibrated frame mapping with the detector, not the before/after signal.

## Calibration validation

- Markers: 7/7 (IDs 10-16, `DICT_5X5_100`) on all 14 images, view auto
  `surface_a3`, no unknown IDs, `--view` gate agreed everywhere.
- Registration: 14/14 valid. RMS 0.43-0.48 mm (gate 0.5), max 1.31-1.48
  mm (gate 1.5), 27/28 RANSAC inliers per image (1 outlier corner each).
- Rectified alignment: residual shift <= 0.03 mm, phase response
  0.93-0.95. Overlays show ROI contours hugging the printed work area
  and tray cells; region boxes sit inside the correct kit zones.
- Robustness: exposure varies (144153 darker), a person moves at frame
  edges, framing drifts slightly between shots. No-change pairs stay at
  diff p99 ~1.4 (threshold 30); change pairs peak at 92-101. Margin is
  wide on both sides.
- Scale: photo cross-checks suggest the print is within a few percent
  of 100%, but geometry limits (off-plane ruler, perspective, crude
  picks) prevent a firm number. Extrapolating the homography to the
  steel ruler reads 15% short: the planar map must not be used outside
  the marker hull (uncorrected lens distortion; barrel compression at
  frame edges is the leading hypothesis). Metric accuracy is therefore
  NOT established. Next step is a physical measurement, not analysis.

## Frozen baseline detection

Config: harness canonical `a2308f66...5812`, kit bytes `a5f32cb5...357e`
(`--require-config-sha256` gate passed). Untouched outputs in `results/`;
bbox-scored re-run with identical detection bytes in `results_scored/`.

Per-pair verdicts: R01-R07 all PASS (expected == detected, counts and
boxes). Cross-pair centroid repeatability of identical screw placements
is ~0.1 mm across independent registrations. No failures to diagnose;
no relabeling; no tuning.

Watch item (not a failure): residuals pass with little headroom and one
RANSAC outlier recurs per image. Consistent with uncorrected phone-lens
distortion. Characterize distortion (ChArUco) before considering any
gate change; do not retune on this set.

Note: OpenCV honors EXIF orientation (pipeline sees 3468x4624 portrait).
Some viewers display the raw frame rotated 90 deg; this affects display
only — all pipeline geometry was verified in frame-mm.

## Tray counter

Config `trays.json` copied verbatim from the kit (12 cells + work area
as device area; default params). Run over frozen `results/`.

- Tray 1 addition (R05), tray 1 removal (R06), tray 7 addition (R06),
  tray 7 removal (R07): all PASS, overlap 1.0, strong deltas.
- Unchanged trays 80/80: PASS. Zero ambiguous/unknown (strong signals).
- R05 proposes device-R2 -> T1-R1 with `requires_confirmation: true`
  and "not the same object; co-occurrence only". R06 tray-to-tray move
  is correctly NOT linked. `count_estimate` stays null everywhere.
- No identity claim is made from visual movement. PASS.

## Assembly memory

Disposable session `memory_test/`: R04+R05 import yields obs-0001..0003
with `result_sha256` + `kit_sha256` provenance. `where` answers
`unassigned` (observation) and `unknown` (empty tray) correctly.
Unconfirmed `record tray-link` is rejected and writes nothing. No links
created, no existing session touched. PASS.

## Regression

- change-detection 3 OK; synthetic selftest 16/16 (reference match)
- tray-counter 14 OK; assembly-memory 21 OK
- calibration 13 run: 9 pass, 1 failure + 3 errors — identical to the
  documented Cairo/poppler baseline (pre-existing, unrelated)
- compliance check: 0 errors, 8 open reviews (baseline unchanged)

## Capabilities

Validated with real hardware: A3 marker registration + view
confirmation; no-change rejection under out-of-ROI motion and exposure
drift; single/multiple counting on a small reflective screw; mm
localization (<= 2.4 mm centroid, ~0.1 mm repeatability); tray
add/remove verdicts with unconfirmed co-occurrence links; memory import
with provenance and a working confirmation gate.

Tested but unsuccessful: absolute metric scale from photos alone
(homography extrapolation to the steel ruler reads 15% short; method
limit, needs physical print measurement).

Not yet tested: other sheets/views, larger or occluded parts, strong
glare, fixture boards, ChArUco distortion work, any other reader on
real photos, phone capture-companion checklist, multi-step disassembly
sequences.

## Blockers and next actions

1. Measure the print (both 100 mm lines + marker sizes, steel ruler /
   caliper) and record `measured_print_scale`; then re-score metric
   accuracy. Nothing else unblocks it.
2. Characterize lens distortion (ChArUco) to explain the RMS headroom
   and recurring RANSAC outlier; decide gates from data, not this set.
3. Grow the benchmark: more parts, trays, views, lighting; keep GT
   single-image and human-verified.
4. Keep .venv usable: its base interpreter lives outside any sandbox;
   document the approved-Python path for future validation runs.

## Reproduce

Environment: Python 3.12.10, OpenCV 4.14.0, NumPy 2.5.4
(see `evaluation.json`). Exact commands: `commands.log`.
Inputs: `originals/`, `pairs/`, `manifest.csv`, `manifest_gt.csv`,
`trays.json`. Outputs: `results/`, `results_scored/`, `tray_out/`,
`memory_test/`, `evaluation.json`, `gt_boxes.json`, `gt_check/`.
No production file was modified; `git status` shows only the pre-existing
capture-companion work plus this new `hardware-01/` directory.
