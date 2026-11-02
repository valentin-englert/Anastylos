# Capture workstation receiver (prototype v1)

A tiny Windows-compatible HTTPS service that accepts evidence packages from
the Capture Companion PWA over a private LAN, validates every upload with
the existing capture validator, and stages accepted evidence for operator
review. Standard library only — no install beyond Python 3.12.

- Service: `anastylos_capture_receiver.py` (`http.server` + `ssl`, threads)
- Validator reused verbatim: `../python/anastylos_capture_validate.py`
- Protocol + security analysis: `../SYNC_PROTOCOL.md`
- Tests: `tests/test_receiver.py` (40 tests, real loopback HTTP + TLS)

What it never does: append to `memory.jsonl`, invent identity, confirm
anything, or expose an unauthenticated upload endpoint. Staged evidence
waits under `--data-dir` until a human reviews it.

## 1. Windows setup (reproducible)

Prerequisites: Windows 10/11, Python 3.12 (`py -3.12 --version`), and the
workstation's LAN address (PowerShell: `ipconfig`, e.g. `192.168.1.20`).
The phone and the workstation must share one private network.

```powershell
# From the repository root:
cd $HOME\Documents\Anastylos          # or wherever you cloned it

# 1) Create state, a workstation id and a first pairing code:
py -3.12 capture-companion\receiver\anastylos_capture_receiver.py --data-dir .\recv-data init

# 2) Create a TLS certificate (pick A or B below), then serve:
py -3.12 capture-companion\receiver\anastylos_capture_receiver.py --data-dir .\recv-data serve `
  --host 192.168.1.20 --port 8443 --cert .\recv-data\cert.pem --key .\recv-data\key.pem
```

On startup the receiver prints its `workstation_id`, the certificate
SHA-256 fingerprint, and whether a pairing code is active. Keep this
window visible: the operator reads the fingerprint and the pairing code
from it. Stop with Ctrl+C.

Mint a fresh single-use code at any time (invalidates the previous one):

```powershell
py -3.12 capture-companion\receiver\anastylos_capture_receiver.py --data-dir .\recv-data pair-code
```

Review what arrived (read-only; staging never touches memory):

```powershell
py -3.12 capture-companion\receiver\anastylos_capture_receiver.py --data-dir .\recv-data status
py -3.12 capture-companion\receiver\anastylos_capture_receiver.py --data-dir .\recv-data status --json
```

Revoke every paired phone (after a lost device, or to start over):

```powershell
py -3.12 capture-companion\receiver\anastylos_capture_receiver.py --data-dir .\recv-data revoke-tokens --yes
```

## 2. TLS certificates (required for LAN use)

The receiver serves the LAN over TLS only. There is no plaintext LAN
mode and no silent fallback: `--plaintext-localhost` binds loopback
only and refuses any other address.

### Option A — self-signed certificate with OpenSSL (simplest)

If `openssl` is available (Git for Windows ships it under
`C:\Program Files\Git\usr\bin`):

```powershell
$ip = "192.168.1.20"   # the workstation's LAN address
& "C:\Program Files\Git\usr\bin\openssl.exe" req -x509 -newkey rsa:2048 -nodes `
  -keyout .\recv-data\key.pem -out .\recv-data\cert.pem -days 825 -sha256 `
  -subj "/CN=anastylos-capture-receiver" `
  -addext "subjectAltName=IP:$ip,DNS:anastylos-receiver"
```

The phone will not trust this certificate automatically — that is the
point of Section 3 (compare the fingerprint by hand, or install it).

### Option B — Windows self-signed certificate (no OpenSSL)

```powershell
$ip = "192.168.1.20"
$cert = New-SelfSignedCertificate -DnsName "anastylos-receiver", $ip `
  -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(2) `
  -KeyUsage DigitalSignature, KeyEncipherment -TextExtension "2.5.29.37={text}1.3.6.1.5.5.7.3.1"
# Export certificate (public part, for the phone + the receiver):
Export-Certificate -Cert $cert -FilePath .\recv-data\cert.cer | Out-Null
certutil -encode .\recv-data\cert.cer .\recv-data\cert.pem | Out-Null
# Export the private key (stays on the workstation, protect this file):
$pw = Read-Host "Password for the key export" -AsSecureString
Export-PfxCertificate -Cert $cert -FilePath .\recv-data\key.pfx -Password $pw | Out-Null
# Convert the PFX to an unencrypted PEM key with OpenSSL (Git for Windows):
& "C:\Program Files\Git\usr\bin\openssl.exe" pkcs12 -in .\recv-data\key.pfx `
  -out .\recv-data\key.pem -nodes -passin "pass:$([Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw)))"
