# HARDWARE-02 — disassembly/reassembly step (2026-10-27)

One complete physical cycle on a Raspberry Pi case: screw installed (A),
removed (B), in tray 1 (C), reinstalled (D). Frozen config, no tuning,
no production-code changes.

## Outcome

Partial PASS with honest failures. BC (tray addition) and CD (tray
removal + case reinstallation) detect correctly with 3/3 GT boxes hit.
AB and AC are `registration_invalid`: image A misses the max
reprojection gate by 0.003 mm, so no coordinates are emitted and memory
import refuses them — the pipeline refusing rather than guessing. The
reassembly step still demonstrates end-to-end: CD localizes the
reinstalled screw to 0.8 mm of GT, and a where query retrieves the
mounting location with evidence after explicit operator confirmation.
Case-surface coordinates are paper-plane projections, NOT verified 3D.

## Dataset

4 SM-A536B photos (4624x3468 raw), same A3 setup as HARDWARE-01, ~1 h
later. Hashes in `inventory.json`; byte copies in `originals/`.
Labels verified against the originals (full frames + case/tray crops);
no discrepancies. Removed part: top-left case screw. Note: the tray-1
screw in C looks silvery while case screws look brass — reflection
angle, not an identity signal; identity below is operator-asserted only.

## Pairs (all `surface_a3`, independent)

- AB A->B, expected single: top-left case screw removed (elevated
  surface). RESULT: registration_invalid (A: max 1.5026 mm > 1.5 mm
  gate). No regions, no coords. FAIL (availability), correct refusal.
- BC B->C, expected single: screw placed in tray 1. RESULT:
  single_change, 1 region in tray 1, 1/1 GT boxes, centroid err
  2.2 mm. PASS.
- AC A->C, expected multiple: case removal + tray addition. RESULT:
  registration_invalid (same A failure). FAIL (availability).
- CD C->D, expected multiple: tray emptied + screw reinstalled.
  RESULT: multiple_changes, 3 regions: R1 tray removal, R2 case
  re-addition (0.8 mm from GT), R3 weak reflection on the black case
  (mean diff 33.8 vs threshold 30, area 7.75 mm2). 2/2 GT boxes hit;
  R3 unmatched -> flagged false_positive. PASS with documented extra.

## Registration diagnosis (A failure)

All 7 markers (IDs 10-16) detected in all 4 images; view auto
`surface_a3`. B/C/D pass (RMS 0.47-0.49, max 1.45-1.46). A fails only
on max (1.5026 vs 1.5; RMS 0.48 fine), with the usual 27/28 inliers.

Root-cause analysis (read-only, repo code reused, nothing changed):
the worst corner is marker 10 corner 0 in ALL 12 real photos
(HARDWARE-01 + HARDWARE-02, 1.31-1.50 mm) — fully systematic. 4x
closeups show clean print (no defect), so this is geometry, not ink:
local sheet lift near that corner and/or uncorrected lens-distortion
residual at that frame position. Next falsification: re-shoot with the
sheet rotated 180 deg (outlier follows the physical corner -> lift;
follows the frame position -> distortion). Observation for future
design (not a proposal): the max gate counts the RANSAC-rejected
outlier that the homography itself does not use.

## Parallax and screws vs non-screw changes

- The case is elevated (height unmeasured); its screws' frame-mm are
  paper-plane projections. R2/CD matches GT to 0.8 mm IN THE PAPER
  PLANE; true 3D position is unverified and unclaimed.
- R3/CD is a specular/illumination change on the glossy black case
  (D is visibly darker overall), not a part. Kept as unmatched region
  and imported as a candidate observation the operator does NOT link.
- Screwdriver moves (B, C, D) and the loose screw below the sheet are
  all outside the ROI and produce no regions. The case itself never
  moves; no casing-displacement regions occur.

## Tray counter

- BC: T1 addition (overlap 1.0), device unchanged, no link. PASS.
- CD: T1 removal PASS; device AMBIGUOUS (R2 labeled "removal" by the
  brightness-polarity model, which inverts on the black case since the
  brass screw is brighter than its background; R3 weak/ambiguous at
  delta 4.94 < 8). No link proposed: reassembly direction
  (tray-removal + device-addition) is not paired by design. Honest
  AMBIGUOUS + model-limit finding.
- AB/AC: all trays + device unknown with explicit "unusable" reasons.
  PASS (correct propagation). No identity asserted anywhere. PASS.

## Assembly memory (disposable `memory_test/`)

- AB import REFUSED (ValidationError, nothing written). PASS.
- BC -> obs-0001; CD full -> obs-0002 (R1 tray), obs-0003 (R2 mount),
  obs-0004 (R3 reflection). PASS.
- Unconfirmed tray-link for obs-0003 REJECTED. PASS.
- Operator-confirmed link (link-0001, `--confirmed-by
  HARDWARE-02-operator`, mission-authorized): obs-0003 -> tray 1.
  Basis is the operator's performed sequence (BC screw enters tray 1,
  CD screw leaves it and reappears at the case corner), NOT pipeline
  identity proof. R3 is deliberately left unlinked.
- where tray 1 -> LOCATED: mounting bbox [154,81,6.75,6.25],
  centroid [157.25,83.84], CD/R2 evidence + result SHA-256 + human
  basis. where obs-0003 -> assigned. where tray 2 -> unknown. PASS.

Temporal caveat: the mounting observation comes from the CD
re-addition because the AB removal has no coords. Live use would link
at step C against the AB removal observation.

## Capabilities

Validated: tray addition/removal on paper plane; reinstalled-screw
localization (paper plane, 0.8 mm); reflection-region flagging
(unmatched + weak/ambiguous); registration refusal at the gate with
exact margin; invalid-import refusal; unconfirmed-link rejection;
operator-confirmed link + evidence-backed where retrieval; unknown
propagation through tray counter and memory queries.

Tested but unsuccessful: AB/AC detection (registration gate miss by
0.003 mm); device-side event polarity on black surfaces (inverted
label); reassembly-direction pairing (not implemented by design).

Not tested: elevated-surface 3D accuracy (needs case-height model or
stereo); other case screws/positions; full multi-screw disassembly;
sheet-rotation falsification of the marker-10 bias.

## Blockers and next actions

1. Re-shoot with sheet rotated 180 deg to split lift vs distortion
   for the marker-10 corner bias; then decide (distortion calibration
   via ChArUco, sheet re-mounting, and/or gate rationale) from data.
2. Physical print measurement still open (from HARDWARE-01).
3. Tray-counter polarity on dark devices; reassembly-direction pairing
   — contract decisions, not blind edits.
4. Height-aware reporting for elevated parts before any 3D claim.

## Reproduce

Env: Python 3.12.10, OpenCV 4.14.0, NumPy 2.5.4
(`evaluation.json`). Commands: `commands.log`. Inputs: `originals/`,
`pairs/`, `manifest.csv`, `manifest_gt.csv`, `trays.json`. Outputs:
`results/`, `results_scored/`, `tray_out/`, `memory_test/`,
`evaluation.json`, diagnostics (`corners_A.png`, `corner10_*.png`,
`marker10_*.png`, case/tray crops). No production file modified;
Capture Companion and hardware-01/ untouched; nothing committed.
No video tracking or automatic identity verification is claimed.
