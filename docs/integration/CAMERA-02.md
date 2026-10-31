# CAMERA-02 — Professional Camera Controls

- Scope: native Android app only (`capture-companion/android`), session (Live Observer) + Guided Capture.
- Device: Samsung SM-A536B (Exynos 1280), wireless ADB `192.168.0.42:43947` (+ USB serial `R5CTA06SFRB`).
- Final APK: `capture-companion/android/app/build/outputs/apk/debug/app-debug.apk`, SHA-256 `7E67F84A409D04A7AD61799B7FA049437B5BF9D5A3BFE202A411D9EC4B1639DD` (hash-verified on device).
- Gate: 420/420 unit tests PASS, `lintDebug` 0 errors, `assembleDebug` SUCCESS, compliance 0 errors.
- Status: implemented + device-verified. Changes UNCOMMITTED, no push/merge (per task).
- Evidence dir: `cam01-evidence/cam02-*` (screenshots, JPEGs, EXIF logs in report).

## 1. Implementation summary

Shared, single-source implementation used by both screens (no duplication):

- `camera/CameraSettings.kt` — `CameraSettings` (exposure/focus/AWB/zoom/torch/analysis+still size/profile), `CameraCapabilities` (ranges, modes, sizes, `manualSensor`), `sanitizeSettings` (clamp + force auto when unsupported), `aspectBucketFor/aspectStrategyFor` (16:9 vs 4:3 selector aspect).
- `camera/CameraSettingsStore.kt` — DataStore persistence per `(cameraId, profile)` + active profile; versioned `v1;...` serialization.
- `camera/SettingsApplier.kt` — sole owner of control requests: one Camera2 interop bundle per apply (AE/AF/AWB modes, SENSOR_*, focus distance, EV compensation) + non-overlapping CameraX controls (zoom, torch). `StillOptions`/`stillOptionsFor`/`applyStillOptions` mirror the same keys at ImageCapture bind time (see §4.1).
- `camera/CameraInventory.kt` — `UsableCamera` now carries `capabilities`, parsed per camera (incl. `REQUEST_AVAILABLE_CAPABILITIES` → `manualSensor`).
- `camera/CameraStillSource.kt`, `camera/GuidedCameraSource.kt` — apply settings, `buildCapture` (Extender + resolution selector + aspect), `refreshStillOptions` (selective ImageCapture-only rebind, Preview/Analysis stay attached; restores previous capture on failure), stream-negotiation logging (`requested=… resolved=…`).
- `camera/StillCamera.kt`, `camera/GuidedCamera.kt` — seams extended with `suspend fun refreshStillOptions()`.
- `camera/Camera2Intrinsics.kt` — profile keyed by `(cameraId, analysis WxH, linearZoom)`; zoom change re-keys (`…-z46-…`) instead of silently reusing intrinsics.
- `ui/camera/CameraSettingsPanel.kt` — shared minimal panel: profile chips, Auto/Manual chips, sliders (commit on release), AWB chips, zoom, analysis/still size chips, torch, `Applied: n/a` line, honest "Manual unavailable…" note.
- `camera/AppliedSettings.kt` — requested-vs-applied display path; sampling stays `null` (§4.4).

## 2. Device capability matrix (SM-A536B, from `cam01-evidence/camera-dumpsys.txt`)

All four cameras: LIMITED, no `MANUAL_SENSOR`, no RAW. AE modes include OFF everywhere (but OFF only locks AE, see §4.2).

| camera | facing | focal | flash | zoom max | AF modes | min focus | AWB modes | ISO range | exp range | EV |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Back | 5.23 mm | yes | 8x | OFF/AUTO/CONT_PICT/CONT_VIDEO | 10 dpt | OFF,AUTO,INCAND,FLUOR,DAYLIGHT,CLOUDY | 50–3200 | 166.7 µs–125 ms | ±20 @0.1 |
| 1 | Front | 3.72 mm | no | 4x | OFF only (fixed) | fixed | same 6 | 50–3200 | 166.7 µs–125 ms | ±20 @0.1 |
| 2 | Back (UW) | 1.74 mm | no | 4x | OFF only (fixed) | fixed | same 6 | 50–3200 | 166.7 µs–125 ms | ±20 @0.1 |
| 3 | Front aux | 3.72 mm | no | 4x | OFF only (fixed) | fixed | same 6 | 50–3200 | 166.7 µs–125 ms | ±20 @0.1 |

