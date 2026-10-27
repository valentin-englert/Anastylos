# Known limitations (v0.1.0 prototype + sync v1)

Honest boundaries of this iteration. Nothing here is presented as working
in the app when it is not.

## Not implemented (labeled as such in the UI)

- **Automatic identification.** No provider exists; the review screen's
  identification section states this and no photo leaves the device. The
  provider-neutral interface is declared in `app/src/lib/identify.ts`.
- **Ordering, catalogs, suppliers.** The BOM screen is a confirmed-data
  draft with CSV export. No part numbers, ratings, compatibility or
  purchasing — by design.

## Network sync v1 (implemented prototype)

The Sync screen pairs a workstation and pushes export-identical packages
over authenticated TLS (`SYNC_PROTOCOL.md`, `receiver/README.md`). Its
boundaries:

- **Manual trust.** The browser cannot pin certificates from JavaScript,
  so trust is the operator's job: install the workstation certificate on
  the phone, or compare the printed fingerprint character by character
  before pairing. The PWA stores the fingerprint as a display reminder
  only and says so.
- **Whole-package uploads only.** Interruptions retry the full package;
  there is no chunked resume. The size cap is the validator's 512 MiB.
- **Valid-or-nothing staging.** A package the validator marks `invalid`
  is rejected as a unit with its report, even if some observations look
  acceptable. Nothing is staged, nothing is marked.
- **One operator, one workstation.** No user accounts; revocation is
  all-or-nothing (`revoke-tokens --yes`). The PWA path has no
  per-device labels; the native QR path shows the phone's label at
  approval time (`pair-status`) but revocation stays global.
- **Operator-triggered only.** Nothing uploads by itself; there is no
  automatic upload after capture and no background sync.
- **Lab-LAN service posture.** Unbounded threading, plain-JSON state
  files, no log rotation: fine for one phone on a private network, not
  a public service. The pairing token lives in the phone's IndexedDB —
  unpair and revoke on device loss.
- **Not yet run on hardware.** The receiver and the Sync screen were
  exercised by automated tests (mocked HTTP + loopback HTTP), not yet
  on physical phones over a real LAN with a real TLS certificate.

## Native in-session frames (Slice A transport milestone)

The Android app under `android/` delivers automatically sampled CameraX
stills through the existing package endpoint during an active session
(`SYNC_PROTOCOL.md`, `receiver/SESSION_FEED.md`). It is a transport
slice, not the full native product:

- **Pairing: QR first, manual fallback.** The app scans the
  workstation QR (in-app ML Kit scanner, bundled offline model),
  pins the scanned SPKI, and waits for operator approval
  (`SYNC_PROTOCOL.md`). The manual host/port/code/fingerprint form
  remains for unscannable cameras. A key change fails closed and
  needs a fresh QR; certificate renewal over the same key needs no
  re-pairing.
- **QR pairing caveats.** Approval is poll-only (2 s interval): the
  operator must approve within the code lifetime or the phone needs a
  fresh QR. Treat a displayed QR like a short-lived secret — screenshots
  still require operator approval, but approve only the device label
  you expect. ML Kit ships under custom Google terms (not an
  SPDX-listed license): human review is open before sharing the APK
  (see `THIRD_PARTY_NOTICES.md` review item 7).
- **Minimal session UI.** Session create/select, sampling interval
  chips, capture status, and an upload button. No camera switcher UI
  polish, no review/confirm flows, no design-system pass, no
  stationary-mode extras (exposure lock, timer, Bluetooth shutter).
- **One package per frame, poll-only feed.** Each sampled still is a
  full validate + stage round trip; sustained-rate behavior is
  untested. Core-side discovery is polling
  (`GET /v1/packages?session_id=`); there is no push channel and no
  Core ingestion worker consumes the feed yet.
- **Ordering and gaps.** Consumers must order by observation
  `captured_at`, never by arrival. There are no gap markers: delayed,
  dropped, or unsent frames all look like absence, and absence must be
  read as unknown. Local gap logs exist on the phone but are not
  transported.
- **No source registry.** Session ids isolate sessions, but the phone
  and a second source (e.g. the MUSTOOL microscope) on the same
  session are not yet distinguishable by a `source_id` — only by
  filenames and capture metadata.
