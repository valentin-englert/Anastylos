# CAMERA-01 — Capability-Driven Camera Discovery & Selection

Date: 2026-10-30. Worktree: `C:\Users\HP\Documents\Anastylos-integration-spatial`,
branch `integration/spatial-capture-v1`. Status: implemented, unit-verified,
device verification in progress. No commits (task authorizes no history writes).

## 1. Implementation summary

Replaced the hardcoded back/front `requireLensFacing` selection in both
camera sources with one shared inventory + resolution path:

- `camera/CameraInventory.kt` (new): `UsableCamera` (id, label, facing,
  focal, sensor, flash, level), pure `resolveCamera()` (exact stored-ID
  match → lowest-ID back → lowest-ID camera; a requested-but-missing ID
  always yields a note, never a silent substitution), pure `cameraLabel()`
  (facing + ID + measured focal only; roles never guessed),
  `CameraInventorySource` seam + `CameraXInventorySource` (per-camera
  containment: one unreadable camera never hides the rest).
- `camera/StillCamera.kt` (new): stable contract for the Live Observer
  source, mirroring `GuidedCamera` (keeps error-level Camera2-interop
  opt-ins inside the implementation; no lint cascade).
- `data/CameraPrefs.kt` (new): DataStore-backed selected camera ID,
  shared by both screens (single owner, no duplicated state).
- `ui/camera/CameraPicker.kt` (new): minimal chip row + verbatim note,
  shared by both screens. Hidden while the inventory is empty.
- Sources (`CameraStillSource`, `GuidedCameraSource`): accept
  `requestedCameraId`, resolve via the shared function, bind Preview /
  Analysis / Capture through one ID-filtered `CameraSelector`, expose
  effective ID + inventory + note, and log
  `requested / effective / available[] / stream geometry / profileId`
  under `AnastylosCam`. Bind failures return false with a note (no
  crash, no silent switch).
- `GuidedCamera.StreamGeometry` gains additive `cameraId` (defaulted;
  existing constructions compile unchanged).
- ViewModels: both own `availableCameras / selectedCameraId /
  effectiveCameraId / cameraNote`; `selectCamera()` persists
  synchronously-visible, then rebinds. Guided rebind resets dwell,
  speeds, corners, last estimate and motion, recomputes intrinsics
  (profileId embeds the camera ID: `factory-<id>-uncalibrated`), and
  disarms the session via detach (rearm required) — no metric state
  crosses a sensor switch.
- Session screen rebinds through the existing `unbindAll`-takeover on
  selection change; an in-flight sampling frame during the switch
  degrades to a counted `FrameMissed`, never a crash.

## 2. Device capability matrix (SM-A536B, dumpsys + in-app)

Source: `cam01-evidence/camera-dumpsys.txt` (2026-10-30) + in-app
`CameraXInventorySource` log (identical ID set).

| API ID | Facing | Focal | Sensor (mm) | Flash | Level | Max PRIV/YUV | Role |
|---|---|---|---|---|---|---|---|
| 0 | Back | 5.23 mm | 7.42×5.56 | yes | LIMITED | 4624×3468 | main wide |
| 1 | Front | 3.72 mm | 5.22×3.92 | no | LIMITED | 3264×2448 | selfie |
| 2 | Back | 1.74 mm | 4.48×3.36 | no | LIMITED | 4000×3000 | ultra-wide |
| 3 | Front | 3.72 mm | 4.21×3.16 | no | LIMITED | 2640×1980 | front aux |

Not API-exposed (HAL-only, correctly absent from the picker): 23/4
(duplicates), 52 (macro 1.96 mm), 58 (UW dup), 60 (depth 2.13 mm).
No logical multi-camera physical IDs are exposed. All cameras LIMITED.
In-app log: `still bind requested=<default> effective=0 (Back 0 · 5.2 mm)
available=[0, 1, 2, 3]`.

## 3. Test results

- Android unit: 394/394 (was 384; +6 `CameraInventoryTest`, +2 guided
  select/rebind + inventory/note, +2 session persist/surface).
