# CAL-01 — Physical Calibration & Multi-Mode Camera Validation

- Worktree: `C:\Users\HP\Documents\Anastylos-integration-spatial`
- Branch: `integration/spatial-capture-v1`, HEAD `bcea6310` (uncommitted changes, see §11)
- Device: Samsung SM-A536B, `192.168.0.42:43947` (WiFi ADB)
- Date: 2026-10-30. Operator-assisted physical session.
- Installed APK (pulled from device, SHA-256): `7EA1B7FAC36931954685C16D378424F38A34E44F9B661FE4B7F1F5D0B059D7D0`
  (193,791,213 bytes, versionName 0.1.0, installed 09:55). Contains
  `assets/cal01-profile.json` = `E34F7B7D…BEFE` (validated profile, §2).

## Verdict: PASS

One physically calibrated camera (cam0, Back 0 · 5.2 mm, 1920×1080, manual
focus 40 cm) works stationary overhead, freehand in motion, and from an
oblique sideview with one shared CameraX/calibration/tracking/persistence/
receiver stack. Positive Guided Auto-Shutter remains honestly
PENDING_EVIDENCE (quality gate blocked, never bypassed) and is GUIDE-01 scope.

### Gate matrix

| Gate | Result | Evidence |
|---|---|---|
| G1 real board measurements | PASS | §2, Mahr caliper + Talmeter |
| G2 genuine calibration images | PASS | 30 ChArUco + 8 A3 views, phone originals |
| G3 valid intrinsics + distortion | PASS | joint fit RMS 0.49 px |
| G4 independent metric validation | PASS | held-out 0.58 px, cross-board 0.61 px, scale 481.0 vs 480.98 mm |
| G5 genuine valid Android pose | PASS | ACQUIRED, 7 markers, live RMS 0.53–0.64 px overhead |
| G6 invalidation + tracking-failure behavior | PASS | uncalibrated gate, LOST banner, dwell idle, no spurious shutter |
| G7 no regression CAMERA-01/02 + receiver | PASS | 430/430, 185 OK, 88 OK, compliance 0 errors |
| P4 overhead physical | PASS | §4, 60/60 receiver packages byte-identical |
| P5 freehand tracking | PASS | §5, loss/recovery, oblique ACQUIRED 1.1 px |
| P5 positive auto-shutter | PENDING_EVIDENCE | blocked by quality gate (`dark`↔`saturated`), GUIDE-01 |
| P6 sideview compatibility | PASS (assessment) | §6, ACQUIRED + live metric guidance vectors |

## 1. P1 — Configuration / capability audit

Calibration validity is bound to: physical camera ID (`Back 0`), analysis
WxH (1920×1080), still WxH, zoom (0 %), manual focus diopters (±0.05 D),
profile source ID. Binding is enforced fail-closed in
`GuidedCameraSource.resolveIntrinsics` + `MeasuredProfileSource`; any mismatch
yields a `…-uncalibrated` suffix that blocks metric pose in
`OpenCvPoseEstimator` (tested). No second camera system was built; overhead /
freehand / sideview share one stack.

Known blocker found in P1, still open: cold-start analysis-size restore gap —
after app restart the analysis stream binds 1280×720 although 1920×1080 is
selected; a manual 1280→1920 toggle rebinds and restores the measured profile.
Reproduced 3× during CAL-01 (CAMERA lane bug, filed as finding F1).

## 2. P2 — Physical calibration (joint two-board fit)

Boards (copy-shop prints on 5 mm MDF, verified flat and true to size):

- ChArUco A4: squares 30×30 mm, markers ≈24×24 mm (Mahr caliper), active area
  210×150 mm (Talmeter). DICT_6X6_250, 5×7, `legacy_pattern=false`.
- Surface A3: markers 42×42 mm (Mahr caliper), 7 markers ID 10–16,
  DICT_5X5_100. Diagonal anchor: **481.0 mm ±0.2** (operator, steel rule).

Focus breathing measured first (fx 1365→1407 across focus range): AF would
invalidate any profile → fixed manual focus 40 cm (2.5 D) for all captures.

Calibration images: genuine phone originals via paced capture
(`tools/adb/cal01tick.py` metronome on PC speakers): 30 ChArUco views
(pose-diverse, ~40 cm standoff) + 8 A3 views. Single-board fit extrapolated
20 px error at r≈0.7 where overhead A3 markers sit (150 mm board never reaches
that radius) → joint fit required:

- Script: `calibration/cal01_joint.py` (venv `calibration/.venv`:
  opencv-contrib 4.14.0.94, numpy 2.5.4, pillow 13.0.0).
