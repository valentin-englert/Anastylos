# CLAUDE-ANDROID-REVIEW — Active Vision usability takeover (GUIDE-01 continuation)

- Worktree: `C:\Users\HP\Documents\Anastylos-integration-spatial`,
  branch `integration/spatial-capture-v1`, base HEAD `b1f11399`
  (GUIDE-01 WIP). All changes below are **uncommitted**.
- Date: 2026-10-30, 15:30–16:40. Devices: Samsung SM-A536B
  (`192.168.0.42:43947`, CAL-01 phone) and Xiaomi Redmi Note 10 Pro
  M2101K6G (`sweet`, SM7150, Android 13; operator-provided second phone).
- Evidence labels: **MEASURED** = device log/screenshot from this session;
  **CODE** = read in source; **UNIT** = JVM/Robolectric test.

## 1. Initial findings (before any change, MEASURED on SM-A536B)

Live guided screen at 15:31–15:38, APK `17772C20` (GUIDE-01 WIP):

| Symptom | Measurement |
|---|---|
| UNCALIBRATED banner, no metric pose | stored analysis size 1280×720; CAL-01 profile bound to exactly 1920×1080 |
| `Quality Rejected([dark])` | full-frame dark fraction 0.081–0.086 (≥ 0.05 gate) |
| Pose age 306 ms avg / 450 ms max (gate 150 ms) | sensor→analyzer 154 ms avg, analysis 70 ms, UI dispatch ~80 ms |
| Camera ran ~13 fps | AE target-FPS range never set; tsGap 78 ms |
| UI thread saturated | `gfxinfo`: 100 % janky, median 69 ms/frame |
| ADB gestures “unreliable” on guided screen (CAL-01 F5) | consequence of the saturated UI thread, not of ADB |
| `Applied: n/a` always | `GuidedCameraSource.lastApplied` was never assigned (CODE) |
| Thermal | after ~15 min camera use: Thermal Status 2→3 (SEVERE), detection time doubled |

## 2. Root causes

1. **Exposure gate measured the wrong region.** `FrameQuality` used the
   full frame (documented v1 limitation). A white board on a black bench
   is high-dynamic-range: the black bench is ≤ 5/255 at EV0 (`dark`),
   raising EV saturates the board (`saturated`). There was no passing
   exposure — exactly the CAL-01 `dark`↔`saturated` deadlock. The H6
   preregistration defines the fractions over the **ROI**; the gate
   values were right, the ROI was not.
2. **Calibration was bound to one pixel grid.** 1280×720 and 1920×1080
   are the same 16:9 crop of the same sensor (Camera2 crop contract:
   every stream is cropped from the same crop region, centred to its
   aspect, then scaled). The profile was rejected on size alone, so the
   faster grid had no metric pose.
3. **Pose age is dominated by the camera pipeline and the UI thread, not
   by detection.** Sensor timestamp = exposure start. Components on the
   SM-A536B: exposure+ISP floor ≈ 94–106 ms (s2a minimum), analyzer
   queueing, analysis, then a main thread busy recomposing the whole
   screen for every pose.
4. **AE dropped the frame rate.** Without `CONTROL_AE_TARGET_FPS_RANGE`
   the Samsung AE stretched exposure in room light (~13 fps).
5. **Whole-screen recomposition per pose.** One `state` object fed every
   section; every pose recomposed settings sliders, chips, remote
   section, result card.
6. **Detector ran three adaptive-threshold passes** (windows 3/13/23 px);
   the 3 px pass is useless for ~100 px markers.
7. **Calibration friction:** exposure presets (`Default/Low light/Bright`)
   are separate full settings sets with default focus AUTO, so switching
   an *exposure* preset silently drops the calibrated *focus* and with it
   the metric profile; the banner never said why.
8. **Safety gap found on the second phone:** the bundled profile is bound
   only to `camera_id "0"`. Every phone has a camera 0, and the schema has
   no device field. On the Xiaomi with manual focus 2.5 D at 16:9 the
   SM-A536B intrinsics would have been accepted as *measured/validated*.

## 3. Changes implemented (all uncommitted, Android scope)

