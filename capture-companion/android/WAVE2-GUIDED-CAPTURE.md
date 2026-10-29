# Wave 2 — Guided Capture on Android

Date: 2026-10-29. Branch: `feat/wave2-guided-capture`.
Base: `5ef14ad8` (Wave-1.5 integration HEAD).
Final commit: `deb6c5c0`.
G0 contract: `ec313f7f…d44663`, recomputed MATCH (13/13 files).

Verdict: **code-complete, emulator-verified. No physical-phone run yet.**

The APK builds reproducibly, installs, launches, and runs the full
guided pipeline on an emulator: live preview, frame analysis, native
OpenCV ArUco detection, quality measurement, manual capture with
verified SHA identity, and shutter safety (armed 20 s without
tracking captures nothing). Positive board detection, guidance
arrows, dwell auto-shutter, and Core live round-trip need real
hardware or a live receiver. They stay explicitly open below.

## 1. Toolchain (versions and paths)

| Component | Version | Path / source |
|---|---|---|
| JDK | Temurin 21.0.12.1+1 | `C:\Users\HP\jdk\jdk-21.0.12.1+1` |
| Android SDK | cmdline-tools, platform-tools 37.0.1 | `C:/Users/HP/Android/android-sdk` (reused) |
| Platforms / build-tools | android-36, android-37.0 / 35–37 | SDK `platforms/`, `build-tools/` |
| Gradle | 9.8.2 | project wrapper |
| AGP | 9.4.1 | `gradle/libs.versions.toml` |
| Kotlin | 2.3.21 (project) | version catalog |
| CameraX | 1.6.2 (core/camera2/lifecycle/view) | version catalog |
| OpenCV | `org.opencv:opencv:4.14.0` AAR | Maven Central via Gradle |
| Emulator | 37.2.12 | installed this phase via sdkmanager |
| System image | android-36 google_apis x86_64 rev 7 | installed this phase via sdkmanager |

SDK path is per-clone in `capture-companion/android/local.properties`
(gitignored). No global PATH or machine settings changed.

## 2. Downloads, hashes, licenses

- `emulator` 37.2.12 + `system-images;android-36;google_apis;x86_64`
  rev 7, via sdkmanager, under `android-sdk-license` (accepted with
  `--licenses`). Installed into the existing workstation SDK only.
- OpenCV AAR `org.opencv:opencv:4.14.0`, Apache-2.0, 123380341 bytes,
  SHA-256 `6D11B40F6A54113DAFE8540B1237B637193CB21E83DEB11BC53D6757D35D494D`
  (Gradle cache copy).
- Temurin 21.0.12.1+1 from Adoptium (GPL+CE). Gradle 9.8.2 (ASL).
- No SDK/build artifacts in Git. Compliance holds at 0 errors
  (1 warning, 10 open REVIEW items, all pre-existing or already
  inventoried, including the Android Maven closure and ML Kit terms).

## 3. Commands and exit codes

All from `capture-companion/android`, `JAVA_HOME` = Temurin 21,
`--offline --no-daemon` (daemon crashes observed; see §11):

- `:app:testDebugUnitTest` → exit 0, 380/380, 0 skipped.
- `:app:lintDebug` → exit 0, 0 errors, 7 warnings (pre-existing:
  ExifInterface ×2, OldTargetApi, newer AGP/Kotlin, mipmap-anydpi).
- `:app:assembleDebug` → exit 0.
- Receiver `python -m unittest discover -s tests` → exit 0,
  185 tests OK (3 pre-existing OpenCV-missing skips).
- `tools/compliance/check.py` → exit 0 (0 errors).
- `accept.py verify-contract` → exit 0, SHA matches G0.

## 4. APK

- Path: `capture-companion/android/app/build/outputs/apk/debug/app-debug.apk`
- SHA-256: `68298E6799E8603DC36356B7D5E3B983CC5EA8540062217799C7D3BED14256AC`
- Size: 193877893 bytes (184.9 MB).
- Native libs ship for arm64-v8a, armeabi-v7a, x86, x86_64.
  `libopencv_java4.so` is 24.7/16.1/45.3/60.5 MB per ABI
  (uncompressed). Per-ABI splits are deferred follow-up work.

