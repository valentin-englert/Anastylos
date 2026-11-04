# AGENTS.md — agent operating contract

> **Moved:** modules that are not part of the working pipeline, including the recognition readers and the microscopy work, now live in [`not-in-pipeline/`](not-in-pipeline/README.md). Older paths in this document refer to that folder.

## Mission

ANASTYLOS is an evidence-backed physical disassembly and reassembly
assistance system: calibrated photo evidence, an append-only memory of
removals, specialized part recognizers, and dependency analysis that
proposes safe reassembly orders. Every claim carries provenance;
uncertain results stay `ambiguous`/`unknown` until a human operator
confirms them. Your job is to extend this system without breaking its
evidence guarantees, contracts, or documentation.

## Reading order (read before acting)

1. This file (rules of engagement).
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — technical source of truth:
   status index, contracts, coordinates, dependencies, limits.
3. The `README.md` of your assigned module — CLI, schemas, eval, limits.
4. [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) before touching
   dependencies or adding anything with a license.

Never assume conversational context. A fresh agent must be able to do
your task from these documents plus the code alone — and so must you.

## Repository map and module ownership

- `calibration/` — sheets, ChArUco, `calibration-config.json`.
  `change-detection/` — before/after `result.json`. `assembly-memory/`
  — append-only `memory.jsonl`, queries.
- Recognition readers (independent owners): `resistor-reader/`,
  `smd-reader/`, `pcb-ocr/`, `component-markings/`, `screw-reader/`,
  `screw-measurement/`, `washer-spacer/`, `polarity-reader/`,
  `component-fingerprints/`, `connector-matching/`, `tray-counter/`,
  `missing-parts/`.
- `cable-routing/` — cable paths + read-only assembly-order graph.
- `custody-ledger/` — video custody observer and linker;
  `tools/session/`, `tools/guide/`, `tools/reassembly/` — session runs
  into memory, guide packages, return checks.
- `capture-companion/` — offline PWA (`app/`) + stdlib validator
  (`python/`); contract in `SCHEMA.md`.
- `tools/compliance/` — SPDX inventory + checker.
- Root docs: `README.md` (product entry), `ARCHITECTURE.md` (source
  of truth), this file (agent contract).

Work only within your assigned module or task scope. Do not make
unrelated changes. Cross-module work needs explicit task authorization.

## Discover actual state before acting

- Never invent implementation status. Read the code, the module
  README, the tests, and the `ARCHITECTURE.md` status index; believe
  files, not chat history.
- Check what exists: entry-point CLIs, `tests/`, fixtures,
  `eval` harnesses. Synthetic-only validation is the norm — never
  present it as hardware validation.
- Inspect Git branch and worktree status before edits
  (`git status`, `git worktree list`, `git rev-parse HEAD`).

## Branch and worktree safety

- Work exclusively in your assigned worktree and branch. Never modify
  other worktrees.
- Never commit, push, merge, rebase, reset, or delete branches or
  worktrees without explicit task authorization.
- Never merge, rebase, or push to `main` unless the task says so
  explicitly.

## Rules for parallel agents

- One agent owns one module or task scope at a time; do not edit
  files another agent owns.
- Coordinate through versioned contracts (`result.json`,
  `memory.jsonl`, `SCHEMA.md`), never through uncommitted local edits.
- Stabilize shared contracts in `ARCHITECTURE.md` before fanning out
  consumers; do not change a contract and its consumers in one blind edit.

## Evidence and provenance invariants

- Preserve existing evidence formats and data contracts (`result.json`,
  `memory.jsonl`, capture packages, calibration JSON). No silent
  schema changes.
- Never silently change coordinate or measurement semantics
  (dominant pixel convention is origin top-left, +x right, +y down;
  mm only with cited calibration — see `ARCHITECTURE.md` for the
  per-kind inventory). A semantic change needs a migration, not an edit.
- Never promote detection candidates into confirmed findings. Only
  operator confirmation confirms (see `ARCHITECTURE.md` for the full
  inventory: tray links, `confirmed_missing`, graph edges, capture
  review states).
- Never mutate append-only memory from read-only analyzers.
  Corrections append new events with `supersedes`.
- Keep SHA-256 provenance on every evidence artifact you produce.

## Testing and verification

- Run the touched module's suite from its own directory:
  `python -m unittest discover -s tests`. Never hide or suppress
  failing tests — no skips, no deletions, no `-k` narrowing to green.
- A failing test you did not cause is a finding for your report, not
  a license to "fix" it by weakening the assertion.
- Report exact files modified and verification results (PASS / FAIL /
  ERROR / SKIP / NOT RUN with commands run).

## Documentation responsibilities

- Keep your module's `README.md` truthful: CLI, schemas, eval
  workflow, limits, validation scope. Fix stale claims you find in
  your scope; report ones outside it.
- Do not duplicate architecture content into module docs — link to
  `ARCHITECTURE.md`. Do not create new doc hierarchies or status
  registries; `ARCHITECTURE.md` is the single status index.
- Personal scratch notes go in `AGENTS.local.md` (gitignored), never
  in this file.

## Compliance and dependency rules

- Python: single pinned manifest (`calibration/requirements.txt`).
  Capture frontend: exact-pinned devDeps + committed lockfile. No new
  dependency without updating the SPDX inventory and notices — and
  never modify license inventories just to make a checker green.
- Never invent license identifiers or close unresolved compliance
  reviews. `tools/compliance/check.py` must stay at 0 errors.
- Never create a root `LICENSE` file. Project licensing is deferred.
- Python is offline by default. Existing isolated provider, capture-control,
  receiver and voice paths are documented in `ARCHITECTURE.md`; this is not
  permission to add another network boundary. The PWA adds no third-party
  runtime code and no telemetry.

## Forbidden operations

- Touching other worktrees; merging/rebasing/pushing without task
  authorization.
- `git add -f` to force-track ignored files; hiding user files via
  ignore rules, stashes, or deletions.
- Rewriting history, mass deletion, module moves/renames, new build
  systems, test-framework rewrites, abstraction frameworks.
- Installing tools/dependencies unless absolutely necessary.

## Definition of Done

- Change is minimal, in-scope, and behavior-preserving outside its
  stated purpose.
- Touched module suite passes (or failures are reported, not hidden);
  compliance check has 0 errors.
- Contracts and coordinate semantics preserved; docs updated where
  the change makes them stale.
- Report lists: exact files modified, verification commands +
  results, known risks, follow-ups.

## Known architectural limitations

- Hardware evidence is bounded per capability: phone capture/transfer,
  development-video processing and one real voice question round have
  recorded evidence. See `ARCHITECTURE.md` and the current `HANDOFF.md`;
  none establishes general recognition accuracy or full MVP acceptance.
- `tools/agent/` and `tools/session-ui/` orchestrate product flows. The
  specialized recognizers remain independent experiments and do not write
  findings directly to memory.
- Capture sync, guided requests, Muse dialogue and Muse/SAM drawing jobs
  exist. Physical capture-to-follow-up acceptance and a real three-assembly
  explosion remain pending. Unimplemented provider seams stay unimplemented.
- Calibration needs native Cairo/poppler on Windows for 4 of 13 kit
  tests; `pixels_per_mm` vs `px_per_mm` spellings differ by module.

## Current priority

Functional product MVP: document disassembly, let Muse obtain missing
information, build image-derived explosion views, and help reassembly.
Use the current `HANDOFF.md` for the active source and next milestone;
`ARCHITECTURE.md` remains the status/contract index. Physical acceptance
and metrology remain explicit gates, never inferred from offline tests.