| # | Change | Files | Semantics |
|---|---|---|---|
| C1 | Exposure fractions over the convex hull of detected marker corners; full-frame kept for diagnostics; falls back to full frame when the hull is degenerate (never loosens). ROI recorded in provenance: `algorithm_config` gains `"exposure_roi"` | `guidance/FrameQuality.kt`, `guidance/OpenCvPoseEstimator.kt`, `ui/guided/GuidanceViewModel.kt` | thresholds unchanged (`DARK_LEVEL` 5, `SATURATED_LEVEL` 250, 5 %); sharpness stays full-frame |
| C2 | Same-aspect **downscale** of a measured profile is compatible; K scaled exactly with the pixel-centre convention `c' = (c+0.5)s−0.5`, distortion unchanged; profile id names the derivation `measured:<id>@1280x720-scaled`; other aspect, upscale, zoom, focus, camera still fail closed | `guidance/CalibrationProfile.kt`, `camera/MeasuredProfileSource.kt` | CAL-01 binding extended by one exact transform |
| C3 | AE target FPS range pinned to the highest fixed range ≤ 30 fps in AUTO exposure (SM-A536B: `[30,30]`) | `camera/SettingsApplier.kt`, `camera/CameraSettings.kt`, `camera/CameraInventory.kt` | AE trades gain for exposure time |
| C4 | Single adaptive-threshold pass (23 px) | `guidance/OpenCvPoseEstimator.kt` | parity checked on device (§5) |
| C5 | Pose-independent screen sections receive an equal-while-unchanged copy of the state, so Compose skips them per pose | `ui/guided/GuidedCaptureScreen.kt` | UI only |
| C6 | Calibration explanation + one tap: banner states the violated binding (e.g. “focus is AUTO; profile needs manual 2.50 D”) and offers **Use calibrated optics** (manual focus at profile value, zoom 1×, compatible grid) | `guidance/CalibrationProfile.kt` (`mismatch`, single source of the rule), `camera/MeasuredProfileSource.kt`, `ui/guided/*`, `di/AppContainer.kt` | validity still decided by the profile binding |
| C7 | **Device binding of bundled profiles**: assets are named `measured-profile-<Build.MODEL>.json`; legacy `cal01-profile.json` only for `SM-A536B`; unknown device = no profile | `camera/MeasuredProfileSource.kt` | fail-closed hardening |
| C8 | Applied CaptureResult via public `Camera2Interop.Extender.setSessionCaptureCallback` (exposure, ISO, focus, AE/AF/AWB, EV, frame duration) | `camera/GuidedCameraSource.kt` | diagnostics/UI only |
| C9 | Diagnostics: sensor→arrival (min/avg/max), sensor→emit, exposure, frame duration, ROI vs full-frame fractions, fresh-pose ratio (≤ 150 ms), pose/RMS/profile | `guidance/FrameTiming.kt`, `camera/GuidedCameraSource.kt`, `ui/guided/GuidanceViewModel.kt` | log tag `AnastylosDiag`, throttled 1/60 |

Tests added/changed (UNIT): `FrameQualityTest` (+5), `SettingsApplierTest`
(new, 4), `CalibrationProfileTest` (+4, one expectation replaced: 1280×720
is now compatible; 1280×960, 3840×2160, 1281×720 stay incompatible),
`AssetMeasuredProfileSourceTest` (new, Robolectric, 3: other phone never
gets the profile).

## 4. Before/after on the SM-A536B (MEASURED, same scene, 1280×720)

All values from throttled `AnastylosDiag` summaries (60-frame windows).

| Metric | Before (15:38, APK `82624068`, thermal 2) | After C1–C5 (15:59, APK `67c9e2ef`, thermal 0) |
|---|---|---|
| Profile | `factory-0-1280x720-z0-uncalibrated` | `measured:b64d3dcb…@1280x720-scaled` |
| Tracking | DEGRADED, no metric pose | ACQUIRED, RMS 0.90–1.05 px (gate 1.2) |
| Quality | Rejected `[dark]` (full 0.086) | Accepted (marker-hull dark 0.035–0.041) |
| Detection | 47 ms | 8.5 ms |
| Analysis total | 70 ms | 18–20 ms |
| Sensor→arrival min / avg | 94 / 154 ms | 96 / 115 ms |
| UI pose age avg / max | 306 / 450 ms | 147–152 / 179–190 ms |
| UI frame median (`gfxinfo`) | 69 ms (100 % slow UI thread) | 38 ms |