Reproduce: install Temurin 21 + SDK (platforms android-36/37.0,
build-tools 37.0.0), point `local.properties` `sdk.dir` at it, run
the three Gradle commands above with `--offline` after first fetch.

## 5. Implementation commits (on `feat/wave2-guided-capture`)

- `3dbde8f5` — OpenCV ArUco/PnP tracking, guided camera source,
  shutter gate (Phase C).
- `e669bb96` — guided capture screen, overlay mapping, ViewModel
  wiring (Phase D).
- `cd95c01b` — guided control transport, session controller,
  delivery, sidecar, Room v1→v2, tripwire retirement (Phase E).
- `deb6c5c0` — emulator-driven fixes: selective camera unbind,
  OpenCV init order, button-row overflow (Phase F).

## 6. CameraX analysis pipeline

`GuidedCameraSource` binds Preview + ImageAnalysis (YUV_420_888,
KEEP_ONLY_LATEST, single bounded worker) + ImageCapture coherently
on the guided screen lifecycle. Every ImageProxy closes in
`finally`; analyzer exceptions drop the frame, never the preview.
Preview and analysis share one resolution so the overlay maps
analytically (FIT_CENTER, no restricted transform APIs).

Device proof (emulator): guided bind creates P+A+C streams,
frames flow (pose age 8–61 ms across visits), preview renders
the virtual scene. Camera stays open across the session→guided
transition after the §11 fix.

## 7. ArUco / PnP

`OpenCvPoseEstimator` over `org.opencv:opencv:4.14.0`:
`ArucoDetector` (DICT_5X5_100 registration, DICT_6X6_250 ChArUco)
plus `solvePnP` (SQPNP) against canonical board geometry from
`calibration-config.json`. Rotation validity (det +1), finite
matrix, and camera-in-front checks gate every solve. Uncalibrated
factory intrinsics yield coarse `degraded` poses only; metric
validity stays unavailable.

Device proof: `lib/x86_64/libopencv_java4.so` loads from the APK
(`nativeloader ... ok`), detection executes per frame, markerless
virtual scene correctly yields LOST/UNREGISTERED. Algorithm
correctness is JVM-tested against the cv2 oracle
(`pnp-oracle.json`). Positive on-device detection needs board
photons (open gate).

## 8. Guidance overlay and shutter

Screen renders tracking banner, FIT_CENTER preview + marker
overlay, target picker (A3 top-down/oblique, A4), board selector
(a3/a4/front/back), Arm/Pause/Disarm, manual fallback, sharpness
calibration, and the remote-control section. Shutter requires
arming + fresh valid pose + quality + dwell with no cancel,
revoke, or expiry; tracking loss resets hold progress.

Device proof: states render live (pose age, sharpness 130–155,
`Rejected([saturated])` on the bright virtual scene). Armed
20 s without tracking: zero new files, no auto-capture —
the negative shutter path holds on-device.

## 9. Core request/receipt adapters

`GuidedControlClient` (source role only, one exchange per call,
echo rule, unknown spellings fail closed) + `GuidedAuth`
(legacy-token exchange, cached guided token, forced refresh on
401) + `GuidedSessionController` (changes poll, consent,
events, attempts, receipts) + `GuidedDelivery` (package upload,
asset binding, sidecar landing, resume). Transport reuses the
pinned-TLS receiver path; no second transport.

Status: contract-tested against all nine `guided-wire-*.json`
fixtures (21 client + 8 auth + 14 controller + 10 delivery
tests). No live receiver run yet — PENDING_EVIDENCE.

## 10. Room migration and compatibility

