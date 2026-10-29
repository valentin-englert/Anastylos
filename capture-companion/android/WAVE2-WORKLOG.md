# Wave-2 Android worklog (feat/wave2-guided-capture)

Base: 5ef14ad89b1b8f4811370b9867dd31599f59c1f8 (Wave-1.5 HEAD).
Created: 2026-10-29. Status: Phase A toolchain provisioning.

## 2026-10-29 — Phase B/C/D: toolchain, OpenCV tracking, guidance UI

Toolchain (isolated, out of Git): Temurin JDK 21.0.12.1+1
(`C:/Users/HP/jdk/jdk-21.0.12.1+1`, SHA-verified at install), reused
SDK `C:/Users/HP/Android/android-sdk` + `platforms;android-37.0` +
`build-tools;37.0.0`, Gradle 9.8.2 + AGP 9.4.1, CameraX 1.6.2,
OpenCV `org.opencv:opencv:4.14.0` AAR (Apache-2.0, `6d11b40f...`).
Gradle MUST run under JDK 21 (`$env:JAVA_HOME=...jdk-21.0.12.1+1`):
JDK 17 breaks Robolectric (SDK 36) and the TLS round-trip test.

Phase C: `OpenCvPoseEstimator` (ArUco + solvePnP, oracle-checked
against cv2 4.14.0), `GuidedCameraSource` (Preview+Analysis+Capture,
1280x720, YUV latest-only, fail-closed), `ShutterGate`,
`DwellAccumulator`, `FrameQuality`, `SpeedTracker`, `PoseMath`.

Phase D: `GuidanceViewModel` (session/request/registration reducers,
dwell-gated auto-shutter, manual fallback, `GuidedPrefs`), local
targets `a3-top-center` + `a3-oblique-left`, `GuidedCaptureScreen`
(preview + detection overlay + cues + arming) on route `guided`,
wired in `MainActivity`/`SessionScreen`/`AppContainer`.
Overlay uses analytic FIT mapping (`OverlayMapping`, tested) over
public `resolutionInfo` geometry — no restricted CameraX APIs; the
quads hide on stream-aspect mismatch rather than misdraw.
`@ExperimentalCamera2Interop` opt-ins on the intrinsics call chain.

Evidence (JDK 21, `--offline`):
- `:app:testDebugUnitTest` 300/300 green, 0 skipped.
- `:app:lintDebug` 0 errors, 5 warnings (pre-existing set).
- `:app:assembleDebug` OK → `app/build/outputs/apk/debug/app-debug.apk`,
  193,416,530 bytes (OpenCV natives × 4 ABIs; was 41 MB pre-OpenCV),
  SHA-256 `3021FDCDDAAF58FB0269C09A94FB052716EC1E9F449B2EEE03E99EA5FC76BD7D`.
- No device attached (`adb devices` empty); no emulator installed.
  APK install/launch + real-board tracking remain PENDING_EVIDENCE.

Next: Phase E Core adapters (guided HTTP, consent/grants, Room
migration, upload/receipts), then Phase F acceptance.
