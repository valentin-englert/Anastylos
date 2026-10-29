# CORE-CONTROL Wave 1 — evidence report

Lane: CORE-CONTROL. Worktree: `C:/Users/HP/Documents/Anastylos-agentcam-core`,
branch `feat/agentcam-core`. Contract G0 read-only:
`capture-companion/control/contracts/`.

## Baseline (2026-10-29, pre-implementation)

- Candidate SHA: `9f4a496d74350f19012df577bc5278da66babcf7` (clean tree,
  verified `git status` / `git rev-parse HEAD` / `git worktree list`).
- Base: parent `f98badb3` main base per dispatch; worktree/branch match dispatch.
- Contract SHA: `ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663`
  (`CONTRACT.json` field match + all 13 manifest files byte/hash OK,
  script `/tmp/verify_contract.py`).
- Tool versions: `C:\Users\HP\AppData\Local\Programs\Python\Python312\python.exe`
  Python 3.12.10; stdlib `sqlite3.sqlite_version` 3.49.1.
- Receiver suite (`capture-companion/receiver`,
  `python -B -m unittest discover -s tests`): 90 tests, OK (3 skipped),
  exit 0. Log: `C:\tmp\receiver-baseline.log`.
- Capture validator (`capture-companion/python`, same unittest cmd): 22 tests,
  OK, exit 0. Log: `C:\tmp\validator-baseline.log`.
- Compliance (repo root, `python -B tools/compliance/check.py`): 0 errors,
  1 warning (no .venv), 10 open reviews, exit 0.
  Log: `C:\tmp\compliance-baseline.log`.
- Blueprint read (read-only): `IMPLEMENTATION-DEPENDENCIES.md` (ownership:
  receiver + new tools/capture-control; no Android/schema/memory/CAD/global
  deps), `MCP-AND-TRANSPORT-CONTRACTS.md` + frozen resolutions (journal,
  CAS, idempotency key, receipts), `EVALUATION-HARNESS.md`,
  `END-TO-END-ACCEPTANCE.md` (H0–H18, profile `guidance-hypothesis-1`).
- Evidence hashes: contract manifest all-OK (see verify script output);
  raw logs retained under `C:\tmp\*-baseline.log` (scratch, not repo).

## First measurable failing capability (Wave-1a)

Durable transactional control journal + CAS/idempotency: MISSING.

- `rg 'sqlite|journal|control|guided|CAS|idempoten capture-companion/receiver'`
  returns no matches; receiver stores only JSON (`config/pairing/tokens/index`)
  with no SQLite, no `(principal,session,operation_id,operation_kind)`
  idempotency, no `expected_session_revision`/`expected_request_revision` CAS.
- Frozen vectors not consumed: `control-request-valid.json` (valid ViewRequest),
  `control-negative.json` (8 schema/semantic/authz cases),
  `control-transitions.json` (17 reducer cases incl. `cas-revision-conflict`,
  `dedupe-same-operation`, `dedupe-different-body`) have no receiver-side
  executable check. Result: FAIL (capability absent, 0/25 vector cases green
  via receiver).
- Wire fixtures for ANDROID: none published in receiver tests (Frozen
  Resolutions 5 handoff missing). Result: FAIL.
- MCP SDK: choice unrecorded at baseline; recorded in
  `tools/capture-control/README.md` as `mcp==2.3.0` (MIT, stdio),
  install pending CONTRACT-OWNER approval. Result: PASS (recorded).

## Wave-1 exit criteria mapping

- Journal + CAS/idempotency green on frozen vectors: PASS (Wave-1a subset;
  receipt/sidecar/consent/attempt transitions NOT_TESTED, see below).
- MCP SDK choice recorded with pin: PASS (recorded, not installed).
- Wire fixtures for ANDROID: PASS (5 files, contract-tested).
- Contract-tested endpoint, no phone: PASS (journal library + fixtures;
  guided HTTP routes NOT_TESTED).
- Physical gates (H8/H9/H11/H18 phone/Muse LAN): PENDING_EVIDENCE (no hardware).

## Wave-1a implementation (2026-10-29)

New: `capture-companion/receiver/anastylos_control_journal.py`
(stdlib sqlite3, WAL + FULL sync, single writer, integrity check,
contract-SHA bind). Sessions open DISARMED (rev 1, lease null/0,
`stale` capabilities); requests create/revise/cancel with dual CAS;
idempotency `(principal, session_id, operation_id, operation_kind)`
with canonical-body compare, replay-before-CAS; event_id dedupe
(conflict → `idempotency_conflict`: frozen `Error.code` has no
`event_conflict`); bounded change cursor; atomic finalize sealing
`(request_id, latest_revision)` with `manifest_sha256` over canonical
bytes excluding itself. Structural + semantic ViewRequest checks
(unknown keys, revision ≥ 1, proper-rotation det +1, Tz < 0,
expiry ordering, ordered ranges/ROI, strict JSON: no NaN/dupes,
depth ≤ 16, 64 KiB cap).

Verification:

- `capture-companion/receiver`: `python -B -m unittest discover -s tests`
  → 115 tests OK (3 pre-existing skips), exit 0
  (baseline 90 + new 25). Log: `C:\tmp\receiver-wave1a.log`.