Remove-Item .\recv-data\key.pfx
```

Then serve exactly as in Section 1. The receiver prints the certificate
fingerprint on every start; that string is what the phone operator
compares.

## 3. Phone access over HTTPS

Serve the PWA itself over HTTPS too (any static host, or `npm run dev`
behind an HTTPS tunnel) so the camera and the Sync screen both work.
Then, on the phone:

1. Open the PWA and go to the **Sync** tab.
2. Type the workstation address, e.g. `https://192.168.1.20:8443`.
3. Establish trust — one of:
   - **Install the certificate (recommended).** Send `cert.pem`/`cert.cer`
     to the phone, install it as a trusted CA/certificate
     (Android: Settings → Security → Install a certificate → CA
     certificate; iOS: send the file, then Settings → General → VPN &
     Device Management → install profile, and enable full trust under
     Settings → General → About → Certificate Trust Settings).
     The browser then validates TLS normally.
   - **Manual fingerprint check (lab use).** Visit
     `https://192.168.1.20:8443/v1/info` in the phone browser, open the
     certificate warning details, and compare the SHA-256 fingerprint
     character by character with the receiver window before accepting
     the exception. Only proceed on an exact match.
4. Enter the pairing code from the receiver window (single-use, expires
   after minutes). Optionally store the fingerprint as your reminder.
5. Tap **Check connection**, select pending evidence, tap **Sync**.
   Failed transfers mark nothing — retry when ready.

Honesty note: the PWA cannot pin the certificate for you. Browsers never
expose the server certificate to JavaScript, so no web page can enforce
"talk only to this fingerprint". The checkbox in the Sync screen records
that *you* compared the fingerprints; the cryptography is the TLS the
browser already validates (option 3a) or your manual comparison
(option 3b). See `../SYNC_PROTOCOL.md` for the full analysis, including
why the earlier PAKE/pinning sketch was not built as drawn.

### Native app: scan instead of typing

The native Android app pairs by QR code — no addresses, codes, or
certificates to type or install on the phone:

1. On the workstation, mint a code and print its QR:
   `receiver ... pair-code --qr --host <lan-ip> --port 8443 --cert cert.pem`.
   The QR (ASCII in the terminal, PNG at `<data-dir>/pair-qr.png` by
   default, or `--qr-out`) carries the endpoint, the SPKI pin, and the
   single-use code — never a token. It expires with the code.
2. On the phone, open Capture → **Scan the workstation QR**, review the
   workstation id, and confirm. The phone verifies the server's public
   key against the scanned pin on every connection.
3. Back on the workstation, `pair-status` shows the pending request
   (device label, expiry); `pair-approve <id>` issues the token, or
   `pair-reject <id>` deletes it. The phone polls until approval,
   then confirms the token with an authenticated status request
   before storing it.
4. If the camera cannot scan, the manual form on the session screen
   (host, port, code, fingerprint) remains as a fallback.

The QR is single-use and short-lived; a screenshot still needs the
operator's explicit approval to become a token. See
`../SYNC_PROTOCOL.md` ("QR pairing") for the trust model.

## 4. CLI reference

All commands need `--data-dir` (operator-owned state; keep it outside
the repository). Exit codes: 0 ok, 1 operational failure, 2 usage error.

| Command | Effect |
|---|---|
| `init [--ttl-minutes 5]` | Create state, workstation id, first pairing code |
| `pair-code [--ttl-minutes 5]` | Mint a fresh single-use code (invalidates the old) |
| `pair-code --qr --host H [--port P] --cert C [--qr-out PNG]` | Mint a code and print its QR pairing payload (ASCII + PNG) |
| `pair-status [--json]` | List pending QR pairing requests (tokens redacted in JSON) |
| `pair-approve <id-prefix>` | Approve one request and issue its token |
| `pair-reject <id-prefix>` | Reject (delete) one request |
| `serve --host H --port P --cert C --key K` | Run the HTTPS receiver (default `127.0.0.1:8443`) |
| `serve --plaintext-localhost` | Loopback-only HTTP for development/tests; refuses LAN binds |
| `status [--json]` | List staged packages (read-only review aid) |
| `revoke-tokens --yes` | Revoke every issued device token |