- Lint: 0 errors (2 UnsafeOptInUsageError fixed via the StillCamera
  seam + literal-2 lens constant, matching the Wave-2 precedent).
- Build: `:app:assembleDebug` SUCCESS. APK `AA858633…` verified
  byte-identical on device.
- Receiver/compliance: untouched paths; no rerun required (Android-only
  change; report honestly: NOT RUN this slice).

## 4. Device evidence

Build `AA858633…` on the SM-A536B (hash-verified). Touch injection
stays blocked; all taps by the operator.

- Session switches (log): `requested=1→1`, `2→2`, `3→3`, `2→2`,
  `0→0` — every bind exact, zero fallback notes, labels
  `Back 0 · 5.2 mm`, `Front 1 · 3.7 mm`, `Back 2 · 1.7 mm`,
  `Front 3 · 3.7 mm`.
- Guided switches (log): cameras 2 → 3 → 0 → 1, each with
  `analysis=960x720 preview=960x720` and a new profile
  (`factory-2/3/0/1-uncalibrated`) — no intrinsics reused.
- Framing (`cam01-evidence/cam01-f1/f2/f3.png`): cam 0 = black room,
  cam 1 = monitor + wall (`Front 1` chip highlighted), cam 3 = lit
  wall/door — visibly different sources, each with live analysis
  (fresh pose age, quality verdict reacting). UW framing shot
  missing (bound 9 s, no screenshot in window); its bind + stream
  + profile are logged.
- Picker UI: chip row with accurate labels on both screens
  (`cam01-guided.png`, `cam01-f2/f3.png`); selection highlight
  follows the effective camera.
- Manual capture on camera 1: JPEG `8d7c48c8…`, 2,303,616 bytes,
  magic `ff d8 ff e1`, persisted + SHA-verified on device.
- Persistence: force-stop + relaunch → `still bind requested=1
  effective=1`, no note — selection restored.
- Regression: 82 stored originals (81 pre-existing + 1 new);
  legacy evidence untouched. No full re-run of sampling/upload
  this slice (code path unchanged for the default camera; sync
  suite untouched).

## 5. Evidence-contract assessment (decision required, no change made)

The capture package manifest, image sidecar and guided spatial
sidecar carry NO camera provenance today (verified by search:
no camera/lens/focal/device field in `Manifest.kt`,
`PackageBuilder.kt`, `SpatialSidecar.kt`, `data/`). Two captures of
the same target from cameras 0 and 2 are currently
indistinguishable downstream, and a future metric calibration
cannot be keyed to the recording sensor.

Proposed additive contract change (owner decision, CAMERA-02 or
calibration slice): add optional `camera_id` (Camera2 ID string),
`lens_facing` (`back`/`front`/`external`), `focal_length_mm` and
`profile_id` to the guided spatial sidecar + the ZIP manifest
generator block. Producers fill from `StreamGeometry.cameraId` +
inventory; old packages without the fields stay valid (absence =
unknown camera, never assumed). No schema file was touched.

## 6. Known limitations & CAMERA-02 follow-up

- Failure recovery (missing/stale ID → noted fallback) is unit-tested
  but not physically forced on device (all 4 cameras healthy).
- Guided disarm-on-switch is wired (detach path) and reducer-tested
  but not device-demonstrated (would need arm → switch → state read).
- Front-aux camera 3 binds through the same path but its imaging
  pipeline (bokeh/depth) is uncharacterized; framing shot will show
  whether it yields a normal preview.
- Manual ISO/shutter/WB controls explicitly excluded (unchanged).
- No UI redesign: picker is a chip row inside the existing scrolling
  layouts (UX-00 HUD work unaffected, can consume the same state).
- Sampling across a switch yields counted misses (accepted, documented).

Rollback: revert the uncommitted CAMERA-01 working tree; reinstall the
prior APK (`91b44a29…`); DataStore `camera` prefs file is inert without
the code. No migration, no schema change, no receiver impact.
