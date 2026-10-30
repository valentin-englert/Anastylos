# UX-00 — Inspiration Gap Audit (AgentCam reference vs ANASTYLOS Android)

Audit only. No code changed, no commits. Baseline: `main` @ `f88456ee`
(contains HARDWARE-03 `bcea6310`; Android tree identical to the audited
integration HEAD). Device APK on the SM-A536B is the audited build
(`91b44a29…`, verified path-identical).

Reference: AgentCam-on-Honeypot keyframes — 04s target request, 10s
mm-guidance, 16s hold-still/dwell, 34s sent-confirmation vs socket error.

## Evidence labels

- CODE: read in `capture-companion/android/app/src/main` on main.
- UNIT: Android suite, 384/384 incl. frozen vectors (per ARCHITECTURE.md).
- PHYS: SM-A536B screenshots in `hw03-evidence/` (2026-10-29, same APK).
- PHYS-FRESH: `ux00-evidence/ux00-fresh1.png` (session screen, sampling
  stopped, 81 stored/0 missed, HW03 persistent) and
  `ux00-evidence/ux00-guided.png` (guided idle/searching, DISARMED,
  UNREGISTERED, pose age 156 ms, honest dark reject on black frame),
  captured 2026-10-30 ~00:53 with operator taps (touch injection
  stays blocked remotely). Device APK re-verified as `91b44a29…`.

## 1. Current screens and layout

Three Compose destinations (`MainActivity` NavHost, start = session):

- session: preview (240dp) + Start/Stop sampling, session CRUD +
  interval chips, capture counters, workstation pairing (QR + manual),
  Sync section with `Upload waiting frames` (enabled only when paired,
  session active, pending > 0). PHYS-verified end to end in HW-03.
- guided: scrolling telemetry column — Back + title, UNCALIBRATED
  banner, TrackingBanner, preview + Canvas overlay (FIT_CENTER,
  analytic mapping), text cue list, StateGrid telemetry, TargetPicker
  (3 preset chips + 4 board chips), ControlRow (Arm/Next/Pause/Disarm/
  manual capture/Cancel/Can't reach), QualityRow (sharpness +
  calibrate/clear), RemoteSection (Core attach/adopt/attempt/delivery),
  ResultCard (sha + association note), error lines.
- pair: ML Kit QR scanner + confirm screen (workstation id, endpoint,
  code expiry; no secrets shown) + retry.

Visual hierarchy today: telemetry-dominant scrolling form, small
preview. The reference is camera-first full-bleed with overlay
graphics and one thumb-zone action cluster.

## 2. Overlays, pose, calibration, quality feedback

- Detection overlay (CODE+UNIT): green corner quads + yellow
  corner-0 dot, analytic rotation-aware mapping, hidden on aspect
  mismatch > 2%. On-screen quad rendering NOT visually confirmed on
  device (HW-03 shots predate live corners) — PARTIAL.
- Pose feedback: pose age ms + RMS px as text (PHYS: 182–358 ms).
  No frustum/box/arrow graphics — PARTIAL.
- Calibration: UNCALIBRATED banner + BAD_CALIBRATION fail-closed
  state, metric evidence disabled (PHYS) — PRESENT and honest.
- Quality: H6 verdict (Accepted/Rejected[reasons]/Unknown) + sharpness
  vs operator reference + dark/saturated/motion inputs (PHYS: dark
  reject at 0.224). Thresholds frozen, never weakened — PRESENT.

## 3. Targets, guidance, distance, progress

- Real target poses: 3 local presets with 3D poses (A3 top-down,
  A3 oblique, A4 top-down) + Core-adopted wire targets with
  fail-closed matrix validation — PRESENT.
- Directional guidance: 6-axis mm/° cues, rotation-aware screen
  frame, frozen tolerances (15/15/20 mm, 5°) — PRESENT in code,
  never exercised with a real VALID pose — PARTIAL.
- Distance-to-target: per-axis deltas as text; no aggregate
  ("14 mm") and no closing-rate line — PARTIAL.
- Progress: request state shown as enum text; no queue position,
  no dwell ring, no next/All-done flow — PARTIAL.

## 4. Dwell and auto-shutter: tested vs physical

- Dwell 1000 ms / 12 samples / 150 ms gap / 8 mm-s, fail-closed on
  negative age (UNIT incl. new quad + dwell tests) — PRESENT.
- ShutterGate: 14 preconditions, first-violation reason shown as
  `Hold: <code>`; exactly-once via reducer edge + arming token
  (UNIT) — PRESENT.
- PHYS: negative paths verified (20 s armed with no capture while
  untracked; DISARMED default; BAD_CALIBRATION blocks). Positive
  auto-fire NEVER observed — BLOCKED on calibration.
