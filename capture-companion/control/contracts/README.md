# AgentCam/CAD frozen contract — G0

Version: `g0` (2026-10-29). Base commit: `f98badb3`.
Owner: CONTRACT-OWNER. All Wave-1 lanes consume these files **read-only**.
Any change is a new contract version (new hash) published by the
contract owner; consumers rebase explicitly. Never edit in a lane branch.

Prose specification (read-only, gitignored research dir — do not force-add):

- `C:/Users/HP/Documents/Anastylos/random/research/ANASTYLOS-AgentCam-CAD-Blueprint-01/`
- Normative docs: `MCP-AND-TRANSPORT-CONTRACTS.md`,
  `CAMERA-COMPANION-STATE-MACHINE.md`, `SPATIAL-EVIDENCE-SCHEMA.md`,
  `SPATIAL-TRACKING-DESIGN.md`, `CAD-PIPELINE.md`, `CAD-BACKEND-DECISION.md`,
  plus each file's FROZEN RESOLUTIONS addendum (addenda win on conflict).

## Frozen files

- `schemas/control.schema.json` (Draft 2020-12, 37 `$defs` incl.
  `AttemptDelivery`; Receipt has 7 statuses incl. `late_after_expire`,
  `late_after_supersede`, `late_after_revoke`; sidecar ref is
  `$id`-based).
- `schemas/spatial-evidence.schema.json` (Draft 2020-12).
- `schemas/cad-feature-model.schema.json` (Draft 2020-12; v1 kinds
  `box|cylinder|through_hole` only).
- `schemas/mcp-tools.json` (registry template; refs dangle until bundled).
- `schemas/mcp-tools.expanded.json` (G0 bundle: self-contained, all 18
  tool schemas compile, 0 dangling refs; embed `SpatialSidecar`).
- `vectors/` — 7 executable vectors (ViewRequest valid/negative ×8 cases,
  spatial valid, guidance signs ×2, control transitions ×17, CAD
  valid + invalid ×6).
- `CONTRACT.json` — file manifest with SHA-256 + validation record.

## Semantic rules (JSON Schema is necessary, not sufficient)

1. Idempotency key is `(principal, session_id, operation_id, operation_kind)`.
2. Session with `lease_expires_at: null` must carry `lease_remaining_ms: 0`.
3. Wire `guidance_state` equals the current Request status string.
4. Phone `quality.overall=accepted` is candidacy only; only a Core
   validator report + `qualified` Receipt promotes to metric-ready.
5. `ACKNOWLEDGED ⇔ qualified`; `PACKAGE_ACCEPTED/SPATIAL_PENDING ⇔
   package_only`; delivery states per state-machine frozen resolutions.
6. Basis joins must apply the canonical mapping (control `operator_declared`
   → `operator_testimony`, `measured` → `geometric_measurement`,
   `model_assumed` → `model_assumption`); never raw string equality.
7. CAD adapter field is `T_CAD_B` (no `T_D_B`); v1 ops only; suggested
   geometry = `model_assumption` + `suggested:` assumption; unobservable
   geometry is absent + listed in `validation.json`, never defaulted.
8. `max_images` counts every shutter incl. retakes/failed/late; CAD sets
   spanning grants cite every session/grant.
9. Effective consent limit = min(adapter ceiling, operator grant).
10. `finalize` is the only sealing operation; late uploads never mutate a
    sealed set.

## Validation record (G0, Wave-0 session)

- Draft 2020-12 compile + 41 instance/vector/vocabulary checks: PASS
  (ajv v8 2020 build via ajv-cli 5, Node 24).
- Guidance analytic recompute 2/2: PASS.
- MCP bundle 18/18 tool-schema compiles, 0 dangling: PASS.
- Receiver suite 90 tests OK (3 skipped): PASS (Python 3.12.10).
- Capture validator 22/22: PASS — ZIP-v1 byte compatibility preserved.
- Compliance `check.py`: 0 errors (1 warning, 10 open reviews): PASS.