Thermal caveat (MEASURED): the same build at Thermal Status 3 (SEVERE,
skin 42.4 °C) needed 20 ms detection and 231 ms average pose age; the
phone cooled to status 0 in ~2 min with the camera off. The 150 ms
freshness gate therefore depends on device temperature, not only code.

Floor (MEASURED, 16:13, no board in view): exposure 30 ms, frame
duration 33.3 ms, sensor→arrival 106–144 ms, UI age 135 ms avg with
55–59 of 60 poses ≤ 150 ms. The SM-A536B camera pipeline alone costs
~100 ms of the 150 ms budget at room light.

## 5. Detector parity (MEASURED)

Same static oblique scene before/after C4: markers {10,16,14,15,11,12}
both, RMS 1.02 px → 1.05 px, Quality Accepted both; detection 46 → 20 ms
(throttled), 8.5 ms cool.

## 6. Second phone: Xiaomi M2101K6G (MEASURED)

Install: MIUI blocks `adb install` until *Install via USB* is enabled and
input injection until *USB debugging (Security settings)*; both needed
operator action. Same APK hash on device as built.

Pipeline comparison, same build, uncalibrated (detection only), board in view:

| | SM-A536B (cool) | M2101K6G |
|---|---|---|
| Exposure / frame duration (CaptureResult, C8) | 30 ms / 33.3 ms | 10 ms / 33.3 ms |
| Sensor→arrival min / avg | 96 / 115 ms | 43 / 62 ms |
| UI pose age avg / max | 147–152 / 179–190 ms | 101–115 / 134–189 ms |

The Xiaomi camera pipeline is half as long: the 150 ms gate is
physically attainable there with margin. It has MANUAL_SENSOR, manual
focus (min 10 D), no OIS, no advertised distortion-correction mode.

### Xiaomi calibration attempt (XCAL), CAL-01 method unchanged

Operator captured hand-held sampling sessions (manual focus 2.4645 D ≈
40 cm, still 1920×1080 after a first run at the 4000×3000 “Max” default,
which is a different 4:3 crop and was discarded). Pull: 61 originals,
SHA-256 identical device = local = Room DB. Classified with the fit
script’s own detectors: 18 ChArUco, 23 A3, 17 neither (floor/transit).
A3 split deterministically (sorted, even index → fit 12, odd → validation 11).

| | CAL-01 SM-A536B | XCAL run 1 |
|---|---|---|
| fit / held-out views | 24 / 11 | 20 / 10 |
| fit RMS | 0.49 px | 1.42 px |
| held-out RMS | 0.58 px | 1.93 px |
| cross-board A3 pooled (gate 1.5) | 0.61 px | 2.07 px |
| scale anchor | ok | ok |
| validator | accept | **needs_review** (`HELD_OUT_RMS_OVER_GATE`, `MAX_VIEW_RMS_OVER_GATE`) |

Verdict: **not validated, not bundled.** Per-view diagnosis (PnP with the
fitted K, diagnostic only): ChArUco views 0.34–0.62 px at 27–40 cm; A3
views 0.74–3.72 px, taken at 40–62 cm with focus fixed at 40 cm,
hand-held at 1/33 s. Root cause is capture geometry (defocus + motion
blur), not the camera. No views were removed to pass the gate.

Run 2 (A3 re-shot by the operator as session `xcal-a3v2`, 13 originals,
hash-identical; ChArUco set unchanged; all run-1 A3 views replaced, not
filtered): 11 A3 usable, split 6 fit / 5 validation.

| | XCAL run 2 |
|---|---|
| fit / held-out views | 16 / 8 |
| fit / held-out RMS | 1.11 / 0.84 px |
| cross-board A3 pooled (gate 1.5) | **1.78 px** |
| validator | **needs_review** (`CALIBRATION_VIEW_COUNT`, `MAX_VIEW_RMS_OVER_GATE`, `HELD_OUT_RMS_OVER_GATE`) |

Still not validated. A3 views were again 44–49 cm away (the whole A3
board does not fit the Xiaomi frame at 40 cm); per-view RMS tracks
sharpness (Laplacian 101 → 2.76 px, 226 → 0.86 px), i.e. defocus at a
focus fixed for 40 cm. Attempts were stopped here on purpose: a third
hand-held try with the same geometry would be gate-shopping.