- Manual fallback `Capture now (manual)` + `Can't reach` — PRESENT
  (matches the reference's escape hatch).

## 5. Persistence, sync, error recovery

- Slice-A sync PHYS-verified: 7 stored/0 missed, 3 synced/0/0,
  byte-identical receiver asset + manifest linkage — PRESENT.
- Guided delivery pipeline (attempt reserve → sidecar → upload →
  receipts incl. qualified/package_only/rejected/late) CODE+UNIT,
  no live Core round-trip — PARTIAL.
- Recovery: text errors + Dismiss/Retry; no prominent sent banner,
  no socket-error banner with retry as in the 34s frame — PARTIAL.
- Exposure-time sidecar stays honestly unqualified (nearest frame
  informational only) — PRESENT, BLOCKED on exposure link.

## 6. State management and the single-screen path

One `GuidanceUiState` StateFlow fed by four pure reducers (session 8
states, request 13, delivery 7 + 7 receipt kinds, registration 5)
plus Dwell/SpeedTracker/ShutterGate; ViewModel injects camera, repo,
clocks, remote — JVM-testable with fakes. The UI is already a pure
function of that state, so a single-screen HUD is a presentation
re-skin: same state, same reducers, no logic rewrite. Smallest safe
path: keep `GuidedCaptureScreen` as the route, replace its column
sections with preview-layer composables reading the same fields.

## Gap matrix

| Capability | Status | Evidence |
|---|---|---|
| Session/sampling/pairing/upload flow | PRESENT | PHYS E2E HW-03 |
| Target presets with real 3D poses | PRESENT | CODE+UNIT |
| Core wire-target adoption | PRESENT | CODE+UNIT |
| 6-axis mm/° text cues | PRESENT | CODE+UNIT |
| Cues against real VALID pose | BLOCKED | needs calibration |
| Aggregate distance + closing rate | MISSING | no code |
| Detection-quad overlay | PARTIAL | CODE+UNIT, no PHYS pixels |
| Target frustum/arrow on preview | MISSING | no code |
| Dwell accumulator + shutter gate | PRESENT | CODE+UNIT |
| Positive auto-fire on device | BLOCKED | needs calibration |
| Dwell progress ring/visual | MISSING | text only |
| HOLD STILL banner | PRESENT | CODE, PHYS untested |
| Request queue position display | MISSING | id exists, unshown |
| Auto-advance + All-done screen | MISSING | button only |
| Manual capture + Can't reach | PRESENT | CODE+UNIT |
| Capture result with sha | PRESENT | CODE+UNIT |
| Guided delivery + receipts UI | PARTIAL | CODE+UNIT, no live Core |
| Sent/socket-error banners | MISSING | text notes only |
| Calibration fail-closed UX | PRESENT | PHYS HW-03 |
| Quality verdict + sharpness cal | PRESENT | CODE+UNIT+PHYS |
| Camera-first full-bleed layout | MISSING | scrolling form |

## Proposed screen hierarchy (guided route only)

- Preview layer (full-bleed): camera + quad overlay + target
  frustum + direction arrow + dwell progress ring + HOLD banner.
- Top strip: request id + queue position + tracking dot +
  calibration dot (tap → Details).
- Thumb cluster: big shutter (state-colored: navigate/hold/
  capturing/disabled+reason), Can't reach, Cancel.
- Details sheet (collapsed default): today's StateGrid, QualityRow,
  TargetPicker, RemoteSection, ResultCard, errors.
- Session/pair routes unchanged.

## Explicit UI state machine (over the existing reducers)

- IDLE (DISARMED) → ACQUIRING (ACTIVE, registration ≠ ACQUIRED,
  "aim at the board") → NAVIGATING (ACQUIRED, cues > 2) →
  ALIGNING (cues ≤ 2) → HOLDING (HOLD_STILL, ring fills) →
  CAPTURING (shutter committed) → VERIFYING → DONE (result +
  delivery pill) or RETAKE (reason + Retry).
- Any non-ACQUIRED during NAVIGATING/ALIGNING/HOLDING →
  TRACKING_LOST banner, dwell resets (already in reducers).
- Delivery pill: LOCAL → SENDING → RECEIPT qualified /
  package_only / rejected / late → NEXT or ALL_DONE ("N photos,
  put the phone down").
- No new reducer states required; all are projections of
  session/request/delivery/registration/dwell today.

## Three prioritized slices

### Slice 1 — Guided HUD (presentation only)

Full-bleed preview, frustum + arrow overlay, dwell ring, HOLD
banner, queue/All-done projections. No reducer, threshold, or
contract change.
Acceptance: per-state screenshots (emulator + device with board,
no calibration needed except live cues); OverlayMapping unit
tests extended; 384-suite stays green.

### Slice 2 — Request queue + completion flow

Show requestId/queue position, auto-advance to the next local
target after CAPTURED_LOCAL, All-done card with count +
thumbnails + delivery pill.
Acceptance: JVM reducer-sequence tests; PHYS: two manual targets
back-to-back with correct queue display and delivery pills.

### Slice 3 — Delivery feedback + recovery parity

Prominent Sent/receipt states, retry-with-backoff UI, socket-error
banner with retry (reference 34s frame).
Acceptance: fixture-server failure-injection tests; PHYS: forced
failure (receiver stopped mid-upload) shows banner, retry after
restart delivers with matching receipt.

## Retain, do not rewrite

Reducers, ShutterGate + arming tokens, Dwell, computeCues +
tolerances, quality verdicts + sharpness calibration, OverlayMapping
math, Preview/Analysis/Capture binding + selective unbind,
Room + idempotent upload, pairing secret handling, ResultCard
honesty (sha + unqualified-sidecar note), calibration fail-closed
gating, Can't reach + manual fallback, error-text explicitness.