Data layout under `--data-dir`: `config.json` (workstation id),
`pairing.json` (single-use code verifier + rate-limit state),
`pairing-requests.json` (pending QR approvals; swept on access),
`tokens.json` (SHA-256 hashes of issued tokens — tokens themselves are
never stored), `index.json` (staged ledger), and
`packages/<package_id>/` (`package.zip`, `report.json`, staged members).

## 5. HTTP API (v1, JSON unless noted)

| Method + path | Auth | Meaning |
|---|---|---|
| `GET /v1/info` | none | `{service, version}` reachability probe; leaks no identity |
| `POST /v1/pair` | pairing code | `{pairing_code}` → `{workstation_id, token}`; 403 on failure, 429 when locked |
| `POST /v1/pair/qr` | pairing code | `{pairing_code, device_label}` → 202 `{status:pending, request_id, expires_at}`; consumes the code without issuing a token; 400 bad label/body, 403 consumed/unknown, 429 when locked |
| `GET /v1/pair/qr/<request_id>` | request id (poll capability) | 200 `{status:pending}` or `{status:approved, workstation_id, token}`; one 404 for unknown/rejected/expired/malformed (no state oracle) |
| `GET /v1/status` | Bearer | `{workstation_id, staged_packages, server_time}` |
| `POST /v1/packages` | Bearer | Raw ZIP body + `X-Package-Id` + `X-Package-Sha256` → 201 `{duplicate:false, report}`, 200 `{duplicate:true, report}` on redelivery, 400 bad metadata/checksum, 413 over cap, 422 invalid evidence (nothing staged) |
| `GET /v1/packages` | Bearer | Staged summaries for the operator |
| `GET /v1/packages?session_id=<uuid>` | Bearer | Same summaries filtered to one session (single UUID; 400 otherwise, empty list when unknown). Read-only in-session projection — consumer contract in `SESSION_FEED.md` |
| `GET /v1/packages/<id>` | Bearer | Stored acceptance report, or 404 |

Authentication failures answer 401 with `WWW-Authenticate: Bearer` and a
generic message. Pairing failures answer one generic 403 (no oracle for
wrong vs expired vs consumed codes). Uploads resume by whole package:
any interruption fails the checksum/length check, stages nothing, and
the phone retries the identical bytes under the same `package_id`, which
the receiver deduplicates.

## 6. Security properties

- TLS 1.2+ on the LAN with an operator-provided certificate; the
  fingerprint is printed for manual comparison. No plaintext LAN mode.
- Pairing codes: 6 words from a 256-word list (48 bits), single-use,
  5-minute default lifetime, 5 attempts then a 15-minute lockout.
  Online guessing is infeasible; the code travels only inside TLS.
- Device tokens: 256-bit random, stored as SHA-256 hashes, compared in
  constant time, revocable with `revoke-tokens --yes`.
- QR pairing is two-phase: redeeming the scanned code creates a
  *pending* request (128-bit id, operator-visible device label) and
  issues nothing; only the operator's explicit `pair-approve` mints
  the token. The QR carries the code and the SPKI pin, never a token.
  Polls cannot distinguish rejected from expired (one 404), and one
  code pool + lockout is shared with legacy `/v1/pair`.
- Every upload is re-validated with the capture validator before
  anything is staged; invalid evidence is reported, never stored.
- Idempotency: staged imports are keyed by `package_id`; repeats return
  the stored report and stage nothing twice (parallel-race safe: the
  staging target refuses non-empty directories).
- The listener binds the operator-chosen interface (default loopback,
  never a forced `0.0.0.0`).

## 7. Limitations (prototype)

- No certificate generation built in: Python's standard library can
  *serve* TLS but not mint certificates — the operator creates them
  (Section 2). No ACME/Let's Encrypt: this is a lab-LAN tool.