Decision needed for a valid Xiaomi profile (not taken here): fix the
Xiaomi working distance/focus to where the A3 board actually fits
(~45–50 cm, ≈ 2.0–2.2 D), capture both boards at that focus from a
support/tripod, ≥ 15 A3 views. Evidence: `claude-review-evidence/xcal-run1/`,
`xcal-run2/` (profiles stay `metric_validity: unknown`).

## 7. Calibration invalidation inventory (CODE)

| Situation | Before | After | Correct? |
|---|---|---|---|
| Different physical camera | uncalibrated | uncalibrated | yes — different optics |
| Different phone, same camera id "0" | **SM-A536B profile accepted if focus/grid matched** | uncalibrated (C7) | fixed safety bug |
| Overhead ↔ freehand ↔ sideview | no effect (pose is not part of the binding) | same | yes — CAL-01 already shared one stack |
| Analysis 1920×1080 ↔ 1280×720 | 1280 uncalibrated | 1280 = exact scaled profile | yes — same crop (Camera2 contract); live RMS 0.90–1.05 px at 1280 vs gate 1.2 |
| Other aspect (960×720, 640×480) | uncalibrated | uncalibrated | yes — different crop |
| Zoom ≠ 1× | uncalibrated | uncalibrated | yes |
| Focus AUTO or ≠ 2.5 ± 0.05 D | uncalibrated, no reason shown | uncalibrated + reason + one-tap fix (C6) | yes — focus breathing measured in CAL-01 |
| Switching exposure preset Default/Low light/Bright | **silently loads another full settings set, focus AUTO → uncalibrated** | same, but explained + one tap | design smell, see §9 |
| Cold start | stored size restored (GUIDE-01 WIP fix, confirmed) | same | yes |

## 8. Remaining blockers

1. **Positive guided auto-shutter not yet demonstrated physically.** All
   software gates were observed passing individually on the SM-A536B
   (ACQUIRED, Quality Accepted, ≥ 92 % of poses ≤ 150 ms when cool), but
   a target-aligned dwell needs the phone fixed over the A3 board at the
   target pose; the board was in use for the Xiaomi calibration.
2. Pose freshness on the SM-A536B sits on the 150 ms gate (camera
   pipeline ~100 ms at room light, thermal throttling pushes it over).
   The gate is frozen (`guidance-hypothesis-1`); it was not changed.
3. Motion gate (0.7 px/100 ms median) is effectively tripod-only;
   freehand dwell will rarely pass. Frozen; not changed.
4. Sharpness reference is a single stored value measured at one analysis
   size; comparing it across sizes is not like-for-like (UNVERIFIED impact).
5. Xiaomi needs a re-shot A3 set (§6) before it can carry metric evidence.
6. Unit tests in the default build dir are blocked by orphaned JVMs of
   the crashed Gradle daemon 17680 holding `classes.jar`; tests were run
   with an init script that redirects `buildDir` to the scratchpad.

## 9. Architecture challenge

- **Necessary and present — do not rebuild:** fail-closed profile
  binding, ShutterGate/Dwell/reducers, provenance hashing, receiver
  path. All behaved correctly under every failure seen today.
- **Simplify without losing correctness:** exposure presets should be
  exposure-only overlays on one optics/geometry set per camera; today
  they duplicate focus/zoom/size and are the main way operators lose
  calibration. Default still size should follow the calibrated 16:9
  grid instead of 4:3 “Max”.
- **Useful but can wait:** remote/Core section, multi-board picker,
  per-frame CaptureResult association.
- **Overengineering that cost time:** the guided screen is one long
  telemetry form; the operator path (target → arm → hold) is below
  settings. Not redesigned here; C5 removed its performance cost.

## 10. Test and hardware evidence

- Unit (UNIT): `gradlew :app:testDebugUnitTest --offline --init-script
  <scratchpad>/altbuild.gradle` → **454/454 PASS** (0 failures, 0 skips).
- Device installs hash-verified each time (SM-A536B and M2101K6G).
- Lint: `:app:lintDebug` → 0 errors.
- Device logs/screenshots/scripts: `claude-review-evidence/` (`SHA256SUMS`).
- Final APK `631e3b87…` (built from the final source tree) installed and
  hash-verified on both phones.

## 11. Files modified

