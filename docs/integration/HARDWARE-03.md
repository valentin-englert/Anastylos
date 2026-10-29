# HARDWARE-03 — Wireless Physical Device Validation (SM-A536B)

Date: 2026-10-29. Worktree: `C:\Users\HP\Documents\Anastylos-integration-spatial`,
branch `integration/spatial-capture-v1`. Baseline `2c8b9232` + uncommitted fixes below.

## 1. Wireless ADB

- Physical Samsung SM-A536B at `192.168.0.42:43947` (wireless debugging), Android 15, SDK 35.
- Later additionally USB-attached as `R5CTA06SFRB`; the phone left the overhead fixture
  (no board in view) for the basics-first phase. All commands addressed `-s 192.168.0.42:43947`.
- `adb shell`, `screencap`, `logcat`, `run-as`, `pm`, `input keyevent` work.
  `input tap`/`swipe` are silently ignored on this device (3 attempts, verified 1080x2400
  coordinates) — UI taps requiring touch fell back to the operator. scrcpy was not used.
- Screen timeout capped at 10 min by the device; operator kept it awake.

## 2. Device & app identity

- Installed APK on device SHA-256 `91b44a29f5a0f4b385346d0894fbff809943b3266960d04a85633f5130be35cb`,
  byte-identical to the local guard-fixed debug build. Package `org.anastylos.capture`.

## 3. Camera capability matrix (dumpsys, cam0 Back)

- LIMITED / REALTIME timestamp source, 4624x3468 + OIS + manual focus.
- ANASTYLOS streams: analysis 960x720, preview 960x720, still capture 4624x3468.
- SENSOR_INFO_TIMESTAMP_SOURCE = REALTIME ⇒ sensor timestamps live in the
  `elapsedRealtimeNanos` (CLOCK_BOOTTIME) domain — decisive for the clock fix (§11).

## 4. Reference-profile comparison

- Operator reference (Samsung Pro Video): UHD/4K, 30 fps, Wide, ISO 400, 1/50 s,
  WB 5600 K, manual focus (MF UI value 0.6), EV −0.4, flash off.
- CameraX currently uses auto exposure/focus/WB; manual ISO/exposure/WB are NOT
  reproduced. Samsung MF 0.6 is a UI-scale value, not meters/diopters. No silent
  change to the experimental protocol was made. Requested-vs-actual: open gate.

## 5. Marker detection — PASS (after fix)

- Live detection of all 7 A3 Surface markers on the physical phone:
  UI `Markers 13, 15, 16, 12, 10, 14, 11` (IDs 10–16, DICT_5X5_100).
- Ground truth: a dumped Y-plane PGM decoded by the desktop cv2 oracle shows the
  same 7 markers — the bytes were always good; the defect was Java-side parsing.
- Analyzer throughput ≈ 33 fps at 960x720 (enter/exit ~30 ms apart, temporary probe).

## 6. Registration & pose — fail-closed PASS

- Tracking moved `UNREGISTERED` → `BAD_CALIBRATION` with `Bad calibration — check profile`:
  correspondences assemble, but `factory-0-uncalibrated` intrinsics fail sanity gating,
  so no pose is solved and RMS stays unset. Metric evidence correctly disabled.
- Pose age is small-positive (182–358 ms) after the clock fix; previously −13,815,021 ms.

## 7. Image quality — P1 open, threshold NOT weakened

- `Quality Rejected(reasons=[dark])` persists: dark ratio ≈ 0.224 on the dim scene
  (yMean 107). Plausible for the lighting; no threshold was touched. Needs a lit-scene
  retest and a threshold review against the frozen acceptance profile.

## 8. Manual/sampling capture (Test C) — PASS

- Session `HW03` (`a320581c-…`): 7 stored, 0 missed at 5 s interval on the physical camera.
- File persistence + SHA-256 verified (§9). Note: Slice A has no single-shot button;
  capture runs through Start sampling → Stop.

## 9. Receiver round-trip (Test D) — PASS (first live round-trip)

- Pairing intact: workstation `db75f1ee71e94bee8859a0fdcd857b0a` at
  `https://192.168.0.110:8443`. App: `3 synced, 0 rejected, 0 failed`.
- Receiver holds 3 packages timestamped 23:37:22–23 (matches the app count exactly).
- Byte integrity: device original `48a89e3e…0c8` == receiver asset
  `assets/09ed0ab0-…/frame-…-0003-cd059641.jpg` (`48A89E3E…0C8`). Manifest links
  session HW03 → observation `09ed0ab0-…` → `capture_source: camera`.

