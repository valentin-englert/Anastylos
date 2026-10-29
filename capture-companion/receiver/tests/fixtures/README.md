# Guided wire fixtures (synthetic, CORE → ANDROID handoff)

Contract G0 `ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663`,
frozen vectors under `capture-companion/control/contracts/vectors/`.
ANDROID consumes these files read-only; CORE regenerates them on
contract rebase. All IDs, timestamps, and hashes are synthetic
examples produced by driving the real `ControlJournal` with a fixed
clock — never live captures.

Files: `guided-wire-open-session.json` (OpenInput → Session),
`guided-wire-request-view.json` (RequestInput → `{request,
session_revision}`), `guided-wire-source-event.json` (Event →
EventAck), `guided-wire-changes.json` (cursor `0` → Changes page),
`guided-wire-finalize.json` (MutationInput → sealed CaptureSet),
`guided-wire-consent.json` (ConsentInput → ACTIVE Session),
`guided-wire-attempt.json` (AttemptRegisterInput → AttemptAck),
`guided-wire-binding.json` (AttemptAssetBinding → ack),
`guided-wire-sidecar-pending.json` (SidecarUpload →
`SPATIAL_PENDING` delivery).

Canonical bytes (idempotency + `manifest_sha256`): UTF-8 JSON, sorted
keys, compact separators, `ensure_ascii=False`, no NaN/Infinity.
`manifest_sha256` hashes the CaptureSet *excluding* itself.
Idempotency key is `(principal, session_id, operation_id,
operation_kind)`; open_session uses `session_id ""`.
Cursors are last-seen `change_seq` strings (`0` = from start).

Regenerate: `python /tmp/gen_fixtures.py` (scratch, deterministic).
`test_control_journal.py::WireFixtureTests` revalidates every file.
