# Spatial evidence validation (AgentCam Wave 1, CAD-0)

Replayable validator for `anastylos-spatial-evidence@1` sidecars: semantic
checks JSON Schema cannot express (rigid-transform validity, reflection and
invalid-rotation rejects, exposure/pose association, quality-gate
consistency, epoch joins, basis vocabulary), a replay evaluator over the
frozen contract vectors plus 24 owned behavior vectors, an immutable
multi-view dataset builder, and versioned JSON handoffs for ANDROID.

Status: synthetic vectors only. No physical calibration, no real phone,
no hardware claim. All physical gates are `PENDING_EVIDENCE`.
Architecture source of truth stays [`ARCHITECTURE.md`](../../ARCHITECTURE.md).

## Layout

- `anastylos_spatial/geometry.py` — rigid transforms, Brown-Conrady
  projection, crop/scale K mapping, guidance cues (stdlib only).
- `anastylos_spatial/validator.py` — sidecar semantic validation.
- `anastylos_spatial/profiles.py` — calibration-profile and board-instance
  validation plus sidecar/profile binding.
- `anastylos_spatial/replay.py` — CAD-0 replay evaluator (report schema
  `anastylos-spatial-replay@1`).
- `anastylos_spatial/dataset.py` — dataset manifests
  (`anastylos-spatial-capture-set@1`).
- `validate.py` — CLI entry point.
- `calibration-profile.synthetic-v1.json` — structurally valid synthetic
  profile for ANDROID (`metric_validity: unknown`; no physical run).
- `guidance-signs.spatial-v1.json` — guidance extensions for ANDROID
  (display rotations, yaw, identity, mirror/depth refusals).
- `vectors/` — 24 owned behavior vectors (mutations of the frozen valid
  sidecar with expected verdicts).
- `tests/` — 88 unit tests. `acceptance-run.json` — lane evidence report.

## CLI

```powershell
cd calibration/spatial
python validate.py sidecar --sidecar sidecar.json [--layout ../../calibration/calibration-config.json] [--profile calibration-profile.synthetic-v1.json] [--epochs epochs.json] [--assets assets.json]
python validate.py profile --profile calibration-profile.synthetic-v1.json
python validate.py board --board board.json
python validate.py replay --contracts ../../capture-companion/control/contracts --candidate <sha> --out replay-report.json
python validate.py dataset --inputs dataset-inputs.json --out manifest.json
```

Exit codes: `0` accept/green/consistent, `1` reject/needs-review/red/issues,
`2` usage or I/O error. Every command prints the SHA-256 of what it wrote.

`--epochs` is a JSON list of `{epoch_id, start_utc, end_utc}` bounding one
observed valid epoch each; `--assets` maps image SHA-256 to byte length.
Without them the chain checks report `*_SKIPPED` notes instead of verdicts.

## Contracts consumed (read-only, never edited)

Frozen G0 contract `ec313f7f…4663` (`capture-companion/control/contracts/`):
`spatial-evidence.schema.json`, `spatial-valid.json`, `guidance-signs.json`.
The replay re-hashes all 13 manifest files and refuses to go green on
mismatch. Board geometry and marker ids bind against the real
`calibration-config.json` (DICT_5X5_100, board +z into the plane).

## Verdicts

`accept` (truthful record), `needs_review` (truthful but incomplete, never
metric-ready), `reject` (contradictory or invalid). `metric_ready` reports
the phone-side *necessary* conditions only; promotion additionally needs a
Core validator report plus a `qualified` Receipt (frozen resolution 1).

Frozen thresholds (`guidance-hypothesis-1`): RMS ≤ 1.2 px / max ≤ 3 px at
1280-equivalent width, inlier ratio ≥ 0.9, ≥ 3 markers, pose age ≤ 150 ms,
interpolation brackets ≤ 50 ms same-timebase, print bars ≤ 0.5 %,
25–40 calibration views with ≥ 8 held-out. Provisional Wave-1 dataset
heuristics (synthetic, not frozen): redundant below 1 mm center separation,
`geometry_ready` needs ≥ 2 accepted poses with ≥ 5 mm baseline, one board
frame, one object epoch.

## Tests

```powershell
cd calibration/spatial
python -m unittest discover -s tests
```

88 tests, stdlib only (no `.venv` needed): transform round trips,
reflection/invalid-rotation/bottom-row/inverted-z rejects, Brown-Conrady
projection, cropped-image K equivalence, frozen guidance recompute,
display-rotation/yaw/identity/refusal extensions, stale-exposure and
bracketing association, epoch-overlap rules, layout/profile/asset binding,
dataset partitioning and redundancy flags, replay determinism, contract
tamper detection.