JPEG/YUV sizes include 4624×3468 (max), 3840×2160, 1920×1080, 1280×720, 960×720, 640×480, … (BLOB/YUV maps verified).

## 3. Device verification (M6)

Requested → applied, each proven on hardware (independent oracle in brackets):

| control | result | proof |
|---|---|---|
| Manual exposure (ISO/shutter) | CORRECTLY UNAVAILABLE | Requested MANUAL@400/1-50 → EXIF `ISO=419, 1/40s` (3 burst frames identical); extremes MANUAL@3200/125 ms → still `ISO=419, 1/40s`. HAL ignores SENSOR_* (no MANUAL_SENSOR); AE_OFF locks at best. UI hides Manual + shows note; sanitize forces AUTO. (`cam02-manual1/first/mid/extreme/fixed.jpg`, EXIF logs) |
| AWB presets | PASS preview + still | Incandescent → preview blue (`cam02-awb.png`); still `WB=1` + blue JPEG (`cam02-awb-still.jpg`) via Extender. |
| MF focus (cam0) | PASS preview | MF@∞ sharp (`cam02-mf.png`) → MF@10 cm blurred (`cam02-mfnear.png`). AF stays default. |
| Zoom | PASS + profile re-key | 0→47 % framing enlarged (`cam02-zoom.png`); guided `profile=…-z0-…` → `…-z46-…` → `…-z6-…` in UI banner + logs. |
| Torch | PASS (HAL-acked) | `TorchMode(value=1)` + `setTorchOnAsync` + Samsung `setTorchPower`; OFF path also logged. Preview flat due to AE compensation (expected). |
| EV compensation | PASS preview + still (after fix) | CameraX EV proven dead (EV −18/0/+17 all luma ≈111); moved to interop bundle + Extender → luma 75.3/110.9/178.0 graded (`cam02-evfix-min/max.png`); stills dark vs blown (`cam02-ev-still-min/max.jpg`). |
| Still sizes | PASS exact | `requested=1920x1080 resolved=1920x1080` (after §4.3 fix); JPEG `PxWidth=1920 PxHeight=1080` (`cam02-still-1080p.jpg`). |
| Analysis sizes | PASS (guided) | `analysis=1280x720 preview=1280x720` negotiated (was 960×720 before aspect fix); chips 1920×1080/1280×720/960×720 render. Size-change rebind path unchanged from CAMERA-01. |
| Persistence | PASS | DataStore `camset_0_Default` vs `camset_0_Bright` isolated; cam1 untouched (defaults); active profile persists; force-stop + relaunch restores Bright/ev16 (log + UI). |
| Guided regression | PASS (partial, see §5) | Bind/profile/aspect/still-opts/panel/chips verified; tracking live (16 markers, pose age ~140–200 ms, honest DEGRADED + `dark` quality gate); session DISARMED never captures. |
| Sampling/storage/sync | PASS, no regression | 27 frames queued → `27 synced, 0 rejected, 0 failed`; receiver got 27 packages; byte-identity phone→receiver→manifest `39221947…` verified. "Upload not tappable" = no session selected (button correctly disabled at 0 waiting). |

Unit/lint/build across iterations: 412/412 → 417/417 → 418/418 → 419/419 → **420/420**, lint 0 errors throughout after each fix, compliance 0 errors. APK hashes: `A854FC3D…` (first green) → `E9A7AEDA…` (still-opts) → `EB76B41C…` (MANUAL_SENSOR gate) → `A6237B03…` (EV interop) → `F7BB51F5…` (stream logging) → `7E67F84A…` (aspect fix, final, on device).

## 4. Key findings (all device-proven, all fixed or gated)

