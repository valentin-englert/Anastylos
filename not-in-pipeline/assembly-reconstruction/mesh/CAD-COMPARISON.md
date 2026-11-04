# Mesh vs parametric CAD comparison (read-only study)

Status: **PARTIAL** — synthetic evidence only on both sides; no shared
observation set; CAD kernel timings PENDING_ENV in this worktree.
See §5 (verdict) and §6 (missing evidence).

Scope rule: `assembly-reconstruction/cad/` is READ-ONLY for this study.
Nothing under it was modified. This file is the study's only output.

Paths below are relative to `assembly-reconstruction/`.

## 1. What the CAD worker actually supports today

Source: `cad/cadwork.py`, `cad/anastylos_cad/*.py`,
`cad/README.md`, `cad/PARAMETRIC-PROPOSAL.md`, `cad/EVIDENCE.md`.

### 1.1 Worker operations (CLI)

Entry `cad/cadwork.py:14` delegates to `cad/anastylos_cad/worker.py:418`
(`main`). Seven fixed-argument subcommands:

| Subcommand | Implementation | Kernel needed |
|---|---|---|
| `validate --spec` | `worker.py:81` | No (stdlib-only) |
| `build --spec --out` | `worker.py:134` | Yes (CadQuery/OCP) |
| `reopen --step` | `worker.py:148` | Yes |
| `run --spec --out` | `worker.py:206` (build + fresh-process reopen + evidence bundle) | Yes |
| `apply-edits --spec --edits --out` | `worker.py:267` | No (stdlib) |
| `export-fcstd --spec --out` | `worker.py:308` | Yes (tree/kernel cross-check) |
| `merge-estimates --current --provenance --estimate --out` | `worker.py:365` | No (stdlib) |

Guards (`worker.py:16-40`): exit codes 0 ok / 2 pre-kernel reject /
3 kernel-or-round-trip failure / 4 usage-or-I/O; `--out` must be
absent-or-empty (revisions never overwritten); spec ≤ 1 MiB, STEP ≤
256 MiB; reopen runs as a fixed-argument same-interpreter subprocess
with a 120 s timeout. STEP round-trip gates: dims within 0.01 mm,
volume relative error ≤ 1e-5 (`worker.py:38-39`, `_compare` at
`worker.py:193`).

### 1.2 Restricted feature language (v1)

`cad/anastylos_cad/validate.py:28`: `KINDS = ("box", "cylinder",
"through_hole")` — nothing else. Parameter sets
(`validate.py:45-47`):

- `box`: `size_mm: [x, y, z]`
- `cylinder`: `radius_mm`, `height_mm`
- `through_hole`: `radius_mm`, `depth_mm`, `parent_feature_id`

Semantic rules (`validate.py:240-279`): parent must exist and be a
solid (hole-on-hole rejected); **`depth_mm` must equal the parent
thickness** (box `size_mm[2]` or cylinder `height_mm`) within 1e-9;
dependency acyclicity. Transforms `T_CAD_B` / `T_CAD_feature` must be
proper rotations (det = +1, orthonormal, bottom row `[0,0,0,1]`;
reflections rejected, `validate.py:126-151`). Bounds: 1–100 features,
dims in (0, 1000] mm, units exactly `mm` (`validate.py:21-26`).
Rejection classes are `schema-reject` vs `semantic-reject`
(`validate.py:17-18`); the validator never raises on malformed input
(`validate.py:282`).

Profile extrudes, blind cuts, slots and lofts are explicitly versioned
v2 work (`validate.py:200-204`, `cad/README.md` limitations).

### 1.3 Inputs required

The worker consumes **authored feature specs**, not images:

- A `cad-feature-model-v1` document (`model_id` UUID, `revision`,
  `parent_model_sha256` chain, `dataset_sha256`, `T_CAD_B`, `features[]`),
  e.g. `cad/fixtures/synthetic/plate_hole.json` (80×50×6 plate +
  r3 through-hole, fully synthetic).