- Sensor-frame pixels throughout (`IMREAD_IGNORE_ORIENTATION`; all stills are
  EXIF Orientation 6 — a display-frame fit swaps the principal point).
- Result `cal01-evidence/cal01-profile.json` (cam0):
  fx 1395.78, fy 1396.32, cx 956.27, cy 556.84, Brown-Conrady 5 coeffs.
- Fit RMS 0.49 px; held-out 0.58 px; cross-board A3 pooled 0.61 px
  (0.36–0.88 px per view); scale anchor 481.0 vs nominal 480.98 mm;
  spatial validator `accept`, no reasons/reviews, `metric_validity=validated`.
- Deterministic `source_id` (uuid5) and `calibration_view_ids` = fit+heldout
  satisfy the validator's provenance checks.

## 3. P3 — Native Android integration

- Asset `app/src/main/assets/cal01-profile.json` = profile bytes (E34F7B7D…).
- New: `guidance/CalibrationProfile.kt`, `camera/MeasuredProfileSource.kt`;
  `resolveIntrinsics` binds cameraId + WxH + zoom + focus; focus re-key at
  ±0.05 D (covers tap quantization 2.4668 D vs 2.5 D); factory fallback logs.
- Uncalibrated suffix explicitly blocks metric pose (gate tests added).
- Tests: 430/430 PASS (incl. `CalibrationProfileTest` 7,
  `OpenCvPoseEstimatorTest` 20 with 3 new gate tests), lint 0 errors.
- APK built offline with Temurin JDK 21, installed over WiFi ADB,
  hash-verified on device; live logcat proves
  `measured profile=measured:b64d3dcb-…` loads.

## 4. P4 — Stationary overhead verification (PASS)

- Mount: phone clamped ~40 cm over `surface_a3`. Tracking ACQUIRED, 7 markers,
  live RMS 0.53–0.64 px (screenshots `cal01-overhead.jpg`,
  `cal01-freehand-oblique.png` for oblique).
- Independent workstation PnP on overhead still `d4ccea44…`:
  t=[−180.5,−144.9,388.7] mm (≈40 cm height, tilt ≈0.02 rad), RMS 0.69 px.
- Sharpness reference calibrated on-device (ref 210.7).
- Receiver round-trip: session CAL01F40, **60/60 packages byte-identical**
  (manifest SHA-256 vs stored bytes, 0 mismatches), incl. the overhead still
  (`d4ccea44…`, package `7370ae74…`). Throughput ≈66 KB/s over WiFi+TLS
  (48×~575 KB in ~7 min). Phone UI: “0 waiting, 60 synced”.
- Pose age overhead: 242–345 ms (finding F2, vs 150 ms p95 target).

## 5. P5 — Freehand verification (tracking PASS, auto-shutter PENDING)

- Oblique freehand: ACQUIRED, 5–6 markers, RMS 1.1–1.2 px, pose age ~350–430 ms.
- Tracking loss: banner “tracking lost — aim at the board”, dwell idle, no
  shutter (fail-closed). Recovery: automatic reacquire + green marker overlays.
- Arm/Resume/Pause/Disarm state machine exercised; no duplicate/spurious
  shutter in any transition.
- Positive auto-shutter NOT achieved: with target `A3 top-down center`
  selected, dwell stays idle. Quality gate reports `dark` under Default
  profile and `saturated` under Bright — no passing point at this lighting.
  Thresholds were not touched (excluded). Honest verdict: PENDING_EVIDENCE,
  owned by GUIDE-01 (exposure/gate tuning + target progression).

## 6. P6 — Sideview compatibility (PASS, assessment)

Phone propped at a stationary oblique angle: Tracking valid, ACQUIRED with
6 markers (10,16,14,15,11,12), RMS 1.12 px, pose age 250 ms. With target
selected, Request NAVIGATING shows live metric guidance vectors
(“Move right 296 mm, Move down 209 mm, Move back 253 mm, Turn right 50°,
Tilt down 30°, Roll right 157°”) — screenshot `cal01-sideview2.png`.
No second-camera calibration attempted (out of scope).

## 7. Evidence paths and hashes

- `cal01-evidence/cal01-profile.json` SHA-256
  `E34F7B7D53D9F8C15D652BC3792EFF9505E83AA4FDF56F5DEF0300B8E058BEFE`
- `cal01-evidence/cal01-diag.json` SHA-256
  `97B590CFBF9906349DFF2A62FC1678C461AA87D06EF2DF040723026F1158836B`