Forward-only v1→v2 adds the guided-association link table
(cascade image→link only); legacy packages, JPEG originals,
SHA identities, and queued uploads preserved. `Migration1To2Test`
(3/3) covers migrate + pre-upgrade fixture + association
round-trip on JVM. Fresh emulator install creates v2 directly;
upgrade-on-device is NOT_TESTED. No downgrade path by design
(rollback = reinstall).

## 11. Issues found and fixed on-device

1. **Black guided preview (fixed).** Forward navigation binds
   the entering screen before the leaving screen disposes, so
   dispose-time `unbindAll()` detached the new camera
   (logcat: guided P+A+C bound, then detached 335 ms later,
   camera CLOSED, no error). Fix: dispose unbinds only owned
   use cases in `CameraStillSource`, `GuidedCameraSource`,
   and `QrPairScreen`; entering bind keeps `unbindAll`
   takeover. Verified: clean UseCaseCamera-1 → 2 handover,
   live preview.
2. **First-frame native crash (fixed).** `detect()` allocated
   `Mat` before `OpenCVLoader.initLocal()` → analyzer
   `UnsatisfiedLinkError` killed the app. Fix: `ensureLoaded()`
   before any Mat, failure latched to LOST, `LinkageError`
   contained at the estimator boundary. Regression tests:
   `nativeMissingFailsClosedBeforeMat`,
   `backendLinkageErrorsContained`.
3. **Button-row overflow (fixed).** Third buttons squeezed to
   slivers on 1080 px. Fix: horizontal scroll on the target
   and action rows. Verified visually.

Environment notes: Gradle daemon crashed once
(EXCEPTION_ACCESS_VIOLATION); all builds use `--no-daemon`.
PowerShell turns adb stderr into exit-1 noise; results above
were verified from payload output, not shell codes.

## 12. Emulator evidence (genuine, labelled)

AVD `wave2-pixel`, medium_phone, API 36 x86_64, WHPX accel,
SwiftShader. APK `68298E67…` installed with `adb install -r`.

- Launch: first screen renders, no crash, no ANR.
- Legacy preview: virtual scene live (screenshot).
- Guided preview: virtual scene live after fix (screenshot).
- OpenCV: `libopencv_java4.so` x86_64 loads from APK (logcat).
- Analysis: pose age 8–61 ms, sharpness ~130–155,
  quality `Rejected([saturated])` — real frame measurements.
- Manual still: persisted to
  `files/evidence/originals/<obs>/<img>.jpg` + preview;
  on-device `sha256sum` = `8d825940b888e646…`, matching the
  UI result card. Identity chain holds.
- Armed 20 s, tracking LOST: file count 2 → 2, session
  ACTIVE/ACCEPTED, dwell idle. No false capture.
- Final run logcat: zero FATAL, zero ANR.

Screenshots/logcats were inspected during the session from
`C:/Users/HP/AppData/LocalLow/wave2-*` (scratch, not
committed). These prove the Android runtime path, NOT camera
optics, board detection, or timing on a real phone.

## 13. Synthetic vs measured, stated plainly

- Measured on Android runtime (emulator): everything in §12.
- JVM unit-tested (synthetic/deterministic): PnP oracle
  round-trip, rotation/sign conventions, stale-pose rejection,
  guidance reducers, dwell, shutter matrix, wire fixtures,
  migration, sidecar gates.
- Sidecar validator probe (frozen `calibration/spatial`):
  nominal sample → `needs_review` (UNKNOWN_SOLVE_SOURCE),
  sha `6135715b…`; calibrated sample → `needs_review`
  (UNKNOWN_SOLVE_SOURCE, NULL_MAX_RESIDUAL,
  NULL_UNCERTAINTY), sha `63721a40…`. Zero structural
  rejects; metric qualification correctly withheld by
  design (preview-frame solve, unmeasured print scale).
- NOT measured anywhere: real-board detection rate, real
  pose error, real throughput, real cold starts, real
  rotations, live Core round-trip.

## 14. Gate matrix