- Per-feature `basis` (`geometric_measurement` | `operator_testimony`
  | `model_assumption`), `evidence_ids` (UUIDs), `uncertainty_mm`
  (nullable), `assumptions[]` (`validate.py:206-235`).
- **Operator corrections** arrive as `anastylos-cad-param-edits@1`
  documents (base-hash pinned, reasoned per-parameter values) via
  `apply-edits`; provenance sidecar
  `anastylos-cad-param-provenance@1` records `estimated` vs
  `operator-specified` per parameter with append-only history
  (`params.py:22-23`, `:120-164`). Merge rule: operator values always
  win; superseded machine estimates are recorded, never silently
  applied (`params.py:223-287`).
- There is **no image-to-feature estimator** in the CAD lane: the
  spec author (human or a future estimator) is outside the worker.
  The `evidence.missing_feature_envelope` handoff is a data-only
  structure for a later-wave viewpoint planner (`evidence.py:86-110`).

### 1.4 Outputs

Per `run` revision (`worker.py:206-264`, `evidence.py:12` bundle):

- `feature-model.json` (canonical validated params),
  `model.py` (deterministic trusted construction script — parameters
  as one JSON literal, calls only `builder.build_solids`;
  `compile.py:45-66`),
- `model.step` (mm units; one solid per revision, `worker.py:106-108`),
- `preview.svg` (orthographic tessellation-wireframe projection from
  the reimported solid, model hash cited),
- `feature-evidence.json` (per-feature basis/uncertainty/support/
  residuals), `validation.json` (native vs reimport gates +
  unresolved-feature list, `evidence_class=synthetic`),
  `manifest.json` (SHA-256 over all six bundle files + input pins).
- `export-fcstd` additionally writes a native parametric `.FCStd`
  (Part::Box/Cylinder/Cut tree, no shape blobs) plus a `.FCMacro`
  rebuild script, after an independent raw-OCP tree rebuild proves
  tree/kernel geometry equivalence (`fcstd.py:160-234`).

Kernel mapping (`builder.py:31-69`): `makeBox` / `makeCylinder` in the
feature frame, through-cut with a tool cylinder overshooting 1.0 mm
past each face (`builder.py:15`). STEP export/import via CadQuery
(`builder.py:93-103`).

### 1.5 Provenance model

Every artifact is SHA-256 pinned (`evidence.py:16-31`); revisions link
via `parent_model_sha256` and `revision+1` (`params.py:159-160`);
`--out` no-overwrite preserves prior revisions (`worker.py:58-69`);
operator provenance beats machine estimates by construction
(`params.py:260-269`). Committed gate records live in
`cad/evidence/` (freeze, PyPI hashes, licenses).

## 2. Fair comparison protocol

### 2.1 Why visual-hull runtime vs CAD-compile runtime is not end-to-end

The two pipelines start from **different inputs** and answer
**different questions**:

- Mesh visual hull: calibrated images + masks + poses → occupancy mesh.
  Its measured runtime covers evidence ingestion through surface export.
- CAD worker: validated feature spec → STEP solid + evidence bundle.
  Its measured runtime covers compilation only. The expensive,
  uncertain step — **estimating the feature spec from observations**
  (dimensions, frames, hole topology, uncertainty) — happens
  *before* the worker and is currently manual/synthetic with no
  measured cost at all.

Comparing `carve+surface+export` ms against `build+export` s without
the estimation account is comparing a full reconstruction against a
code-generation step. A fair protocol separates the accounts below.

### 2.2 Same-observation-set design

1. Fix one physical object + one calibrated capture session (rigid
   matte part, caliper-measured ground truth dims, N phone photos,
   calibration sheet in frame).
2. Both paths consume the **same observation set**: the same images,
   the same camera solution, the same masks (provenance `manual` or
   `auto` — never `synthetic` for the comparison run).
3. Mesh path: `render-fixtures`-equivalent real ingest →
   `reconstruct` → `evaluate` against caliper/CMM ground truth.