- New `tests/test_control_journal.py`: 25 tests OK — valid vector,
  8/8 negative cases, CAS conflict + no-op, dedupe replay/conflict,
  kind-scoped keys, reopen durability, 20-queue bound, cursor
  round-trip, seal + manifest recompute + single-winner, delivery
  mapping, event dedupe, 5/5 fixtures revalidated.
- Root: `python -B tools/compliance/check.py` → 0 errors, 1 warning,
  10 open reviews (unchanged), exit 0.
  Log: `C:\tmp\compliance-wave1a.log`.
- Frozen vectors green via journal: `control-request-valid` 1/1,
  `control-negative` 8/8, `control-transitions` 4/17 PASS +
  2 PARTIAL. PASS: `cas-revision-conflict`, `dedupe-same-operation`,
  `dedupe-different-body`, `attempt-delivery-mapping`. PARTIAL:
  `event-out-of-order-gap` (cursor mechanics + snapshot path only;
  server compaction absent), `concurrent-requests-one-active`
  (20-queue bound only; accept-time one-active check deferred).
  Remaining 11 need consent/attempts/sidecar/receipts/leases/phone
  (Wave-1b): NOT_TESTED, no synthetic PASS claimed.

Per-gate: journal/CAS/idempotency PASS; MCP choice PASS (recorded);
fixtures PASS; guided HTTP routes NOT_TESTED; consent/attempts/
sidecar/receipts/leases NOT_TESTED; H0–H18 physical PENDING_EVIDENCE
(hardware: none attached); security/provenance: no regression
(additive store, legacy routes untouched, no new dependency).

Rollback: delete `control.db*` + revert owned files; legacy JSON
staging never touched, old upload unaffected. No migration.

## Wave-1b implementation (2026-10-29)

Journal: consent/grants (`min()` effective limit, re-consent
increments, disarm cancels session + queued), claim (creating
principal only), unconditional operator revoke, attempt reservation
with exposure cap, immutable asset binding, guided package join with
deterministic late classification (revoke > cancel > expire >
supersede), sidecar intake (envelope + linkage only, revision CAS,
same-attempt supersedes, evidence immutability), validator-report
qualification through an injectable seam (default unavailable:
`package_only` forever), lease renewal (grant/request bounded),
ephemeral status (guidance mirrors request), resume (always
DISARMED), multi-revision sealing with qualified receipts, plus a
Wave-1a → Wave-1b store migration (additive columns/tables, tested).

HTTP (`anastylos_guided_routes.py` + `anastylos_guided_auth.py` +
`anastylos_validator_seam.py`, thin hooks in the receiver): 24
guided endpoints incl. enroll exchange, capabilities, pending
sessions, consent, attempts, binding, guided ZIP join (validator +
manifest join: granted-session-only, bound observation/image),
sidecar, lease, claim, finalize, resume, re-hashed asset reads,
capture-set; six operator CLI commands. Legacy tokens 401 on guided
routes except enroll; guided tokens 401 on legacy routes; scoped
failures share one generic 404. Error mapping: 400/409/410/429/404/
401 documented in the receiver README.

MCP (`tools/capture-control/`): stdlib `control_client.py` (CA or
SPKI pin, loopback-plaintext guard, asset re-hash), `handlers.py`
(nine narrow `capture.*` tools; image bytes need `include_images`
plus `ANASTYLOS_PROVIDER_IMAGES=authorized`), `adapter.py` (official
`mcp` SDK stdio binding, nine tools, no filesystem/shell/provider
tool), `requirements-mcp.txt` pin record. SDK install + smoke stay
BLOCKED on CONTRACT-OWNER manifest/SPDX approval (no ad-hoc pip).

Verification:

- `capture-companion/receiver`: `python -B -m unittest discover -s tests`
  → 158 tests OK (3 pre-existing skips), exit 0
  (baseline 90 + journal 25 + pipeline 34 + routes 9).
  Log: `C:\tmp\receiver-wave1b.log`.
- `tools/capture-control`: `python -B -m unittest discover -s tests`
  → 13 tests OK (1 skip: SDK binding, reason recorded), exit 0.
  Log: `C:\tmp\cc-test1.log`.
- Root: `python -B tools/compliance/check.py` → 0 errors, 1 warning,
  10 open reviews (unchanged), exit 0.
  Log: `C:\tmp\compliance-wave1b.log`.
- Frozen vectors via journal + routes: `control-request-valid` 1/1,
  `control-negative` 8/8, `control-transitions` 17/17 server-green
  (cancel/expire shutter-latch halves are phone-side mechanics
  proven by the journal race ordering; `qualified` promotion proven
  with synthetic validator reports, live reports await SPATIAL).

Per-gate: journal/pipeline PASS; guided HTTP PASS (loopback); MCP
handlers/client PASS, SDK live smoke BLOCKED (CONTRACT-OWNER
approval); fixtures PASS (9 files); H0–H18 physical
PENDING_EVIDENCE (no hardware); security/provenance: no regression
(additive, legacy untouched, no new dependency installed).

Rollback: stop server, delete `control.db*` + `guided-*.json`, revert
owned files; legacy JSON staging and uploads unaffected. The journal
migration is forward-additive; old code ignores new columns/tables.
