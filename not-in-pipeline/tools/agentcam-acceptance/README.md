# tools/agentcam-acceptance — Wave-1 evaluator harness

INTEGRATION-QA owned. Independent evaluator for ANASTYLOS Guided
Capture + CAD Reconstruction candidates. Product source is read-only
to this lane; failures are routed to owning lanes, never fixed here.
Project status index stays in
[`ARCHITECTURE.md`](../../ARCHITECTURE.md).

## What it does (Wave-1 scope)

Replays every frozen G0 vector from
`capture-companion/control/contracts/` (read-only) and emits a SHA-bound
scorecard (`acceptance-run.json`, profile `guidance-hypothesis-1`).
Standard library only, no network. Exit 0 unless a gate FAILs.

## Commands

From the repo root (`python` = pinned 3.12.10; record `--version`):

```text
python tools/agentcam-acceptance/accept.py run --candidate <sha> --expect-contract <sha> --out <dir>
python tools/agentcam-acceptance/accept.py verify-contract --expect-contract <sha>
python tools/agentcam-acceptance/accept.py replay [--vectors control-valid,control-negative,control-transitions,spatial-valid,guidance-signs,cad-valid,cad-invalid,mcp-bundle]
```

Module suite (from this directory):

```text
python -m unittest discover -s tests
```

`run` also accepts `--gates a,b` / `--skip-gates c` (excluded gates are
reported NOT_TESTED, never silently dropped). `--skip-gates
regression-selftests` avoids recursive suite runs inside the integration
test.

## Gates

| Gate | Basis |
|---|---|
| `run-integrity` | `--candidate` equals `git rev-parse HEAD` (stale SHA = FAIL) |
| `contract-manifest` | 13/13 files match CONTRACT.json bytes+sha256 |
| `contract-sha` | recomputed sha256 over sha256sum-style lines equals manifest + dispatch value |
| `vector-control-valid` | ViewRequest vector, 0 violations |
| `vector-control-negative` | 8/8 cases reject/classify as specified |
| `vector-control-transitions` | 17/17 cases well-formed + frozen-vocabulary mapped |
| `vector-spatial-valid` | 0 violations (overall=accepted = candidacy only) |
| `vector-guidance-signs` | 2/2 analytic recompute (delta + cue words) |
| `vector-cad-valid` | 0 violations, det +1, parents resolve, no cycle |
| `vector-cad-invalid` | 6/6 mutations reject with the stated class |
| `mcp-bundle` | 9 tools, 18/18 schemas, 0 dangling/external refs, control source hash match |
| `regression-compliance` | `tools/compliance/check.py` exit 0, 0 errors |
| `regression-selftests` | this suite, run as a subprocess |
| `regression-memory` | every `memory.jsonl` rehashed before/after, unchanged |
| `E1`–`E12`, `H0`–`H18` | PENDING_EVIDENCE without hardware (E3/E6/E8/E9 cite synthetic sub-evidence) |

Verdicts: `PASS | FAIL | BLOCKED | PENDING_EVIDENCE | NOT_TESTED`.
`evidence_class`: `synthetic` (replay), `provenance` (hash/identity facts),
`physical` (needs hardware).

## Scorecard (`acceptance-run.json`)

Candidate + git HEAD, recomputed/manifest/expected contract SHAs,
profile, evaluator file hashes, hardware block, tool versions, every
command + exit code, evidence file hashes, per-gate verdict +
numerator/denominator + detail, memory before/after, verdict summary.
The canonical Wave-1 scorecard for the frozen base lives in `evidence/`.

## Limits (read before citing a PASS)

- The structural checker is a focused subset, schema-driven (required,
  enums, patterns, bounds, oneOf/anyOf/allOf/if-then for the frozen
  shapes) — not a general Draft 2020-12 validator. Full validation was
  done at G0 with ajv (CONTRACT.json record); this harness replays
  instances and guards drift via the manifest.
- Transition vectors are checked structurally + vocabulary; execution
  against a Core reducer is NOT_TESTED (no reducer in Wave-1 candidate).
- `wrong-source` / `same-operation-new-body` verify the vector's
  structural half plus the rule's fixture condition; live authorization
  and idempotency enforcement need a Core store (NOT_TESTED).
- The 7 guidance `required_extensions` are recorded, not executed.
- `format` (date-time etc.) is not validated beyond the frozen patterns.
- Synthetic PASS never passes a physical gate. No hardware was attached;
  every E/H verdict here is PENDING_EVIDENCE, not a claim.

## Design decisions

- Checks are pure functions over loaded documents (`checks.py`); all I/O,
  subprocesses and scorecard emission live in `accept.py`, so the suite
  tests logic without fixtures.
- Constraints are read from the frozen schemas at runtime (required
  lists, enums); only semantic rules (frame convention, determinants,
  idempotency key, cue words) are coded, each citing its frozen source.
- Contract-SHA derivation (sha256 over `"<sha>  <path>\n"` lines) was
  verified by reproducing the G0 value; 6 alternatives mismatch.
