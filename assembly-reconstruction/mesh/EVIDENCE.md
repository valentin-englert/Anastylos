# Evidence index — mesh visual-hull experiment

Every claim below links to a file:line or an artifact path. Hashes were
recomputed with `Get-FileHash` on 2026-10-29; test counts are from the
suite run on 2026-10-29 (see [Tests](#tests)).

## Claim index

| # | Claim | Evidence |
|---|---|---|
| 1 | Algorithm version is `mesh-vh-0.1.0` | `anastylos_mesh/__init__.py:11` |
| 2 | CLI runs as `python -m anastylos_mesh.cli` (no console script, no installed package) | `anastylos_mesh/cli.py:519-526`; `.venv/Scripts/` holds only `trimesh.exe` + activators, no `anastylos*` entry point |
| 3 | Occupancy rule: occupied iff `observed >= min_observed AND foreground == observed`; outside-image/behind-camera is unobserved | `anastylos_mesh/carve.py:3-9`, `:220` |
| 4 | Grid caps: 256/dim, 8M voxels | `anastylos_mesh/carve.py:33-34`, `:67-70` |
| 5 | Manifest schema is NON-NORMATIVE `mesh-evidence-v0` | `manifest-schema.json:4`, `:9-12`; `anastylos_mesh/export.py:39-40` |
| 6 | Outputs are never silently overwritten (`_rN` revisions under `--force`) | `anastylos_mesh/export.py:54-70` |
| 7 | Benchmark held-out bounds are pre-registered before execution | `anastylos_mesh/benchmark.py:170-190`, `:359-361`; `benchmark_out/expected-outcomes.json:69` (`created_before_execution: true`) |
| 8 | Both held-out verdicts PASS | `benchmark_out/benchmark_results.json` `heldout_verdicts` (both `true`); table below |
| 9 | E2E re-run reproduces `smoke_out/recon/` hashes exactly | run 2026-10-29 to `$env:TEMP/mesh-e2e-demo`: ply `680becbc…`, stl `69fc3812…`, 28068/56132 — identical to committed manifest pins |
| 10 | Case-A test fails 0.2375 vs 0.05 (z-floor) | suite output 2026-10-29; `tests/test_carve.py:31-42`; diagnosis below |
| 11 | Low elevations fix the z-floor; `el=0` hard-fails | elevation sweep probes 2026-10-29 (scripts in sandbox temp, uncommitted); table below |
| 12 | Cavity volume is overestimated (concavity fill) | `tests/test_carve.py:68-108`; `smoke_out/rec_cavity/metrics.json` vol rel err 1.5527 |
| 13 | All benchmark/smoke meshes watertight, 1 component, `reopen_ok` | `benchmark_results.json` records; `smoke_out/*/manifest.json` `metrics.surface` |
| 14 | Mask sidecar shape is adapter-defined; `synthetic` provenance rejected on the real path | `anastylos_mesh/spatial_adapter.py:645-703`; `tests/fixtures_spatial/README.md:24-30` |
| 15 | Committed test fixtures are labelled synthetic; sidecars generated at runtime, never committed | `tests/fixtures_spatial/README.md:1-16` |
| 16 | COLMAP path blocked (no backend) | [COLMAP-FEASIBILITY.md](COLMAP-FEASIBILITY.md) §1, §5 |
| 17 | CAD comparison partial (synthetic-only, no shared observations) | [CAD-COMPARISON.md](CAD-COMPARISON.md) §5, §6 |

## Artifacts

sha256 recomputed 2026-10-29 via `Get-FileHash` (shown lowercase to
match manifest pins). Manifest-embedded mesh hashes were verified
against the recomputed file hashes: all match.

| Artifact | Size (B) | sha256 | Git status |
|---|---|---|---|
| `smoke_out/recon/mesh.ply` (box, r96) | 1066751 | `680becbce34809263058cebcefc2f80b8d299276b47f0cd93cbf4e34f981bda9` | ignored (`.gitignore`: regenerable binary) |
| `smoke_out/recon/mesh.stl` | 2806684 | `69fc381239d7c114c094978d4b0ae45dcabf22a496fe118c90af700012638c62` | ignored |
| `smoke_out/recon/manifest.json` | 4047 | `ac6595b4fed69b9f400098a59b69d7608a781bfebd7e335d7878df5f7d1e0dca` | committed (reference output; pins both hashes above) |
| `smoke_out/rec_cylinder/mesh.ply` (r64) | 795735 | `d2be6353247ff78bfc2283a39ad6ba8e392d211845636008080c4ca644b94310` | ignored |
| `smoke_out/rec_cylinder/mesh.stl` | 2093484 | `2dde3b90939e03b1f756b43d1c25bb216a99d16cf8439df8142a91b563b3beca` | ignored |
| `smoke_out/rec_cylinder/manifest.json` | 4039 | `c4499706f8acdd3cafd955093dae5bc4f8b7b41d8bf8484020c6c8298a2df69b` | committed |
| `benchmark_out/benchmark_results.json` | 64624 | `c8ee6f1bdb74c2b90baf2ce155095492136411cfb9b79a84e8fdd0845550e16c` | committed |
| `benchmark_out/benchmark_results.csv` | 10223 | `9d330832b23398030f508a55245027f6414d5413673818c4c1ffbc240ce108be` | committed |
| `benchmark_out/expected-outcomes.json` | 1983 | `86b8d7d77ec6c08e4d8d813b5c5c3baabf1feca8ae0fe4960370d99c866059f5d` | committed |
| `benchmark_out/runs/*/*/mesh.{ply,stl}` (42 files) | 93456117 total | per-record `ply_bytes`/`stl_bytes` in JSON; `reopen_ok: true` all reps | ignored (`benchmark_out/runs/`) |
| `smoke_out/*/metrics.json`, fixture PNGs/JSONs (88 files) | 99195 total | deterministic from code; all PNGs <1 KB | committed |

Reference metric values (committed `metrics.json`):

| Scene | chamfer_sym (mm) | compl@1mm | bbox_max_err (mm) | vol_rel_err | verts/faces |
|---|---|---|---|---|---|
| box (`recon/`, r96) | 1.6086 | 0.3802 | 6.4375 | 0.2321 | 28068/56132 |
| cylinder (`rec_cylinder/`, r64) | 0.8450 | 0.7334 | 4.0 | 0.1047 | 20936/41868 |
| stepped (`rec_stepped/`) | 2.2732 | 0.3751 | 6.4063 | 0.3034 | 13036/26068 |
| cavity (`rec_cavity/`) | 2.6376 | 0.2801 | 6.0938 | **1.5527** | 12342/24680 |

## Tests

Command (from `assembly-reconstruction/mesh/`):
`.venv/Scripts/python.exe -m unittest discover -s tests`.
Run 2026-10-29: **64 tests, 63 pass, 1 FAIL, 80.1 s.**

| File | Tests | Pass | Fail | Covers |
|---|---|---|---|---|
| `tests/test_benchmark.py` | 5 | 5 | 0 | fixed profiles/splits, pre-registration, tiny full run |
| `tests/test_carve.py` | 4 | 3 | **1** | box accuracy (Case A, FAIL), gates, cavity fill |
| `tests/test_case_b_cylinder.py` | 1 | 1 | 0 | cylinder radius/height/curved metrics |
| `tests/test_case_c_stepped.py` | 2 | 2 | 0 | stepped bbox/baseline, rotation-sense unmirrored |
| `tests/test_case_f_calibration.py` | 4 | 4 | 0 | NaN/non-rigid/focal/reflection rejects |
| `tests/test_case_g_provenance.py` | 4 | 4 | 0 | epoch/sha/resolution provenance gates |
| `tests/test_case_h_epoch.py` | 2 | 2 | 0 | mixed-epoch reject, shared-epoch accept |
| `tests/test_case_i_sparse.py` | 2 | 2 | 0 | sparse runs annotated `partial`, overclaim reject |
| `tests/test_case_j_units.py` | 5 | 5 | 0 | mm-only gates, `metric_status=invalid`, strict reject |
| `tests/test_determinism.py` | 1 | 1 | 0 | same inputs twice -> identical sha256 |
| `tests/test_export.py` | 1 | 1 | 0 | PLY/STL reopen finite + nonempty |
| `tests/test_geometry.py` | 5 | 5 | 0 | mat4 round-trip, projection, det1 gates |
| `tests/test_masks.py` | 3 | 3 | 0 | dim/hash/size gates, empty/full flags |
| `tests/test_spatial_adapter.py` | 25 | 25 | 0 | READY/PENDING paths, tamper gates, CLI exit codes, write-nothing |

### Open FAIL: Case A (`test_box_8view_nonempty_and_bbox_under_5pct`)

`tests/test_carve.py:31-42`: 8-view box at 320x240, res-32 grid,
asserts max relative bbox error < 0.05; observed **0.2375**
(= 7.125 mm z-error / 30 mm height).

Diagnosis (verified by probe 2026-10-29, same settings): the ROI grid
pads z to [-3.0, 33.0] (`_grid_for_scene`, margin 0.05); the ring uses
elevations (25°, 45°), all above the board, so the bottom face (z=0) is
never silhouette-observed. Outside-image/behind-camera is unobserved
(`carve.py:219-220`), and nothing carves the void below the object, so
occupancy extends to the grid floor: reconstructed mesh zmin = **-3.0
exactly** (the grid floor), zmax 34.125; x also touches the grid bounds
(±33.0). Fix direction (not implemented): low-elevation views and/or an
explicit bottom prior. The same 0.2375 observation is cited in
`tests/test_case_j_units.py:93`.

Elevation sweep (probe 2026-10-29, synthetic box, res-32, 8 views):

| Elevations (deg) | z abs err (mm) | max rel | chamfer (mm) | vol rel err | Note |
|---|---|---|---|---|---|
| (25, 45) default | 7.125 | 0.2375 | 2.164 | 0.248 | z-floor |
| (10, 10) | 7.125 | 0.2375 | 1.765 | 0.167 | still floors |
| (8, 8) | 5.0625 | 0.1688 | 1.639 | 0.150 | — |
| (5, 5) | 3.0 | 0.1344 | 1.610 | 0.141 | x-err dominates |
| (5, 45) | 3.0 | 0.1000 | 1.598 | 0.140 | mixed ring works |
| (2, 2) | 0.9375 | 0.0313 | 1.447 | 0.031 | thin masks (4-10k fg px) |
| (1, 1) | 1.125 | 0.0375 | 1.399 | 0.020 | slivers (972-1650 fg px) |
| (0.5, 0.5) | 0.9375 | 0.0375 | 1.430 | 0.020 | slivers (min 252 fg px of 76800) |
| (0, 0) | — | — | — | — | hard fail: `INVERTED_BOARD_Z` |

Probe scripts ran from sandbox temp and are uncommitted; the sweep used
only committed code paths (`render_fixtures`/`carve`/`evaluate`).

## Benchmark trade-off table

Source: `benchmark_out/benchmark_results.json` (canonical 3-repeat run,
`mesh-vh-0.1.0`, Intel64 Family 6 Model 158, Python 3.12.10,
Windows 11, total wall 284.38 s). Values are across-rep means;
geometry is bitwise-identical across reps, timing varies.
`render_ms` is synthetic-fixture harness cost and `eval_ms` is scoring
overhead — neither is reconstruction cost.

| Config | total | carve | surface | render* | eval† | chamfer | compl@1mm | vol rel | peak (MB) | verts/faces |
|---|---|---|---|---|---|---|---|---|---|---|
| dev-box-r48 | 1677.7 | 91.2 | 151.4 | 1429.7 | 70.6 | 1.7437 | 0.3266 | 0.2388 | 15.2 | 6964/13924 |
| dev-box-r96 | 3251.6 | 1099.2 | 224.1 | 1915.1 | 118.2 | 1.6841 | 0.3252 | 0.2321 | 64.3 | 28068/56132 |
| dev-box-r144 | 4745.7 | 2860.1 | 507.7 | 1362.2 | 177.3 | 1.6618 | 0.3367 | 0.2278 | 215.4 | 63310/126616 |
| val-cylinder-r96 | 21873.9 | 1600.8 | 279.8 | 19981.3 | 97.8 | 0.9293 | 0.6957 | 0.1041 | 177.2 | 47512/95020 |
| val-stepped-r96 | 3084.7 | 828.1 | 169.8 | 2079.0 | 93.6 | 2.3412 | 0.3156 | 0.3042 | 81.2 | 29548/59092 |
| heldout-rotated-stepped-r96 | 2555.1 | 781.3 | 175.1 | 1590.9 | 105.9 | 2.4856 | 0.3789 | 0.3196 | 81.2 | 32497/64994 |
| heldout-tall-cylinder-r96 | 25657.7 | 478.4 | 118.0 | 25055.8 | 60.4 | 0.8409 | 0.8226 | 0.0884 | 177.2 | 17836/35668 |

All rows: 8 views, baseline 130.0°, watertight, 1 component,
`reopen_ok: true`. Times in ms; peak is tracemalloc Python-level only
(numpy/native buffers excluded by design — `benchmark.py:70-74`).

Held-out verdicts (means vs pre-registered bounds in
`expected-outcomes.json`): rotated-stepped chamfer 2.4856 < 4.0, compl
0.3789 > 0.15, bbox 6.0625 < 12.0, vol 0.3196 < 0.6 → PASS;
tall-cylinder chamfer 0.8409 < 3.0, compl 0.8226 > 0.25, bbox 7.0 <
10.0, vol 0.0884 < 0.35 → PASS.

Read-off: resolution 48→144 buys little geometry (chamfer 1.74→1.66,
vol 0.239→0.228) for 2.8x carve time and 13x peak bytes; cylinders
reconstruct far better than stepped boxes (silhouette-friendly);
render dominates wall time on cylinders (ray-cast cost scales with
triangle count: 64-segment soup).

## Provenance statement

All mesh evidence in this directory is **synthetic**: every fixture,
mask, camera, and metric derives from the analytic renderer
(`anastylos_mesh/synthetic.py`) with `mask_provenance: synthetic`.
No real photo, real calibration, real mask, or real board is
represented. The adapter test fixtures are explicitly labelled
(`tests/fixtures_spatial/README.md`) and staged sidecars are generated
at test runtime, never committed. No claim in this directory transfers
to real hardware without the physical protocol in
[ACQUISITION-CHECKLIST.md](ACQUISITION-CHECKLIST.md).

## Failure modes

| Mode | Symptom | Evidence |
|---|---|---|
| z-floor (Case A, OPEN) | high-only rings leave height unconstrained; hull extends to padded grid floor; box z-err 7.125 mm | suite FAIL 0.2375; probe zmin = -3.0 = grid floor |
| Concavity fill (intrinsic) | interior voids fill; cavity vol rel err 1.5527 | `tests/test_carve.py:68-108`; `rec_cavity/metrics.json` |
| Grazing regime (el ≤ ~2°) | thin-sliver silhouettes (fg collapses to hundreds of px) + far-camera geometry; `el=0` exactly hard-fails `INVERTED_BOARD_Z` | elevation sweep probes; `cameras.py:105-106` |
| Mixed pose epochs | carve refused (`MIXED_POSE_EPOCH`); no registration path exists | `carve.py:133-149` |
| Non-mm scenes | `metric_status: invalid`, or `WRONG_LENGTH_UNIT` under `--strict-units` | `export.py:129-145`; `tests/test_case_j_units.py` |
| Sparse views | `coverage_class: partial`; explicit `complete` raises `COVERAGE_OVERCLAIM` | `export.py:135-156`; `tests/test_case_i_sparse.py` |
| NumPy 2.5 + scikit-image warning | `DeprecationWarning: setting the shape on a NumPy array` from `skimage/measure/_marching_cubes_lewiner.py` during `extract_surface` | observed in suite stderr 2026-10-29; benign deprecation, outputs unaffected |

Note on grazing: on the synthetic box, el 0.5-2° still scores well
because exact masks make sliver silhouettes informative; the failure
prediction (mask-noise fragility) is unvalidated — no real-mask
evidence exists. The physical protocol therefore brackets low
elevations (≈5°) while staying clear of ≤2°; see
[ACQUISITION-CHECKLIST.md](ACQUISITION-CHECKLIST.md).