1. **Runtime interop options do not reach still captures.** Preview/analysis follow the runtime bundle, but ImageCapture builds its own request (MAXIMIZE_QUALITY precapture). Fix: `StillOptions` baked via `Camera2Interop.Extender` at build + selective ImageCapture rebind on change (`refreshStillOptions`); MINIMIZE_LATENCY in manual mode so no precapture disturbs manual values; no-op skip when the still slice is unchanged; previous capture restored on failure. Proven by AWB/EV stills.
2. **SM-A536B honors no manual SENSOR_* (LIMITED, no MANUAL_SENSOR).** AE_OFF locks converged AE at best (EXIF-identical bursts). Fix: `supportsManualExposure` requires `MANUAL_SENSOR`; UI hides Manual with an explicit note; sanitize/still-options fall back to AUTO/QUALITY. Samsung Pro Video manual modes must use vendor paths — out of slice.
3. **CameraX `setExposureCompensationIndex` is ineffective on this stack** (3 EV states, identical luma). Fix: EV moved into the interop bundle (single writer) + Extender for stills; CameraX EV call removed.
4. **CameraX default aspect strategy is 4:3-fallback.** A bare `ResolutionStrategy(1920×1080)` resolved to `4624×3468` (and guided 1280×720 → 960×720). Fix: every explicit-size selector carries a matching `AspectRatioStrategy` (`aspectStrategyFor`); negotiated sizes are logged (`requested/resolved`) and the guided log already shows actuals.
5. **No public CaptureResult path in CameraX 1.6.2.** Both `CameraCaptureResultImageInfo` and `ImageInfo.getTagBundle` are same-library `RestrictedApi`. No reflection, no suppression: UI shows `Applied: n/a`; requested-vs-applied is proven via EXIF/JPEG instead. The `AppliedSettings` seam + test stay for a future public API.
6. **Opt-in pattern that passes lint:** direct `@ExperimentalCamera2Interop` on overrides (interface methods stay unannotated → no cascade). `@OptIn` on an override does not silence this repo's lint gate; `ImageInfo`/`CameraCaptureResultImageInfo` access is banned by `RestrictedApi`.

## 5. Known limitations / open gates

- Manual ISO/shutter: impossible on SM-A536B via public API (see §4.2). Needs a MANUAL_SENSOR-capable device for a positive test; Samsung vendor keys out of scope.
- Applied sampling (`Applied: n/a`): blocked on a public CameraX result API.
- Guided dwell auto-shutter: NOT re-verified on this build — ADB touch injection on the guided screen is unreliable (2/11 gestures landed; log spam + 30 fps recomposition suspected; session taps 100 % reliable). Guided bind/analysis/profile/still-opts/panel/chips/tracking/quality-gate/disarm verified; dwell/shutter code itself untouched by CAMERA-02. Finger re-test recommended.
- Guided sliders specifically never responded to injected drags (same composable proven on session). Finger re-test recommended.
- Torch `setTorchPower(0)` semantics (level numbering) not fully decoded; ON/OFF acknowledged by HAL.
- Pre-existing, untouched: OpenCV `StaticHelper` full-config log spam (rotates logcat in minutes); `dark` quality verdict still needs a lit-scene retest (HARDWARE-03 carryover); sync UX (disabled Upload with 0 waiting) confused the operator — counts are per selected session.
- Front-aux cam3 imaging uncharacterized (CAMERA-01 carryover); failure-recovery (missing/stale ID) unit-tested only.

## 6. Follow-ups (not in this slice)

1. Finger-driven guided dwell capture on this build (tracking + HOLD STILL + auto still + persistence).
2. Positive manual-exposure test on a MANUAL_SENSOR device (no code change expected).
3. Re-test `dark` verdict + tracking in daylight; consider quality-threshold evidence (no relaxation).
4. Silence OpenCV init logging (perf/log-noise, out of scope here).
5. Optional evidence-contract extension (`camera_id`/settings/profile in sidecar + manifest) — proposal from CAMERA-01 §5, no schema touched.

## 7. Provenance

- Modified (main): `camera/{Camera2Intrinsics,CameraStillSource,GuidedCameraSource,GuidedCamera,SettingsApplier,CameraSettings,AppliedSettings}.kt` (last three new), `camera/{StillCamera,CameraInventory}.kt` (new), `data/{CameraPrefs,CameraSettingsStore}.kt` (new), `ui/camera/CameraSettingsPanel.kt` (new), `ui/session/{SessionScreen,SessionViewModel}.kt`, `ui/guided/{GuidanceViewModel,GuidedCaptureScreen}.kt`, `di/AppContainer.kt`; tests `camera/{CameraSettingsTest,CameraInventoryTest}.kt` (new), `ui/{guided,sess
...[truncated 1359 chars]