- `cal01-evidence/cal01-scale-diag.json` SHA-256
  `8AC013BF7590F8C2DF54B706DDA872987914A1DC32DEED30AF5E9D2C1743701E`
- `cal01-evidence/calib*/`, `valid*/` phone-original calibration/validation
  views; `cal01-overhead.jpg` (`d4ccea44…`), `cal01-freehand-oblique.png`,
  `cal01-freehand-active.png`, `cal01-armed-paused.png`, `cal01-sideview.png`
  (LOST/fail-closed), `cal01-sideview2.png` (guidance vectors).
- Receiver: `%LOCALAPPDATA%\Anastylos\receiver\packages\` —
  60 CAL01F40 packages, all hash-verified (`C:\tmp\cal01verify.py`).
- Scripts: `calibration/cal01_charuco.py`, `cal01_joint.py`,
  `cal01_scale_check.py`; `tools/adb/cal01tick.py` (+ `cam02uidump.py`,
  `cam02sliders.py` reused).

## 8. Regression results (2026-10-30, post-physical)

| Suite | Command | Result |
|---|---|---|
| Android unit | `gradlew :app:testDebugUnitTest --offline` (JDK 21) | 430/430 PASS, 42 suites |
| Android lint | `:app:lintDebug` (P3, no code change since) | 0 errors |
| Receiver | `unittest discover -s tests` in `capture-companion/receiver` | 185 OK (3 skipped, pre-existing) |
| Spatial | `unittest discover -s tests` in `calibration/spatial` | 88 OK |
| Compliance | `py -3.12 tools/compliance/check.py` | 0 errors (1 warning + 10 reviews, pre-existing) |
| Calibration kit | venv `unittest discover -s tests` in `calibration` | 9/13, 4 known Windows-Cairo env failures (cairosvg/poppler absent) |

No test, fixture, or threshold was modified to obtain these results.

## 9. GUIDE-01 gap (VALID POSE → … → RECEIPT)

| Link | CAL-01 status |
|---|---|
| VALID POSE | ✅ proven (overhead / freehand / sideview) |
| LIVE TARGET CUES | ✅ proven (metric move/turn/tilt/roll vectors live) |
| DWELL | ✅ gating proven fail-closed; positive dwell PENDING (quality-blocked) |
| AUTO-SHUTTER | PENDING — needs passing quality verdict at working light |
| CAPTURE | ✅ proven (manual + sampling paths, SHA-256 identity) |
| RECEIVER RECEIPT | ✅ proven (60/60 byte-identical, idempotent, no dupes) |

Smallest gap: quality-gate/exposure tuning so a well-lit board passes
(`dark`↔`saturated` currently leaves no passing point), then target
progression + dwell → auto-shutter → delivery acceptance.

## 10. Findings for follow-up (not CAL-01 defects)

- F1: cold-start analysis-size restore gap (stream binds 1280×720, needs
  manual 1280→1920 toggle). CAMERA lane bug, reproduced 3×.
- F2: pose age 242–430 ms vs 150 ms p95 target (1920×1080 analysis load?).
- F3: receiver leaves zombie `.upload-*.zip` temp files after client
  disconnect; needs stale-temp cleanup + client resume UX.
- F4: uploader gives no logcat diagnostics; stall diagnosis was manual.
- F5: `uiautomator dump` fails (“could not get idle state”) while camera
  preview runs; screenshots + finger taps were the fallback. ADB taps are
  ignored by several Compose controls on this screen.
- F6: phone PIN-lock during long sessions interrupts WiFi-ADB flow
  (10-min timeout is a device maximum).

## 11. Files changed (CAL-01 scope, uncommitted)

New: `cal01-evidence/` (profile, diags, views, screenshots),
`calibration/cal01_charuco.py`, `calibration/cal01_joint.py`,
`calibration/cal01_scale_check.py`, `tools/adb/cal01tick.py`,
`app/src/main/assets/cal01-profile.json`,
`camera/MeasuredProfileSource.kt`, `guidance/CalibrationProfile.kt`,
`guidance/CalibrationProfileTest.kt`, this report.
Modified: `camera/GuidedCameraSource.kt` (resolveIntrinsics),
`guidance/OpenCvPoseEstimator.kt` (uncalibrated gate),
`OpenCvPoseEstimatorTest.kt` (3 gate tests).
CAMERA-01/02 + `tools/adb` changes preserved untouched; no commit/push/merge
performed; main, E3-B, Assembly Memory, print masters, other worktrees
untouched.