See §3. No thresholds, contracts, receiver, or evidence files changed;
the CAL-01 profile asset bytes are unchanged. No commit, push or merge.

## 12. Recommended next engineering slice

GUIDE-02: mount the SM-A536B overhead (CAL-01 clamp, ~40 cm), select
“A3 top-down center”, arm, slide the **board** (not the phone) into the
target pose following the cues, let dwell fire, then sync and verify the
receiver package hash. Everything up to dwell has been seen passing
today; this is a ~10 minute operator run. In parallel: contract review
of the 150 ms freshness gate against measured per-device pipeline floors.

## 13. Addendum 2026-10-31 — XCAL run 3 (tripod set + hand-held tilts)

Input: operator tripod set `xiaomi-round2` (114 originals, sessions 2a–2d,
camera 0 “Back 6.0 mm”, manual focus 40 cm per operator) plus the 18
hand-held ChArUco views of run 1 (same optics). A previous Codex fit of the
tripod set alone reached held-out 1.06 px (gate 0.8) with `camera_id`
and focus left `unknown`.

Diagnosis (per-session ChArUco-only fit, diagnostic): tripod session 2b
alone gives fx = 10 877 (near-fronto-parallel views make focal length
unobservable), 2c fx = 1481, pooled 1640 — ill-conditioned. Tilt
diversity, not image quality, was missing.

Run 3, subsampling fixed before fitting and not error-based (validator
allows 25–40 views): all 18 hand-held tilted ChArUco views, every 5th
tripod ChArUco view (10), every 3rd A3 fit view (9); the 26 A3
validation views (odd sorted index) untouched. `--camera-id 0
--focus-diopters 2.5`.

| | value | gate |
|---|---|---|
| fit / held-out views | 25 / 12 | 25–40 total |
| fit RMS | 0.70 px | — |
| held-out RMS | **0.78 px** | ≤ 0.8 ✅ |
| cross-board A3 pooled (26 unseen) | **1.42 px** | ≤ 1.5 ✅ |
| scale anchor | ok | ✅ |
| max single view | **1.79 px** (one A3 view, 488 mm, lowest sharpness) | ≤ 1.5 ❌ |
| K | fx = fy = 1422.8, cx 969.5, cy 535.3 | consistent with run 1 (1418) |
| validator | **needs_review** (`MAX_VIEW_RMS_OVER_GATE`) | |

Verdict: **not validated, not bundled.** Removing the single over-gate view
would pass, but that is error-based selection after seeing the result and
is not done. Clean completion: one pre-registered rule set before a new
fit (e.g. A3 views only at ≤ 470 mm and a sharpness floor fixed from the
ChArUco set), or ~10 fresh tilted A3 views at ~45 cm. Evidence:
`claude-review-evidence/xcal-run3/` (+ `persession.py`). The hackathon
demo does not depend on the Xiaomi (runbook uses the SM-A536B).

## 14. Addendum 2026-10-31 — XCAL run 4: Xiaomi profile validated (owner rule)

Owner decision (valentin-englert, 2026-10-31): the Xiaomi must carry a
profile; no further captures; a recorded rule change is acceptable. Rule
written **before** the refit: `claude-review-evidence/xcal-run4-METHOD.md`
(single-pass removal of fit-side views over the 1.5 px per-view gate under
the run-3 profile → exactly one A3 view; one refit; gates unchanged; no run 5).

| | run 4 | gate |
|---|---|---|
| fit / held-out views | 24 / 12 | 25–40 total ✅ |
| fit / held-out RMS | 0.71 / **0.58 px** | ≤ 0.8 ✅ |
| cross-board A3, 26 untouched views | **1.42 px** | ≤ 1.5 ✅ |
| scale anchor 481.0 vs 480.98 mm | ok | ✅ |
| validator | **accept**, `metric_validity: validated` | |

Bundled as `app/src/main/assets/measured-profile-M2101K6G.json`
(limitation names the rule and its weakness: error-based, chosen after
run 3). Tests: `AssetMeasuredProfileSourceTest` now checks that each phone
gets only its own profile → Android **455/455 PASS**. APK `385894bc…`
installed and hash-verified on the Xiaomi; live log: `guided intrinsics
measured profile=measured:e92c8ea1…`, `status=VALID`, RMS 1.21 px at
1920×1080 (gate 1.8), 52/60 poses ≤ 150 ms, pose age avg 117 ms.
Evidence: `claude-review-evidence/xcal-run4/` (SHA256SUMS).

