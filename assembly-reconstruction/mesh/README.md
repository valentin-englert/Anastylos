# Mesh visual-hull experiment

Synthetic-only experiment: silhouette (visual-hull) reconstruction of
small rigid objects from calibrated multi-view masks. Algorithm version
`mesh-vh-0.1.0` (`anastylos_mesh/__init__.py:11`).

Pipeline: `render-fixtures` (synthetic Camera+Mask fixtures) ->
`reconstruct` (carve occupancy -> marching-cubes surface -> PLY/STL +
manifest) -> `evaluate` (vs analytic ground truth) -> `verify-reopen`
(fresh-process reload check). Plus `benchmark` (fixed-profile
dev/validation/held-out harness) and `ingest-spatial` (experimental
adapter from sealed production Spatial evidence to a carve request).

Status: experiment only. No real-hardware validation anywhere; one open
test failure (Case A, z-floor); the manifest schema is NON-NORMATIVE.
See [EVIDENCE.md](EVIDENCE.md) for the claim-by-claim evidence index.

## Install

Recreate the pinned venv from this directory
(`assembly-reconstruction/mesh/`):

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-mesh.txt
.venv/Scripts/python.exe -m pip freeze | Select-String -NotMatch '^pip==' > $env:TEMP/freeze.txt
Compare-Object (Get-Content gate-freeze.txt) (Get-Content $env:TEMP/freeze.txt)
```

`Compare-Object` must print nothing: the venv must match
`gate-freeze.txt` exactly (11 pins; `pip` itself is excluded).
Hashes: `gate-pypi-hashes.json`. Licenses: `gate-licenses-compact.txt`.
There is no console script and no installed package: the CLI always runs
as `python -m anastylos_mesh.cli` from this directory.

Run the suite from this directory:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests
```

