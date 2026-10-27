# Capture → Core sync protocol (v1, implemented)

How ANASTYLOS CAPTURE hands evidence to a Windows workstation over a
private LAN: the PWA builds the identical ZIP bytes a file export would,
then pushes them over authenticated TLS to the workstation receiver
(`receiver/anastylos_capture_receiver.py`), which re-validates with the
existing validator and stages accepted evidence for operator review.

File export stays the offline fallback: both transports share the byte
format, and the validator is transport-agnostic.

## Goals (unchanged)

1. Move validated evidence packages from phone to workstation without
   removable media or cloud accounts.
2. Work fully offline on a lab LAN; assume no internet.
3. Keep Core's guarantees: append-only memory, no invented identity,
   human review before confirmation — sync stages evidence, it never
   appends events.
4. Never expose an unauthenticated upload surface.

## Non-goals (unchanged)

- Cloud sync, accounts, push notifications.
- Remote control of the phone from the workstation.
- Streaming raw camera video; only packaged evidence moves. (In-session
  frame packages, below, are stills in the existing package format —
  they are not a video stream and do not reopen this non-goal.)

## Roles

- **Capture (phone PWA):** holds observations and builds export packages
  exactly as for file download (`planExport` + `buildPackage`).
- **Capture (native Android, Slice A):** same contract from Kotlin
  (`planFramePackage` + `buildFramePackage` under
  `android/.../sync/`): schema v1, stored ZIP, manifest first,
  content re-hashed. The validator cannot tell the builders apart
  (cross-language golden fixtures prove it).
- **Workstation receiver:** stdlib-only Python listener on the LAN. It
  validates every upload with `anastylos_capture_validate.py` semantics
  before staging anything into an empty per-package directory.

## Pairing (one time, per phone)

1. The workstation operator runs `receiver ... init` (or `pair-code`)
   and reads a 6-word pairing code off the receiver window (single-use,
   5-minute default lifetime, 5 attempts then a 15-minute lockout).
2. The operator types the code into the PWA Sync screen (manual entry)
   together with the workstation address (`https://<lan-ip>:8443`).
3. The phone POSTs the code over TLS to `/v1/pair`. On success the
   receiver consumes the code and returns `{ workstation_id, token }`
   (a 256-bit random Bearer [REDACTED] stored hashed server-side). The phone
   stores `{ workstationId, baseUrl, token, fingerprint?, pairedAt }`
   in IndexedDB (`sync.pairing`).
4. Either side can unpair at any time: the phone deletes its keys, the
   workstation operator runs `revoke-tokens --yes`.

No account, no cloud rendezvous, no QR-or-nothing flow.

## QR pairing (native Android, M2)

The native app replaces typing with scanning, and the manual
fingerprint comparison with a QR-bootstrapped SPKI pin:

1. The operator runs `pair-code --qr --host <lan-ip> --cert cert.pem`.
   The receiver mints a single-use code and prints a versioned QR
   payload (`{"p":"anastylos-pair","v":1,"w":…,"u":…,"s":…,"c":…,"e":…}`):
   workstation id, https endpoint, SPKI SHA-256 pin, pairing code,
   expiry. The QR carries no token and expires with the code.
2. The phone scans (in-app ML Kit scanner, bundled offline model),
   strictly validates the payload (exact keys, https, pin shape,
   expiry), and shows the workstation id + endpoint + code expiry for
   review. Nothing is sent until the user confirms.
3. On confirm the phone POSTs `{pairing_code, device_label}` to
   `/v1/pair/qr` over TLS pinned to the scanned SPKI. The receiver
   consumes the code and answers 202 with a pending `request_id` —
   *no token yet*. The phone then polls
   `GET /v1/pair/qr/<request_id>` until approval.
4. The operator runs `pair-status` (device label, expiry) and
   `pair-approve <id>` (or `pair-reject <id>`). Approval mints the
   256-bit token; the next poll delivers it. The phone confirms the
   token with an authenticated `GET /v1/status` over the same pin
   (retryable without spending anything; a rejection fails closed),
   then stores `{baseUrl, workstationId, token, spkiPin}` encrypted
   (Keystore) and uses the pin for every later connection, including
   uploads.

Trust model, precisely:

- **The QR exchange authenticates the workstation's public key**
  (physical possession of the receiver window) and authorizes *one*
  code redemption. It does not authenticate the phone — that is what
  the operator's explicit approval is for. A QR screenshot is useless
  without approval, and approval shows the claimant's device label.
- **The SPKI pin is leaf-only and narrowly scoped**: it applies to
  the scanned endpoint only, alongside the platform validity check.
  Hostname verification is subsumed by the pin (whoever completes
  the handshake with the pinned key holds the private key).
- **Renewal without re-pairing iff the key is kept.** A new
  certificate over the same key reconnects transparently; a key
  change fails closed (`SpkiMismatchException`, never retried) and
  needs a fresh QR. PWA pairings (manual trust) are unaffected and
  keep working.
- **What the QR does not do:** bind the self-asserted workstation
  id to anything (the server echoes it; the pin binds the key),
  protect against a workstation that was already compromised when
  the QR was printed, or survive the operator approving the wrong
  request — `pair-status` exists so the human checks the label.

## Sync session (operator-triggered)

1. The operator taps **Sync** and picks pending observations (same
   selection idea as export). The PWA plans and builds the identical ZIP
   bytes a file export would produce.
2. The PWA POSTs the bytes to `/v1/packages` with `Authorization: Bearer
   <token>`, `X-Package-Id`, and `X-Package-Sha256`.