## 15. Addendum 2026-11-01 — Remote view request end to end on the Xiaomi

Device Xiaomi `M2101K6G`, profile `measured:e92c8ea1-36a8-449f-8df9-50f0f3481548`,
tripod over `surface_a3`. Workstation side: a scratch script in place of
`tools/session-ui/captures.py`, using the TL client
`integration/northstar` @ `7db24380` with SPKI pin `6c5f72a4…`. Grant read
read-only from `control.db` (R11 open).

Final run, APK `78630d4c8e4cd3d9dcfffb450e9f43201198043205f038daf115ef0a1f7fb744`
(device `base.apk` hash equal), session `5bc9fe29-cad5-4a9c-aafb-54aed2548f41`:

| Step | Evidence |
|---|---|
| Capabilities after attach | stored: cameras `0,1`, `[1920,1080]`, calibration `e92c8ea1…`, `pose`, `guidance-hypothesis-1`, protocol 1 |
| First arm | one tap, no revision conflict; grant `865d8c10…` claimed |
| Request | `5cb2d223-0ce5-4ee6-8c77-56830e1daca1`, label “Back shell, inner side up — hands off”, `board_id` `5a3a3a3a-0000-4000-8000-00000000a3a3` mapped once to `surface_a3`, `T_B_C.to` `board:surface_a3` |
| Gates | NAVIGATING → ALIGNING → HOLD_STILL, dwell 5/5, then one shutter; no gate changed |
| Local | `CAPTURED_LOCAL`, image `0d1e5ad3-f894-4241-9adb-369f6b05d1a5` |
| Core | events `armed`, `accepted`, `captured_local`; attempt `e00fb4e0…` `asset_bound` |
| Receipt | `package_only` (card: “Sent — image only, not spatially qualified”) |

The request as recorded is the test vector
`app/src/test/resources/remote-view-request-back-shell.json`; the only change is
`status` restored to `QUEUED`.

Defects found only against the live receiver (all fixed, each with a test on
the bytes the phone sends):

1. Wire bodies dropped fields at their default (`resolutions_px`,
   `expected_session_revision`) and nulls (`reason`). G0 bodies carry every
   field, nulls explicit. Fixtures only checked replies, which hid it.
2. First arm sent revision 0: the phone now fetches the session first.
3. Delivery uploaded on the legacy route before binding, so no receipt was
   ever joined. Now G0 order: bind → guided upload (`X-Guided-*`, guided token)
   → sidecar.
4. Guided captures went to a local session with its own id; Core accepts a
   guided package only when its manifest names the guided session. The local
   session is now the guided session's twin.
5. The sidecar used the newest pose instead of the pose at still time.

Gate still blocking `qualified` (not loosened): the sidecar is skipped because
`sample_age_ms` exceeds the 150 ms exposure-association gate. It is measured
from the still callback (~0.9 s after the shutter request), because the file
save path (`OnImageSavedCallback`) gives no sensor timestamp. Missing
precondition: a still sensor timestamp (`OnImageCapturedCallback` /
`ImageInfo.timestamp`), a capture-path change awaiting approval.

Verification: `gradlew testDebugUnitTest lintDebug assembleDebug` with the test
JVM pinned to JDK 21: 482 tests, 0 failures; lint passed.

### 15.1 Still sensor timestamp (owner-approved capture-path change)

`takeGuidedStill` now captures in memory (`OnImageCapturedCallback`): the HAL
JPEG bytes unchanged, plus `ImageInfo.timestamp` on the analysis sensor clock.
The sidecar pairs the still with the analysis frame exposed last at or before
it (`guidance/StillPose.kt`, never a later frame); without a still timestamp it
falls back to the callback age, which the gate blocks.

Run on APK `2b813318c0bcdba878066fa8349c8d8c52df4a18fccb5ab14cd21be234a6e5e0`
(device hash equal), session `4a486784-87d8-4735-a213-3a8b5f023be2`, request
`428ee79c…`, image `c742ea5c…`:

- `sample_age_ms` 13.3 (was ~900 from the callback); the 150 ms gate passes.
- Sidecar stored (revision 1, `2f643588…`); receipt re-versioned with it.
- JPEG keeps its EXIF orientation (6) in the in-memory path.
- Receipt stays `package_only`. Not qualified because of two blockers outside
  the capture path:
  1. The receiver's spatial-validator seam is not delivered
     (`unavailable by default: package_only, never qualified`).
  2. The sidecar declares calibration `nominal_only` with the factory
     descriptor even when the measured Xiaomi profile is active, and
     `validated` needs a measured board instance (A3 print scale unmeasured).
     Declaring the measured profile id and sha is a phone change that waits
     for a TL decision.

Verification: 485 tests, 0 failures, JDK 21 test JVM; lint passed.

### 15.2 Sidecar cites the measured profile (step a)

The sidecar now cites the measured profile when the pose was solved with its
unscaled intrinsics: `profile_id`, `profile_sha256` (sha256 of the exact
bundled asset bytes), the profile's `sensor_mode` and
`calibration_pixel_frame_id`, and `calibrated = true` (pose `valid`, basis
`geometric_measurement`). A scaled or factory solve stays nominal.
`metric_validity` stays `nominal_only`: the A3 print scale is unmeasured.
Fixed on the way: the sidecar used the consent spelling `camera:0` as
`camera_id`, which made `T_board_camera.from` `camera:camera:0`; it now
carries the bare Camera2 id.

Workstation staging (done for this run): the profile bytes at
`<receiver-data>/spatial/profiles/aeac1c7c130fb017e7ee42f94c907043c530ae452c3f55b1c2f82ac7f97537eb.json`.

Run on APK `7c12e352a845c86c0fa4c4a0af3a893662835985c40a963bb4ce00e08cf76f4d`
(device hash equal), session `6d27c352…`, request `e09f4268…`: receipt
`package_only`. The validator, run with the receiver's own context builder:
verdict `needs_review`, no rejects; profile bound, pose `valid`,
`sample_age_ms` 13.3.

Still between this and `qualified`:

| Item | Kind | Owner |
|---|---|---|
| `CALIBRATION_NOT_VALIDATED` | A3 board instance: measured 100 mm bars, `anastylos-board-instance@1` record staged, its id in the sidecar | owner measures; phone + workstation wire the id |
| `UNKNOWN_SOLVE_SOURCE` | `timestamp_interpolated` needs two bracketing pose samples uploaded as assets; the receiver has no route for them | contract request (TL) |
| `NULL_MAX_RESIDUAL`, `NULL_UNCERTAINTY` | the estimator reports RMS only; max residual and pose uncertainty are null | phone (estimator) |
| `QUALITY_NOT_ACCEPTED` | quality is `rejected` here (motion verdict; the hackathon gate only warns on motion) and is never `accepted` without validated calibration | follows the rows above; motion needs a still tripod |
| `LAYOUT_CHECK_SKIPPED`, `EPOCH_CHECK_SKIPPED` | no layout / epochs staged on the workstation | workstation |

A3 measurement (owner): print check per `calibration/README.md` step 5. With a
steel ruler or caliper, read the horizontal and the vertical 100 mm line, end
tick to end tick, to 0.1 mm. Record both values, the instrument, the
uncertainty (ruler ±0.5 mm, caliper ±0.05 mm), who measured, the time (UTC)
and whether the sheet lies flat. The validator rejects more than 0.5 % per axis
(below 99.5 or above 100.5 mm): reprint at 100 % then.

### 15.3 Measured A3 print (board instance)

Owner measurement 2026-11-01 (reported 2026-11-02 00:00 local): both 100 mm
check lines on the `surface_a3` print read about 100.15 mm with a Mahr caliper
(+0.15 %, limit 0.5 %). Recorded as `anastylos-board-instance@1`
`50462f3b-d954-4e96-8d3f-ae877e144f25` in
`app/src/main/assets/board-instance-surface_a3.json` (uncertainty 0.05 mm,
flatness `unknown` because it was not stated); `validate_board_instance`:
accept. Staged on the workstation at
`<receiver-data>/spatial/boards/b6f7da9a37124483cc72f7b5d7e7daa2b0a394f0730024f90786475225e1f5af.json`.
Note for review: the earlier CAL-01 diagonal check (481.0 vs 480.98 mm, ±0.2,
Hultafors Talmeter) implies about +0.004 %, the bars +0.15 %; both are inside
the limit, but they disagree by more than their stated uncertainties.