Expect 63 pass / 1 known FAIL (Case A) in ~80 s. See
[EVIDENCE.md](EVIDENCE.md#tests).

## CLI usage

All commands run from `assembly-reconstruction/mesh/` as
`.venv/Scripts/python.exe -m anastylos_mesh.cli <subcommand>`.
Full flags: `anastylos_mesh/cli.py:453-516`.

| Subcommand | Purpose | Key flags |
|---|---|---|
| `render-fixtures` | render a synthetic scene's Camera+Mask fixture set | `--scene box\|cylinder\|stepped\|cavity`, `--param k=v` (repeatable dim overrides), `--views 8`, `--width 320`, `--height 240`, `--focal-px`, `--elevations 25.0 45.0`, `--out` (required) |
| `reconstruct` | carve + extract + export PLY/STL/manifest | `--fixtures`, `--resolution 96`, `--margin 0.05`, `--min-observed 2`, `--out`, `--force` (write `_rN` revisions, never overwrite), `--strict-units` (reject non-mm scenes) |
| `evaluate` | score a mesh vs fixture ground truth | `--fixtures`, `--mesh`, `--samples 20000`, `--tau 1.0`, `--seed 0`, `--out` (optional metrics.json) |
| `verify-reopen` | reload PLY/STL in a fresh subprocess; compare counts + sha256 | `--manifest` (exit 1 on mismatch) |
| `benchmark` | fixed-profile benchmark (dev/val/held-out) | `--out benchmark_out`, `--repeats 3` (refuses to overwrite an existing `benchmark_results.json`) |
| `ingest-spatial` | build a request from sealed Spatial evidence, then carve + export | `--dataset`, `--masks`, `--config`, `--out`, `--force`. Exit 0 READY, 1 PENDING (writes `ingest-status.json`, never a manifest), 2 ERROR |

### End-to-end example (verified 2026-10-29)

Copy-paste from `assembly-reconstruction/mesh/`. Scratch outputs go to
`$env:TEMP` so the worktree stays clean. Geometry outputs are
deterministic: this run reproduced the committed `smoke_out/recon/`
hashes byte-for-byte (see [EVIDENCE.md](EVIDENCE.md#artifacts)).

```powershell
$PY = ".venv/Scripts/python.exe"
$T = "$env:TEMP/mesh-e2e-demo"
Remove-Item -Recurse -Force $T -ErrorAction SilentlyContinue

& $PY -m anastylos_mesh.cli render-fixtures --scene box --views 8 --out $T/fix
& $PY -m anastylos_mesh.cli reconstruct --fixtures $T/fix --resolution 96 --out $T/rec
& $PY -m anastylos_mesh.cli evaluate --fixtures $T/fix --mesh $T/rec/mesh.ply --out $T/rec/metrics.json
& $PY -m anastylos_mesh.cli verify-reopen --manifest $T/rec/manifest.json

# Benchmark to a scratch dir (--repeats 1 takes ~70 s; the committed
# benchmark_out/ holds the canonical 3-repeat run and is never overwritten):
& $PY -m anastylos_mesh.cli benchmark --out $env:TEMP/mesh-e2e-bench --repeats 1

# ingest-spatial needs staged real evidence (no committed dataset exists;
# test datasets are generated at runtime, never committed). Shape:
& $PY -m anastylos_mesh.cli ingest-spatial --dataset <dataset_dir> --masks <mask_dir> --config <grid-carve.json> --out $T/ingest
```

`ingest-spatial` inputs: `dataset_dir` with `capture_set.json` +
`packages/` + `sidecars/` + `spatial/`; `mask_dir` with
`<image_sha256>.png` + `.json` pairs; config with `grid.bounds_mm`,
`grid.voxel_size_mm`, and optional `carve` params. Full layout in
`anastylos_mesh/spatial_adapter.py:9-40`; config shape template (test
values) at `tests/fixtures_spatial/config.synthetic-v1.json`; mask
sidecar shape at
`tests/fixtures_spatial/mask-meta.synthetic-template.json`; physical
capture protocol in [ACQUISITION-CHECKLIST.md](ACQUISITION-CHECKLIST.md).

## Module map

| Path | Job |
|---|---|
| `anastylos_mesh/__init__.py` | `ALGORITHM_VERSION`, typed-error re-exports |
| `anastylos_mesh/geometry.py` | rigid-transform + Brown-Conrady projection math; error classes; convention mirror inventory |
| `anastylos_mesh/cameras.py` | `Camera` dataclass + fail-closed calibration validation |
| `anastylos_mesh/masks.py` | `Mask` dataclass (hash-pinned PNG + foreground grid), `preprocess` binarizer |
| `anastylos_mesh/synthetic.py` | scenes (box/cylinder/stepped/cavity), exact ray-cast silhouette renderer, deterministic PNG codec |
| `anastylos_mesh/carve.py` | `VoxelGrid` + visual-hull `carve` (frozen occupancy rule, baseline/epoch gates) |
| `anastylos_mesh/surface.py` | marching-cubes extraction + trimesh cleanup, watertight/components report |
| `anastylos_mesh/metrics.py` | `evaluate`: bbox err, symmetric Chamfer, completeness, volume err |
| `anastylos_mesh/export.py` | PLY/STL export, no-overwrite `resolve_output`, NON-NORMATIVE manifest builder |
| `anastylos_mesh/benchmark.py` | fixed-profile benchmark runner (pre-registered held-out bounds) |
| `anastylos_mesh/cli.py` | `anastylos-mesh` subcommands (fixtures, reconstruct, evaluate, reopen, benchmark, ingest) |
| `anastylos_mesh/spatial_adapter.py` | experimental read-only SpatialDataset -> mesh-request adapter |
| `tests/` | 14 test modules, 64 tests; `tests/fixtures_spatial/` holds committed synthetic adapter fixtures |
| `benchmark_out/` | canonical 3-repeat results: `benchmark_results.json/.csv`, `expected-outcomes.json` (committed); `runs/` meshes (git-ignored, regenerable) |
| `smoke_out/` | CLI smoke reference outputs: fixtures + manifests + `metrics.json` (committed); `mesh.ply/stl` binaries (git-ignored, regenerable) |
| `manifest-schema.json` | JSON Schema for the NON-NORMATIVE `mesh-evidence-v0` manifest |
| `requirements-mesh.txt`, `gate-freeze.txt`, `gate-pypi-hashes.json`, `gate-licenses-compact.txt` | pinned environment + provenance gates |

## Manifest schema note

`mesh-evidence-v0` (`manifest-schema.json`) is explicitly NON-NORMATIVE:
an experiment interchange record, not a system contract. Every manifest
carries `non_normative: true`, algorithm version, params, input
sha256 pins, voxel resolution, timing, metrics, output sha256 pins, and
an `extra` block with `reconstruction_class` (always
`"silhouette-constrained"`), `coverage_class` (`complete`/`partial`),
`metric_status` (`valid`/`invalid`), and `declared_units`. Promotion to
a real contract needs contract-owner approval; see
[INTEGRATION-PROPOSAL.md](INTEGRATION-PROPOSAL.md#contract-owner-questions).

## Limits (synthetic-only experiment)

- Zero real-hardware validation: every number in `benchmark_out/` and
  `smoke_out/` is synthetic (`mask_provenance: synthetic`).
- Case A open failure: the 8-view box bbox test fails 0.2375 vs the
  0.05 bound (z-floor: high-only ring leaves height unconstrained).
- Concavities are filled, never recovered (cavity volume rel err 1.55).
- COLMAP comparison is blocked (no backend); CAD comparison is partial
  (synthetic-only, no shared observation set). Details:
  [COLMAP-FEASIBILITY.md](COLMAP-FEASIBILITY.md),
  [CAD-COMPARISON.md](CAD-COMPARISON.md).
- Single pose epoch per carve; mm-only world units; voxel quantization
  floors dimension accuracy; `el=0` exactly hard-fails
  (`INVERTED_BOARD_Z`).
- Full failure-mode inventory: [EVIDENCE.md](EVIDENCE.md#failure-modes).

Further reading: [EVIDENCE.md](EVIDENCE.md) (evidence index),
[ARCHITECTURE-NOTES.md](ARCHITECTURE-NOTES.md) (rules, error taxonomy,
bounds), [ACQUISITION-CHECKLIST.md](ACQUISITION-CHECKLIST.md) (physical
protocol), [INTEGRATION-PROPOSAL.md](INTEGRATION-PROPOSAL.md) (promotion
path). Production contracts referenced but never modified: the
calibration Spatial validator/profiles, the capture-companion receiver
seam/journal, and the CAD worker (see ARCHITECTURE-NOTES for file:line
sources).