| Gate | Result | Evidence |
|---|---|---|
| Toolchain provisioned | PASS | §1, Gradle/JDK/SDK receipts |
| Unit tests 380/380 | PASS | §3 |
| Lint 0 errors | PASS | §3 |
| APK assembles | PASS | `68298E67…`, 184.9 MB |
| Install + launch (emulator) | PASS | §12 |
| Legacy preview (emulator) | PASS | §12 |
| Guided preview live (emulator) | PASS | §12, after §11 fix |
| OpenCV native load (emulator) | PASS | logcat `nativeloader … ok` |
| Detect executes, honest LOST (emulator) | PASS | §12 |
| Quality/sharpness/pose-age live (emulator) | PASS | §12 |
| Manual still SHA-verified (emulator) | PASS | `8d825940…` match |
| Armed, no tracking, no capture (emulator) | PASS | file count 2 → 2 |
| Sidecar samples validator-clean | PASS | §13, no `reasons` |
| G0 contract unchanged | PASS | 13/13, SHA match |
| Compliance 0 errors | PASS | §3 |
| Room v1→v2 migration | PASS | JVM `Migration1To2Test` |
| Positive ArUco + PnP on device | NOT_TESTED | needs board photons |
| Guidance arrows / dwell / auto-shutter + | NOT_TESTED | unit-tested only |
| Display rotations on device | NOT_TESTED | unit-tested only |
| QR pair camera after fix | NOT_TESTED | same fix, not re-driven |
| Upgrade-on-device (v1→v2 data) | NOT_TESTED | fresh installs only |
| Core live round-trip over TLS | PENDING_EVIDENCE | fixture-tested only |
| Real-phone camera/tracking/ANR/perf | PENDING_EVIDENCE | no phone attached |
| 15 Hz / p95 ≤150 ms / 20 cold starts | PENDING_EVIDENCE | physical gates |

## 15. Security and provenance

- No regressions: consent/arm gating unchanged and
  stronger (negative path proven on-device); TLS pinning
  reused, no second transport; image bytes hashed at rest
  (mode 600) with UI-shown SHA matching disk bytes.
- No images leave the device in any test (no receiver
  was live; no pairing performed on the emulator).
- No background capture: camera binds only to visible
  screens; navigation cleanly transfers ownership (§11).
- Memory/journals untouched: no receiver production code
  changed (one test file: tripwire retired into presence
  + scatter check after transport landed).
- Main, E3-B, Assembly Memory, and all other worktrees
  untouched. No push, no merge, no history rewrite.

## 16. Manual hardware actions for the user

1. Attach a physical phone (USB debugging, authorized) or
   say the word for a store-ready build; then:
   `adb install -r <APK>` and grant the camera permission.
2. Print-scale session: measure the mounted A3/A4/fixture
   sheets, record `measured_print_scale` in
   `calibration-config.json` (still empty by policy).
3. First real guided run: open Guided Capture on the A3
   surface, confirm tracking VALID, drive one armed
   dwell to auto-capture; report pose age + quality + RMS.
4. Calibration session: ChArUco captures → measured
   profile → flip metric validity from `nominal_only`.
5. Workstation receiver up with TLS + pairing, then:
   enroll, attach session, reserve attempt, capture,
   verify receipt linkage and sidecar qualification.
6. Real-image CAD + FreeCAD GUI + live MCP remain
   PENDING_EVIDENCE as handed off from Wave-1.5.

## 17. Rollback

- Code: `git checkout 5ef14ad8` (Wave-1.5 HEAD) or delete
  branch `feat/wave2-guided-capture`; lane commits
  `3dbde8f5`/`e669bb96`/`cd95c01b`/`deb6c5c0` stay intact.
- Device: uninstall the debug APK; Room v2 has no
  downgrade (reinstall = clean v1→v2 re-migrate).
- Workstation: remove `wave2-pixel` AVD
  (`avdmanager delete avd -n wave2-pixel`); SDK additions
  (emulator, system image) are inert until used.
- No data loss: no memory, journal, or evidence file was
  modified outside the disposable emulator.