The phone cites the record (id + sha256 of its bytes) only together with the
measured profile and only when the record's layout hash equals the phone's
layout; then `metric_validity` is `validated`.

The card no longer says "On target — hold still" while an image gate blocks:
it names the blocking reason (sharpness, dark, saturated). Found in a run where
sharpness blocked the shutter (gate unchanged); the next run captured.

Run on APK `1d9d2596bf97dd11bc4c8f5da716c8ba7284ed1bfe88386613fb5ac00b9aa6d0`
(device hash equal), session `c33c73e5…`, request `49ff251e…`: receipt
`package_only`; validator `needs_review`, no rejects,
`CALIBRATION_NOT_VALIDATED` gone. Remaining: `UNKNOWN_SOLVE_SOURCE` (contract
route for pose-sample assets), `NULL_MAX_RESIDUAL` / `NULL_UNCERTAINTY`
(estimator), `QUALITY_NOT_ACCEPTED` (quality `rejected` in this sidecar),
layout and epochs not staged. `measured_print_scale` in
`calibration/calibration-config.json` is not updated (calibration module,
generated file).

Verification: 488 tests, 0 failures, JDK 21 test JVM; lint passed.

### 15.4 Pose metrics: max residual and uncertainty

The estimator now hands on the max reprojection residual it already gated on,
and computes a 1 sigma uncertainty for gated poses
(`guidance/PoseUncertainty.kt`): Gauss-Newton covariance `s²(JᵀJ)⁻¹` at the
solution, residual variance over `2N−6`, central-difference Jacobian through
`PoseMath.projectPoints`, propagated to the camera centre in board mm (the
frame the sidecar reports) and to the rotation angle in degrees. The sidecar
writes `max_residual_px`, `position_uncertainty_mm`, `angle_uncertainty_deg`
and the method string; `none` and nulls when not computable.

Run on APK `78c581a4253f046745820041ace87788461619912ed087051345420579783556`
(device hash equal), session `42cc4b1b…`, request `71ac5944…`: RMS 0.80 px,
max residual 2.04 px, uncertainty (0.47, 0.57, 0.12) mm and 0.09°. Validator:
`needs_review`, no rejects, the only review left is `UNKNOWN_SOLVE_SOURCE`.

Quality is `rejected` on motion alone: p90 1.5001 px against 1.5 px (pose,
focus and exposure pass). The phone applies the motion gate unscaled at the
1920 px analysis width, while the RMS and max-residual gates are scaled from
`REF_WIDTH_PX` 1280. Whether the frozen motion thresholds are also defined at
1280 px is a TL question; nothing changed here.

Verification: 490 tests, 0 failures, JDK 21 test JVM; lint passed.

### 15.5 `guidance-hypothesis-2`: the phone takes the photo (TL decision on R12)

For an adopted workstation request the shutter blocks only on tracking
(registration acquired, fresh pose) and the 0.5 s stillness dwell. Target
offset, sharpness, exposure and motion no longer block; they are still
measured against the unchanged H1 thresholds and reported in the sidecar, so
such a photo stays `package_only`. Local arming stays H1. Motion thresholds are
now scaled to the analysis width like the RMS gate (live gate and sidecar).
Card: "Photo sent", plus "a bit blurry — retake if the drawing looks wrong"
when an image gate failed at the shutter. Capabilities list H1 and H2.

Wide workstation tolerances (`integration/northstar` @ `d4eec96b`: ±100/±100/
±150 mm, 20°) are adopted as sent (test
`workstationRequestShootsWhenTrackedAndStillEvenOffTarget`, which also shows a
shot 250 mm off target).

Run on APK `f7a7d42bbabe057c51122624a94a335cbf92d0883f71f202d63a698fb4f89d8c`
(device hash equal), session `b5751f5d…`, request `8161005f…`: posted
23:51:39Z, adopted 23:51:41.3Z (2 s change poll), captured 23:51:42.9Z,
receipt `package_only` 23:51:42Z, re-versioned with the sidecar 23:51:43Z.
About 1.7 s from adoption to the photo at the workstation, about 4 s from the
post. The first-arm revision fix is in since `3ad13126` (one tap, verified).

Verification: 491 tests, 0 failures, JDK 21 test JVM; lint passed.