4. CAD path: operator (or estimator) derives a `cad-feature-model-v1`
   spec **from that same observation set**, recording wall-clock
   estimation time and every `basis`/`uncertainty_mm` honestly →
   `cadwork.py run` → compare STEP dims against the same ground truth.
5. Report both paths' full accounts (§2.3) and all quality axes (§2.4).
   Pre-register bounds before running (as `benchmark.py` already does
   for mesh held-outs, `mesh/anastylos_mesh/benchmark.py:86-105`).

### 2.3 Separated timing accounts

Mesh account (already instrumented; `mesh/anastylos_mesh/cli.py:197-203`,
`mesh/anastylos_mesh/benchmark.py:287-292`):

- (M1) mask acquisition (manual tracing time or auto-segmentation
  runtime — currently unmeasured; synthetic render is NOT a proxy),
- (M2) camera registration / calibration solve time,
- (M3) `load_ms` + `carve_ms` + `surface_ms` + `export_ms`
  (reconstruction proper; `render_ms` in benchmark records is
  synthetic-fixture cost and `eval_ms` is scoring overhead — report
  them separately, never folded into reconstruction).

CAD account:

- (C1) feature-spec estimation time (operator measuring/modeling, or
  estimator runtime) — currently unmeasured and unimplemented,
- (C2) `validate` (stdlib, pre-kernel),
- (C3) `build` kernel time + STEP export (`run` minus reopen),
- (C4) fresh-process `reopen` + round-trip gate check,
- (C5, optional) `export-fcstd` + tree/kernel cross-check.

End-to-end compares (M1+M2+M3) against (C1+C2+C3+C4). Any comparison
omitting M1/M2/C1 must say so in the same table.

### 2.4 Quality axes

| Axis | Mesh visual hull (intrinsic) | Parametric CAD (intrinsic) |
|---|---|---|
| Fidelity | Silhouette-bound: convex-ish overestimate; concavities unrecoverable (cavity vol rel err 1.55, §3.2) | Exact to spec by construction; fidelity = spec-estimation error |
| Watertightness | Reported per mesh (`surface.py:45` marching-cubes + cleanup); benchmark: all watertight, 1 component | Kernel `isValid()` + positive-volume gate (`worker.py:111-113`) |
| Editability | None: baked triangles, no parameters | Full: stable `{feature_id}.{role}` params, revision chain, FCStd tree |
| Dimension control | Voxel-quantized (voxel_size_mm floor); dims emerge, not prescribed | Prescribed to float precision; +1 mm regen exact (§3.3) |
| Fabrication suitability | Visualization / rough print; no tolerances, no features | STEP solid + tolerances-ready params; FreeCAD UI path PENDING |

## 3. Measured numbers available TODAY

All mesh figures are synthetic (`mask_provenance=synthetic`).
No real-image inputs exist on either side.

### 3.1 Mesh benchmark (`mesh/benchmark_out/benchmark_results.json`)

`algorithm_version=mesh-vh-0.1.0`, 7 configs × 3 reps,
total wall 284.38 s, Intel64 Family 6 Model 158, Python 3.12.10,
Windows 11. Held-out verdicts: **both PASS** (pre-registered bounds).
Means across 3 reps (min/max omitted for brevity; geometry is
bitwise-deterministic, timing varies — see the JSON):

