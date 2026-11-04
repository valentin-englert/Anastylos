# INTEGRATION-QA lane evidence report (Wave 1)

Lane owns only `tools/agentcam-acceptance/` (harness/tests/protocol/evidence
manifests). Product source is read-only. Profile: `guidance-hypothesis-1`
(frozen). Hardware: none attached — every physical gate is PENDING_EVIDENCE.

## Frozen identifiers

- Candidate: `9f4a496d74350f19012df577bc5278da66babcf7` (contract commit;
  parent `f98badb3` main base). HEAD verified equal, tree clean.
- Worktree: `C:/Users/HP/Documents/Anastylos-agentcam-qa`, branch
  `feat/agentcam-qa` (verified via `git rev-parse HEAD`, `git worktree list`).
- Contract: `ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663`
  (`capture-companion/control/contracts/`, 13 files: README + CONTRACT.json
  + 5 schemas + 7 vectors).
- Python: `C:\Users\HP\AppData\Local\Programs\Python\Python312\python.exe`,
  3.12.10 (bare `python`/`py` are broken stubs on this machine).

## Baseline (2026-10-29, pre-harness, observed by read-only probes)

- Contract manifest: 13/13 files match CONTRACT.json bytes+sha256. OBSERVED.
- Contract-SHA derivation: `sha256` over sha256sum-style lines
  `"<file-sha256>  <path>\n"` in manifest order reproduces `ec313f7f…4663`
  exactly (6 other derivations tested, all mismatch). OBSERVED.
- Blueprint integrity: `ARTIFACT-SHA256.json`, 33/33 files match. OBSERVED.
- Compliance: `tools/compliance/check.py` exit 0, 0 errors, 1 warning,
  10 open reviews — matches the G0 validation record. OBSERVED.
- G0 validation record (CITED from CONTRACT.json, not yet re-verified by QA):
  ajv Draft 2020-12 41/41 PASS; guidance analytic 2/2; MCP bundle 18/18,
  0 dangling; receiver suite 90 OK (3 skipped); capture validator 22/22.
- Candidate contains no lane implementations (all AgentCam worktrees sit at
  the frozen base): Android/Core/Spatial/CAD product gates have no code to
  score yet. OBSERVED (`git show --stat 9f4a496d`: contracts only).

## First measurable failing capability

- `vector-replay`: 0/7 frozen vectors replayed — harness does not exist.
  Verdict: FAIL (BLOCKED on this lane's own implementation, repair
  iteration 0). Exit criterion: 7/7 vectors replayed with SHA-bound
  scorecards (`acceptance-run.json`, `evidence_class=synthetic`).
- Physical gates E1–E12 / H0–H18: PENDING_EVIDENCE (no phone, no print
  metrology, no caliper, no LAN timing rig). Emulator/mock evidence can
  never pass these gates; no synthetic PASS is claimed for them.

## Runs

### Run 1 — Wave-1 exit (2026-10-29, frozen base as candidate)

- Command: `python -B tools/agentcam-acceptance/accept.py run --candidate
  9f4a496d74350f19012df577bc5278da66babcf7 --expect-contract
  ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663 --out
  tools/agentcam-acceptance/evidence` (Python 3.12.10). Exit: 0.
- Scorecard: `evidence/acceptance-run.json` (14 PASS, 31 PENDING_EVIDENCE,
  0 FAIL/BLOCKED/NOT_TESTED).
- Replay: 7/7 vectors — control-valid 1/1, control-negative 8/8,
  control-transitions 17/17, spatial-valid 1/1, guidance-signs 2/2,
  cad-valid 1/1, cad-invalid 6/6 (`evidence_class=synthetic`).
- Bundle: 9 tools, 18/18 schemas, 513 refs, 0 dangling, 0 external,
  control source hash match. Contract manifest 13/13, SHA recompute match.
- Regression: compliance exit 0 / 0 errors; self-tests 20/20;
  memory rehash 1/1 unchanged
  (`assembly-reconstruction/fixtures/synthetic/memory.jsonl`).
- Physical: E1–E12, H0–H18 all PENDING_EVIDENCE (no hardware attached,
  candidate has no lane implementation). No synthetic PASS claimed for them.
- Repairs used: 2 (harness-internal: plain-pointer form; per-tool-schema
  ref base). Failures routed to lanes: none (no product code in candidate).
