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