3. The workstation checks auth, length, and checksum, re-validates with
   the capture validator, and — only for fully valid packages — stages
   into `packages/<package_id>/` and replies with the acceptance report.
4. The PWA marks synced observations (`export_history` gains a
   `{ package_id, exported_at, via: 'sync' }` entry) only after the
   workstation confirms validation. A failed upload marks nothing: the
   evidence stays pending and retryable.
5. The workstation operator reviews staged packages (`status` command,
   staged directories) before anything touches `memory.jsonl` — which
   the receiver cannot do at all (it has no memory code path).

## In-session frame delivery (Slice A)

The same endpoint also carries automatically sampled stills from a
continuous native-camera session *while the session is active*. This
is a transport permission, not a new protocol: every frame package is
an ordinary schema-v1 package (one session, one observation per
sampled frame) uploaded with the same Bearer auth, `X-Package-Id`,
`X-Package-Sha256`, validation, staging, and idempotency rules as an
operator-triggered sync. Nothing about pairing, trust, or the review
boundary changes — staged frames wait for review like any other
package, and the receiver still cannot touch `memory.jsonl`.

What in-session delivery adds, and its limits:

- **Discovery during the session.** Staged summaries record
  `session_ids` (re-read defensively from the validated manifest),
  and `GET /v1/packages?session_id=<uuid>` filters the staged index
  to one active session. This is the read-only projection a Core
  ingestion worker polls; see `receiver/SESSION_FEED.md` for the
  consumer contract (ordering, dedupe, gap semantics).
- **Ordering is by `captured_at`, not arrival.** Frames may arrive
  delayed, retried, or out of order; manifests list observations in
  `captured_at` order and consumers must use it. There are no gap
  markers: absence of a frame is unknown, never "nothing happened".
- **One package per frame.** Simple and idempotent (deterministic
  content-derived ids make retries reuse the same `package_id`), but
  one validate + stage round trip per frame. Sustained-rate framing
  is untested; batching would be a later, separately reviewed change.
- **No new upload surface.** No streaming endpoint, no append/update
  endpoint, no schema change. The raw-video-streaming non-goal above
  is explicitly retained.

## Security properties

- TLS 1.2+ on the LAN with an operator-provided certificate; the server
  prints its certificate SHA-256 fingerprint for manual comparison.
- Pairing codes are single-use, short-lived, and rate-limited; device
  tokens are 256-bit, hashed at rest, constant-time compared, revocable.
- The listener binds the operator-chosen interface (default loopback)
  and refuses unauthenticated requests with generic messages (no
  identity leak, no pairing oracle).
- Packages are content-addressed end to end: the phone refuses to send
  without a valid id + SHA-256, the workstation verifies both, and the
  phone checks that the acceptance report references the uploaded id.
- No API secrets in browser code: the token is user entropy established
  at pairing time, not a shipped credential.

## Failure handling

- Interrupted uploads resume by whole package (length/checksum fail,
  nothing staged; retry sends the identical bytes under the same id).
- Duplicate delivery is idempotent: staged imports are keyed by
  `package_id` and repeats return the stored report without staging.
- Clock skew is tolerated: `exported_at` is informational; acceptance
  ordering uses validation time on the workstation.
- A package the validator marks `invalid` is rejected as a unit with
  its report — even if some observations look acceptable (same rule as
  the validator's `--extract`). Nothing is staged, nothing is marked.

## Design analysis: what changed since the sketch, and why

The earlier sketch in this file proposed SPAKE2/CPace PAKE plus
browser-side certificate pinning. That design was not built as drawn,
for two concrete reasons:

1. **Browsers cannot pin certificates from JavaScript.** TLS validation
   happens inside the browser; pages and service workers never see the
   server certificate (no API exposes it to `fetch`). Any "pinning"
   implemented in PWA code would be theater, not enforcement. v1 is
   honest instead: trust comes from the browser's normal TLS validation
   (install the workstation certificate on the phone) or from the
   operator manually comparing the printed fingerprint before pairing
   (trust-on-first-use). The PWA stores the fingerprint as a display
   reminder only and says so in the UI.
2. **PAKE buys nothing here once trust is manual.** SPAKE2/CPace would
   need custom cryptography in both the PWA (no third-party runtime
   code allowed) and the stdlib-only receiver — a large, unauditable
   surface for a lab tool. The actual threat PAKE would address (a
   network attacker stealing the pairing code) is already handled: the
   code travels only inside the operator-verified TLS session, is
   single-use and short-lived, and is rate-limited with lockout. A
   pairing-code-over-verified-TLS bootstrap plus a 256-bit token is the
   standard, boring, reviewable answer — so v1 uses it.

Additional deliberate deviations: pairing codes carry 48 bits (6 words
× 8), not ~80 — ample for a single-use, 5-minute, 5-attempt code, and
stated honestly; revocation is all-or-nothing in v1; chunked resume is
still out of scope.

## Protocol reference

See `receiver/README.md` Section 5 for the endpoint table and Section 2
for certificate setup. PWA client: `app/src/lib/sync.ts`; screen:
`app/src/ui/view-sync.ts`. Native client: `android/.../sync/`
(`ReceiverClient.kt`, `FrameUploader.kt`, `TlsTrust.kt`) and
`android/.../pairing/` + `android/.../ui/pair/` for QR pairing
(`QrPayload.kt`, `QrScan.kt`, `HttpQrPairClient.kt`,
`PairViewModel.kt`, `QrPairScreen.kt`); in-session discovery
contract: `receiver/SESSION_FEED.md`. Wire format of the evidence
itself: `SCHEMA.md` (unchanged — sync reuses the export package byte
for byte).