| Config | total_ms | carve | surface | export | render* | eval† | chamfer_sym | compl@1mm | bbox_max_err | vol_rel_err | verts/faces |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev-box-r48 | 1677.7 | 91.2 | 151.4 | 5.4 | 1429.7 | 70.6 | 1.7437 | 0.3266 | 7.125 | 0.2388 | 6964/13924 |
| dev-box-r96 | 3251.6 | 1099.2 | 224.1 | 13.2 | 1915.1 | 118.2 | 1.6841 | 0.3252 | 6.4375 | 0.2321 | 28068/56132 |
| dev-box-r144 | 4745.7 | 2860.1 | 507.7 | 15.7 | 1362.2 | 177.3 | 1.6618 | 0.3367 | 6.2083 | 0.2278 | 63310/126616 |
| val-cylinder-r96 | 21873.9 | 1600.8 | 279.8 | 12.0 | 19981.3 | 97.8 | 0.9293 | 0.6957 | 4.0 | 0.1041 | 47512/95020 |
| val-stepped-r96 | 3084.7 | 828.1 | 169.8 | 7.8 | 2079.0 | 93.6 | 2.3412 | 0.3156 | 6.0625 | 0.3042 | —/59092‡ |
| heldout-rotated-stepped-r96 | 2555.1 | 781.3 | 175.1 | 7.8 | 1590.9 | 105.9 | 2.4856 | 0.3789 | 6.0625 | 0.3196 | 32497/64994 |
| heldout-tall-cylinder-r96 | 25657.7 | 478.4 | 118.0 | 5.6 | 25055.8 | 60.4 | 0.8409 | 0.8226 | 7.0 | 0.0884 | 17836/35668 |

\* `render_ms` = synthetic fixture rendering (harness cost, not
reconstruction). † `eval_ms` = scoring overhead (not reconstruction).
‡ val-stepped verts 29548. All rows: watertight=true, components=1,
`reopen_ok`=true.

Held-out checks (means vs bounds): rotated-stepped chamfer 2.4856 <
4.0, compl 0.3789 > 0.15, bbox 6.0625 < 12.0, vol 0.3196 < 0.6 →
PASS; tall-cylinder chamfer 0.8409 < 3.0, compl 0.8226 > 0.25, bbox
7.0 < 10.0, vol 0.0884 < 0.35 → PASS.

### 3.2 Mesh CLI smoke figures (`mesh/smoke_out/`)

`recon/manifest.json` timing (box, r96): load 6.69 / carve 595.10 /
surface 149.62 / export 8.27 / total 759.69 ms. Evaluate reports
(n=20000 samples, tau=1.0 mm, seed=0):

| Scene | chamfer_sym | compl@1mm | bbox_max_err | vol_rel_err | verts/faces |
|---|---|---|---|---|---|
| box (`recon/metrics.json`) | 1.6086 | 0.3802 | 6.4375 | 0.2321 | 28068/56132 |
| cylinder (`rec_cylinder/metrics.json`) | 0.8450 | 0.7334 | 4.0 | 0.1047 | 20936/41868 |
| stepped (`rec_stepped/metrics.json`) | 2.2732 | 0.3751 | 6.4063 | 0.3034 | 13036/26068 |
| cavity (`rec_cavity/metrics.json`) | 2.6376 | 0.2801 | 6.0938 | **1.5527** | 12342/24680 |

The cavity row is the expected visual-hull failure mode: the interior
void never appears in any silhouette, so the hull fills it
(vol_rec 88068 vs vol_gt 34500 mm³). This is a method limit, not a bug.

### 3.3 CAD timings: PENDING_ENV in this worktree

The CAD worker venv does not exist here
(`assembly-reconstruction/cad/.venv` absent; `Test-Path
cad/.venv/Scripts/python.exe` → False), and installing the
~1.2 GB CadQuery/OCP toolchain is out of scope for a read-only study.
Therefore **no CAD kernel timing was measured in this worktree** and
no CAD-vs-mesh runtime comparison is made.

Read-only check that DID run here (stdlib-only, no writes, no install):

```powershell
python assembly-reconstruction/cad/cadwork.py validate --spec assembly-reconstruction/cad/fixtures/synthetic/plate_hole.json
# → {"ok": true, "features": ["base_plate", "hole_1"], "solids": ["base_plate"]}
```

Committed prior-session CAD figures (`cad/EVIDENCE.md §2.2`, worktree
`Anastylos-agentcam-cad`, 2026-10-29 — cited, NOT re-measured here):
cold build+export 2.33 s (budget 60 s); warm rebuild 0.004 s; invalid
spec pre-kernel reject 0.15 s; plate 80×50×6 r3 volume 23830.354 mm³,
STEP 19014 bytes, fresh-reopen volume rel err ~1.7e-16; +1 mm regen
dx=1.0 exact with y/z unchanged; 1045-edge preview; peak working set
331.7 MB. These describe compile cost (C2–C4), never estimation (C1).