- **Staged is not confirmed.** Frame packages land `unreviewed` like
  any other upload; nothing auto-promotes, and the receiver still has
  no path to `memory.jsonl`.
- **SDK posture.** Compiled against API 37, targeting API 36 until
  target-37 behavior changes are verified on hardware.
- **Verification gaps.** The receiver suite (90/90, including QR
  encoder, payload, approval, and CLI tests) passed over live loopback
  in this session, but real-LAN binds were not exercised here; re-run
  the full suite on the workstation. The 12-step physical acceptance
  checklist (install APK, pair, capture, sync, verify bytes,
  reconnect, confirm no memory mutation) is OPEN — no phone was
  attached in this session. ML Kit on-device decode of the printed QR
  shapes is unproven until that gate runs (OpenCV interop in tests is
  an oracle, not field proof).
- **Emulator UI pass (API 36 `anastylos-m2` AVD, debug APK).** Proven
  on the virtual device: install, clean launch, session create via the
  real form, in-app QR scanner opening live with no crash (this caught
  a real missing `camera-camera2` backend dependency, since fixed),
  6 genuine CameraX JPEG stills sampled at the 5 s cadence and stored
  with UTC timestamps, clean stop with frozen counters, and session +
  photo survival across force-stop/cold-start — zero `FATAL EXCEPTION`
  lines in logcat throughout. Not proven by the emulator: CameraX
  Preview rendering shows static block garbage in both PreviewViews
  (session + scanner) while the still-capture path works — a
  SwiftShader/ANGLE virtual-camera artifact, with the pre-permission
  screen rendering clean as control; viewfinder rendering stays a
  physical-device gate. Screenshots live under
  `.toolchain/emu-shots/` (gitignored toolchain output, not evidence).

## Platform and browser constraints

- **Live camera needs a secure context** (HTTPS or `http://localhost`)
  and a browser with `getUserMedia`: current Chrome/Edge, Firefox or
  Safari. Over plain-HTTP LAN hosting the app falls back to file import
  with an explanation; export and hashing keep working because SHA-256 is
  dependency-free (`app/src/lib/sha256.ts`).
- **Tap-to-focus, torch and multi-camera switching are best-effort.**
  They probe `getCapabilities()` and hide or report when unsupported;
  desktop browsers typically lack all three, phones vary by model.
- **HEIC originals** (iOS default) are stored and exported byte-identical,
  but in-app thumbnails depend on the browser's HEIC decoding; where the
  browser cannot decode, the original is still preserved and exported.
- **Installability** needs a standards-serving host (correct MIME types,
  HTTPS outside localhost). `vite preview` and any static host qualify.
- **Storage quotas** are device- and browser-managed. Writes that exceed
  quota fail loudly and nothing is half-saved; very large sessions should
  be exported and trimmed by the operator.

## Data and workflow simplifications

- One operator, one device: no user accounts, no conflict resolution, no
  merge of packages from two phones (the validator does reject duplicate
  ids within a package).
- Sessions, observations and photos are edited on one screen each; there
  is no bulk edit, search, or filtering beyond per-session lists.
- Crop previews are rectangles only (no rotation, no perspective fix).
- The service worker caches the app shell for offline use but performs
  no background sync; an update requires loading the app once while
  online.
- Timestamps use the device clock; skewed clocks produce skewed
  `captured_at` values (validation checks format, not truth).

## Verification gaps

- The vitest suite covers schemas, storage on both backends,
  ZIP round-trips, export planning/building, CSV, photo intake, SHA-256
  vectors, package structure, and the sync client (URL policy, pairing
  store, protocol mapping, marking rules) with a stubbed workstation;
  the Python leg of the round trip runs only where an interpreter exists.
- The Python unittest suites (validator + receiver) are stdlib-only;
  run them on a machine with Python 3.12 — see `README.md`. The receiver
  suite drives real loopback HTTP but never TLS or a real LAN.
- Camera, installability and offline-reload behavior were verified by
  code inspection, capability probing and build checks, not on physical
  devices in this session; the manual checklist in `README.md` covers
  what to confirm on a phone. The sync TLS + phone-trust path additionally
  needs the hardware checklist in `receiver/README.md` Section 3.