- Trust is manual (fingerprint comparison) or via a phone-installed CA.
  There is no browser-enforceable certificate pinning, and this
  prototype does not claim any.
- Whole-package uploads only (no chunked resume); the package cap is the
  validator's 512 MiB.
- Only fully valid packages are staged. A package the validator marks
  `invalid` — even with some acceptable observations — is reported and
  rejected as a unit (same rule as the validator's `--extract`).
- One operator, one workstation: no user accounts, no per-device token
  labels beyond timestamps, revocation is all-or-nothing.
- Unbounded threading (`ThreadingHTTPServer`), plain-JSON state files,
  no log rotation: fine for one phone on a lab LAN, not a public
  service. Bind it to a private interface and keep the firewall closed.
- The PWA keeps the pairing token in IndexedDB (`sync.pairing`): anyone
  with the unlocked phone can sync. Unpair + `revoke-tokens` on loss.
- Not yet validated on physical phones or real LANs in this session —
  run the checklist in Section 3 on hardware before relying on it.

## 8. Guided control (Wave 1b, wired)

`anastylos_control_journal.py` is the durable store for guided state
(frozen G0 contract `ec313f7f...`, read-only under
`../control/contracts/`). Standard library only (`sqlite3`, WAL,
single bounded writer, integrity check on open). It is additive: the
JSON staging files above are untouched and legacy upload/review
behavior is unchanged.

Covered: sessions, request create/revise/cancel (dual CAS),
`(principal, session_id, operation_id, operation_kind)` idempotency,
consent/grants with effective-limit `min()`, claim/revoke, attempt
reservation with exposure cap, immutable asset binding, guided
package join with late-receipt classification, sidecar intake with
revision CAS + supersedes chains, validator-report qualification via
an injectable seam (unavailable by default: `package_only`, never
`qualified`), lease renewal, ephemeral status, resume, multi-revision
sealing, change cursor, and structural + semantic input checks.

HTTP: `anastylos_guided_routes.py` serves the guided table below on
the existing TLS listener; `anastylos_guided_auth.py` holds the
enrollment registry + hashed scoped tokens (`source`,
`adapter_bootstrap`, `adapter_session`, `spatial`, `reader`).
Legacy tokens get 401 on guided routes except the single
`POST /v1/guided/enroll` exchange; guided tokens get 401 on legacy
routes. Authenticated scope failures answer one generic 404.

| Method + path | Auth | Meaning |
|---|---|---|
| `POST /v1/guided/enroll` | legacy Bearer (sole exception) | enrolled device → `{source_id, token, expires_at}` |
| `POST /v1/guided/sources/{id}/capabilities` | source | publish bounded capabilities |
| `GET /v1/guided/sources/{id}/pending-sessions` | source | ≤20 DISARMED sessions |
| `GET /v1/guided/capabilities?source_id=` | scoped | published caps or stale-unpublished stub |
| `POST /v1/guided/sessions` | adapter bootstrap | 201 DISARMED session |
| `GET /v1/guided/sessions/{id}` | scoped | snapshot (requests, status, receipts) |
| `POST /v1/guided/sessions/{id}/requests` | adapter session | 201 `{request, session_revision}` |
| `POST /v1/guided/requests/{id}/revise` | adapter session | new immutable revision (dual CAS) |
| `POST /v1/guided/requests/{id}/cancel` | adapter session | cancel one request |
| `GET /v1/guided/sessions/{id}/changes?after=` | source/adapter | ≤100 changes + cursor |
| `POST /v1/guided/sessions/{id}/events` | source | source event ack |
| `POST /v1/guided/sessions/{id}/status` | source | ephemeral status (guidance mirrors request) |
| `POST /v1/guided/sessions/{id}/consent` | source | arm (grant) / disarm (cancel) |
| `POST /v1/guided/sessions/{id}/attempts` | source | 201 exposure reservation |
| `POST /v1/guided/attempts/{id}/asset-binding` | source | immutable ID/hash binding |
| `POST /v1/packages` + `X-Guided-*` | source | validated join → 201/200 `{report, receipt}` |
| `POST /v1/guided/attempts/{id}/spatial` | source/spatial | receipt or `SPATIAL_PENDING` |
| `POST /v1/guided/sessions/{id}/lease` | adapter session | renewed lease ≤ grant/request TTL |
| `POST /v1/guided/grants/{id}/claim` | adapter bootstrap | creating adapter → session token |
| `POST /v1/guided/sessions/{id}/finalize` | adapter session | seal one set revision |
| `POST /v1/guided/sessions/{id}/resume` | adapter session | recover (always DISARMED) |
| `GET /v1/guided/sessions/{id}/assets/{image}` | adapter/spatial/reader | re-hashed image bytes |
| `GET /v1/guided/sessions/{id}/capture-set` | scoped | latest sealed revision |

Error mapping: `invalid_input` → 400, `revision_conflict` /
`idempotency_conflict` / `unarmed` → 409, `expired` → 410, `busy` →
429, `unauthorized` / `not_found` → generic 404, missing Bearer →
401. Control bodies ≤ 64 KiB, sidecar uploads ≤ 320 KiB.

CLI: `guided-enroll <pair-prefix>`, `guided-bootstrap --principal`,
`guided-reader <session> <spatial|reader>`, `guided-revoke-grant`,
`guided-revoke-tokens --session`, `guided-status [--json]`.

Not yet: the live spatial validator executable (SPATIAL lane owns
it; `anastylos_validator_seam.py` defines the handoff), cursor
compaction/HMAC, and the `mcp` SDK install (`tools/capture-control/`
ships the adapter against `mcp==2.3.0`; install pending
CONTRACT-OWNER manifest/SPDX approval).

Tests: `tests/test_control_journal.py` (25), `tests/test_control_pipeline.py`
(34: consent/grants/attempts/binding/receipts/sidecar/lease/status/
resume/migration + fixtures), `tests/test_guided_routes.py` (9 live
loopback incl. auth matrix + CLI). ANDROID consumes
`tests/fixtures/guided-wire-*.json` read-only (synthetic,
contract-tested); see `tests/fixtures/README.md`.

## Disassembly video upload

`anastylos_video_routes.py` adds chunked, resumable video upload on the
existing TLS listener and guided source token (additive; no frozen G0
contract file changes). Videos of a guided session are stored under
`<data>/videos/<session_id>/<video_id>/` as `meta.json` plus, once complete,
`video.mp4`.

| Route | Auth | Effect |
|---|---|---|
| `POST /v1/guided/sessions/{id}/videos` | source | declare `{sha256, size}` (≤ 4 GB) → 201 new / 200 known; `video_id` is the first 32 hex chars of the SHA-256 |
| `GET /v1/guided/sessions/{id}/videos` | source | list the session's videos with status |
| `GET /v1/guided/sessions/{id}/videos/{video_id}` | source | `status` (`receiving`, `complete`, `failed`), `chunk_count`, `received` chunk indexes |
| `PUT /v1/guided/sessions/{id}/videos/{video_id}/chunks/{n}` | source | one raw chunk, 0-based; every chunk is exactly 8 MiB except the last |

Behaviour: a repeated chunk with identical bytes is a no-op (200); different
bytes for a stored chunk are 409 `chunk_conflict`; a wrong length is 400. The
chunk that completes the set triggers assembly under a `.part` name, the
SHA-256 and size are verified, and only then is the file moved into place
atomically (`complete`). A mismatch is 409 `hash_mismatch`, the chunks are
discarded and the video is marked `failed`; declaring it again starts clean.
A phone resumes after a dropped connection by reading `received` and sending
only the missing chunks. `video-journal.jsonl` records route, status and byte
counts only, never content. Tests: `tests/test_video_routes.py`.

## Voice settings and ASR keywords

`GET /v1/guided/sessions/{id}/voice/settings` (guided source token, session bound to that source) relays the session
UI's `GET /api/dialogue/settings` and returns `agent_name`, `persona`, `wake_words`, `asr_keywords`, `mishearings`;
502 `settings_unavailable` when the session UI cannot be reached. The phone matches those wake words (with no name it
keeps "Muse" and "Meta") and sends the keywords with every transcription in the optional `X-Asr-Keywords` header
(comma separated, letters/spaces/apostrophes/hyphens, at most 6); the receiver passes each as `--keyword` to the
transcriber. Tests: `tests/test_voice_routes.py`.