## 4. Hybrid mesh-to-CAD proposal (architecture note only; no CAD changes)

Mesh as the dimension-extraction and visualization front-end feeding
operator-confirmed parametric features:

1. **Mesh first (fast visual feedback).** Run the mesh path on the
   real observation set (`ingest-spatial` → carve → PLY/STL +
   `mesh-evidence-v0` manifest). The operator gets a watertight
   hull in seconds-to-minutes plus bbox dims as first dimension hints.
2. **Estimate features from mesh + images.** An estimator (human
   today; machine later) fits v1 features (box/cylinder/through-hole
   + frames) to the mesh and the source views, writing a
   `cad-feature-model-v1` spec whose `dataset_sha256` pins the same
   observation set and whose `evidence_ids`/`uncertainty_mm` cite the
   mesh manifest and the views used. Mesh bbox/chamfer residuals
   become the fit quality signal.
3. **Compile + confirm.** `cadwork.py run` compiles the spec; the
   operator reviews preview/validation, applies `apply-edits`
   corrections (revision+1, base-hash pinned), and the provenance
   sidecar records estimated-vs-operator-specified per parameter.
   `merge-estimates` folds later re-estimates in without ever
   overwriting operator values.
4. **Protected revisions + provenance preserved.** Mesh `_rN`
   no-overwrite outputs (`mesh/anastylos_mesh/export.py:54-70`) and
   CAD `--out` no-overwrite revisions (`cad/anastylos_cad/worker.py:58-69`)
   stay independent; linkage is by hash citation
   (`dataset_sha256`, manifest SHA-256 pins), never by mutation.
   Append-only discipline holds on both sides.

**Mode-selection path (note only):** a future orchestrator offers
`Mesh` (visualization/rough dims only), `Parametric` (spec-first,
mesh skipped when dims come from calipers/drawings), or `Both`
(hybrid above). Mode is recorded in run provenance; it must never
change what either pipeline outputs for identical inputs.

## 5. Explicit non-equivalence statement

The three tasks below are **different tasks**. Results and runtimes
do not transfer between them without the accounts in §2.3:

- **Task A — mesh reconstruction:** images + masks + calibrated poses
  → sampled occupancy mesh (fidelity bounded by silhouettes and
  voxel size; no parameters, no editability).
- **Task B — feature estimation:** observations → validated
  `cad-feature-model-v1` spec (dims, frames, topology, uncertainty).
  This is where real-world accuracy is won or lost on the CAD side;
  it is currently manual/synthetic and unmeasured.
- **Task C — trusted CAD compilation:** validated spec → genuine
  STEP solid + FCStd tree + evidence bundle (deterministic,
  hash-pinned, editable; geometry exact to the spec).

Task A output can *inform* Task B (dimension hints, fit residuals),
and Task C *compiles* Task B output. A "mesh vs CAD" benchmark that
times A against C while ignoring B measures two different things.

## 6. Verdict: PARTIAL + missing evidence

**Verdict: PARTIAL.** Both sides report synthetic-only numbers; the
comparison protocol in §2 is specified but not executed.

Missing evidence, in dependency order:

1. Real observation set: one rigid matte part + caliper ground truth
   + N calibrated phone photos + real masks (blocks M1/M2 and C1).
2. CAD worker env in this worktree (or a recorded remote run) for
   C2–C4 timings on the same machine class as the mesh numbers.
3. Feature-spec estimation procedure + measured C1 time for the
   comparison object (blocks end-to-end CAD).
4. Mesh `ingest-spatial` READY run on the real set (currently
   synthetic fixtures only).
5. Caliper-vs-mesh and caliper-vs-STEP dimension residuals on the
   same object (blocks the fidelity axis).
6. FreeCAD UI open/edit/recompute session (blocks fabrication-path
   confidence; already PENDING_EVIDENCE in `cad/EVIDENCE.md §2.4`).

No performance claim beyond the measured tables in §3 is made.