## 10. Guided capture (Test E) — PARTIAL

- Guidance states, BAD_CALIBRATION banner, dwell-idle, DISARMED default all render honestly.
- Dwell auto-shutter PENDING: requires a calibrated pose, unavailable by design.
- Untracked/disarmed camera never auto-captured (sampling ran only on explicit Start).
- Tracking-loss recovery PENDING: phone left the fixture before the cover-the-lens test.

## 11. Reproduced bugs & fixes (all in this worktree, 8 files, +89/−10)

1. P0 clock domain — `System.nanoTime()` (excludes suspend) vs sensor BOOTTIME:
   `AppContainer.kt` nanoClock → `SystemClock.elapsedRealtimeNanos()`. Pose age now positive.
2. P0 corner-quad NPE — 4.14 Android binding returns quads as **1x4** CV_32FC2, code
   assumed 4x1: `quad.get(1..3, 0)` → null → NPE on every frame, swallowed as LOST.
   Fix: single bulk `get(0, 0, FloatArray(8))` + null/bounds guards + pure
   `quadPointsRowMajor()` reshape. Proven by on-device shape probe
   (`ids=7x1 t=4 nCorners=7 q0=1x4 t=13`).
3. P0 byte-count guard — bulk `Mat.get` returns BYTES (32), not elements (8):
   guard is now `bytesCopied != quad.size * Float.SIZE_BYTES`.
4. P0/P1 hardening — `Dwell` rejects negative age (`in 0..max`, matches ShutterGate);
   `markersSeen` plumbed to the UI so "detector saw nothing" differs from registration failure.
- Regression tests: `quadReshapePreservesCornerOrder`, `quadReshapeRejectsShortBuffer`
  (+ prior-turn Dwell/ViewModel tests). All temporary logcat/PGM probes removed;
  no `TEMP-HW03`/`AnalyzerDebug` string remains in `capture-companion/android`.

## 12. Remaining blockers

- Calibration: print-scale measurements + qualified intrinsics profile (gates metric pose,
  dwell auto-shutter, RMS, Test E completion).
- `dark` quality verdict under dim light (retest lit; review, don't weaken).
- Manual camera controls (ISO/exposure/WB/focus) not yet in CameraX path.
- Touch injection via ADB blocked; operator taps needed for UI flows.
- Tracking-loss/reappear behavior untested (needs board back in fixture).

## 13. Validation commands (representative)

- `adb -s 192.168.0.42:43947 shell|logcat|screencap|run-as|pm path|uiautomator`
- `gradlew :app:testDebugUnitTest :app:assembleDebug --offline` (JDK 21.0.12.1+1)
- `py tools/compliance/check.py`; receiver + calibration `unittest discover -s tests`
- `Get-FileHash … -Algorithm SHA256` vs on-device `sha256sum` (install verified twice)

## 14. Before/after evidence

- `hw03-evidence/` (untracked): guided UNREGISTERED/dark baseline, BAD_CALIBRATION +
  7 markers after fix, session/upload/3-synced flow, UI dumps. Receiver packages:
  `%LOCALAPPDATA%\Anastylos\receiver\packages\{453fe776-…,54ce1d8e-…,d0de6c54-…}`.

## 15. Gate verdicts

| Gate | Result |
|---|---|
| Test A live camera / lifecycle | PASS (preview+analysis live, nav without crash, ~33 fps) |
| Test B ArUco tracking (7/7 IDs) | PASS |
| Pose-age clock | PASS (positive, fresh) |
| Fail-closed uncalibrated | PASS (BAD_CALIBRATION, no metric claims) |
| Test C capture + SHA | PASS (7 stored/0 missed, hash-verified) |
| Test D receiver round-trip | PASS (3/0/0, byte-identical asset) |
| Test E guided auto-shutter | PENDING (needs calibration) |
| Tracking-loss recovery | PENDING (board off fixture) |
| Quality `dark` | FAIL-open P1 (honest reject; needs lit retest + review) |
| Manual camera controls | BLOCKED (not implemented) |

Regression suites: Android 384/384 ✓ · receiver 185 OK (3 skips) ✓ ·
compliance 0 errors ✓ · calibration suite env-blocked (`No module named 'cv2'` on
system python — pre-existing, unrelated to this Android-only change).

Commit: HARDWARE-03 fixes on `integration/spatial-capture-v1` (see git log).
Rollback: `git revert` the HARDWARE-03 commit; reinstall prior APK; receiver data untouched.
Main, E3-B, other worktrees, Assembly Memory: not modified.
