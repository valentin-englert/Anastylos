# Future pairing/sync protocol (design only — NOT implemented)

This document designs how ANASTYLOS CAPTURE will one day hand evidence to
ANASTYLOS Core over a local network. Nothing here is implemented: this
version moves evidence exclusively through exported ZIP files validated by
`python/anastylos_capture_validate.py`, and the app labels sync
"unavailable". No upload server, endpoint or credential exists in this
iteration.

## Goals

1. Move validated evidence packages from phone to Core without removable
   media or cloud accounts.
2. Work fully offline on a lab LAN; assume no internet and no particular
   cloud provider.
3. Keep Core's guarantees: append-only memory, no invented identity,
   human review before confirmation.
4. Never expose an unauthenticated upload surface.

## Non-goals

- Cloud sync, accounts, push notifications.
- Remote control of the phone from Core.
- Streaming raw camera video; only packaged evidence moves.

## Proposed design: local pairing + package push

### Roles

- **Capture (phone PWA):** holds observations and builds signed export
  packages exactly as today.
- **Core agent (workstation):** runs the existing Python toolchain plus a
  small pairing listener on the LAN. It validates every package with the
  present validator before staging anything.

### Pairing (one time, per workstation)

1. The Core agent shows a pairing code (6 words from a fixed wordlist,
   ~80 bits) and its LAN address + self-signed certificate fingerprint.
2. The operator types the code into Capture's Sync screen (manual entry —
   no QR dependency, though QR may encode the same payload later).
3. Both sides run SPAKE2 (or CPace) over the code to derive a session key
   without ever transmitting the code; the phone pins the workstation
   certificate fingerprint shown alongside the code.
4. The phone stores `{ workstation_id, address, pinned_fingerprint,
   pairing_key }` in IndexedDB; the workstation stores the counterpart in
   its local config. Either side can unpair (delete the keys) at any time.

No account, no cloud rendezvous, no QR-or-nothing flow.

### Sync session

1. Operator taps "Sync" and picks pending observations (same selection UX
   as export). Capture builds the identical ZIP bytes as a file export.
2. Capture opens TLS to the pinned workstation (certificate must match
   the pin; anything else aborts), authenticates with the pairing key
   (mutual challenge-response), and uploads the package with its SHA-256.
3. The workstation re-validates with `anastylos_capture_validate.py`
   semantics, stages into an empty directory, and replies with the
   per-observation acceptance report.
4. Capture marks uploaded observations (`export_history` gains a
   `{ package_id, exported_at, via: 'sync' }` entry) only after the
   workstation confirms validation. A failed upload changes nothing.
5. The workstation operator reviews staged packages before anything
   touches `memory.jsonl` — sync stages evidence, it never appends events.

### Security properties

- Pairing code is single-use, short-lived (5 minutes), and rate-limited
  (5 attempts then lockout).
- All traffic is TLS 1.3 to a pinned certificate; no plaintext fallback.
- The workstation listener binds to a LAN interface chosen by the
  operator, never `0.0.0.0` by default, and refuses unauthenticated
  requests with no information leak.
- Packages are content-addressed (SHA-256) end to end; the phone verifies
  the workstation's acceptance report references the uploaded hash.
- No API secrets in browser code: the pairing key is user entropy
  established at pairing time, not a shipped credential.

### Failure handling

- Interrupted uploads resume by whole package (packages are small;
  chunked resume is explicitly out of scope).
- Duplicate delivery is idempotent: the workstation keys staged imports
  by `package_id` and ignores repeats.
- Clock skew is tolerated: `exported_at` is informational; acceptance
  ordering uses validation time on the workstation.

## Migration path

When this protocol is built, the file-export path stays: "download ZIP"
remains the offline fallback and the format both transports share. The
validator is already transport-agnostic — sync adoption means adding a
listener and a pairing screen, not a new format.
