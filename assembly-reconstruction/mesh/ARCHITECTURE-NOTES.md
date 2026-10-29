# Architecture notes — mesh experiment

Short companion to the code. Conventions, the frozen carve rule, the
full error taxonomy, and determinism/security bounds — each with
file:line sources. Nothing here overrides the code or any production
contract.

## Conventions mirrored (file:line)

The mesh core mirrors production geometry EXACTLY: no axis flips, no
silent inverses. Source inventory lives in
`anastylos_mesh/geometry.py:1-33`:

- Board frame: x right, y down, z into the board; mm; column vectors,
  right-handed frames (from `calibration/anastylos/layout.py` and
  `calibration/spatial/anastylos_spatial/geometry.py:1-9`).
- `T_board_camera` maps camera coords into board coords; camera center
  is its translation part and must sit at z < 0
  (`geometry.py:183-185`; `cameras.py:105-106`).
- Brown-Conrady OpenCV model `[k1,k2,p1,p2,k3]`, applied to normalized
  coords BEFORE the `reference_to_image` 3x3 map
  (`geometry.py:188-205`, `:221-260`).
- `K_image = A @ K_reference` fold; K row-major, fx=K[0], fy=K[4]
  (`geometry.py:252-259`).
- Tolerances: ortho 1e-6, det 1e-6, bottom-row 1e-9, singular-det
  1e-12, image dims 1..32768 (`geometry.py:41-45`).
- Pixel convention `top_left_pixel_center_0_0`, nearest-pixel sampling
  via floor (`carve.py:204-210`; `cameras.py:3-9`).
- Length unit mm-only on `Camera` and `VoxelGrid`
  (`cameras.py:111-112`; `carve.py:52-53`).

Production reuse inventory for the adapter:
`anastylos_mesh/spatial_adapter.py:42-64` (validator, profiles, seam,
journal functions with line pins) plus the 11 documented assumptions
(`spatial_adapter.py:66-107`).

## Occupancy rule + coverage policy

**Occupancy (frozen G0)** — `carve.py:3-5`, `:219-220`: a voxel is
occupied iff `observed_count >= min_observed AND foreground_count ==
observed_count`. Any observed background carves the voxel;
outside-image and behind-camera projections are UNOBSERVED (no evidence
either way). Every manifest is therefore marked
`extra.reconstruction_class == "silhouette-constrained"`
(`export.py:125-127`): visual-hull occupancy can fill concavities,
never recover them.

**Coverage gates** (fail-closed, before carving):

- Minimum views (`TOO_FEW_VIEWS`), `min_observed` sanity
  (`BAD_MIN_OBSERVED`, `MIN_OBSERVED_EXCEEDS_VIEWS`) — `carve.py:168-175`.
- Minimum baseline: max angular separation of camera centers about the
  ROI center must reach `min_baseline_deg` (default 5°) —
  `carve.py:115-130`, `:180-185`.
- Single pose epoch: mixed epochs reject (`MIXED_POSE_EPOCH`) —
  `carve.py:133-149`.
- Manifest coverage label: fewer than `MIN_VIEWS_FOR_COMPLETE = 4`
  views derives `partial`; explicit `complete` on a sparse run raises
  `COVERAGE_OVERCLAIM` — `export.py:135-156`.
- Declared-units gate: non-`mm` scenes record
  `metric_status: invalid` (or raise `WRONG_LENGTH_UNIT` under strict
  mode) — `export.py:129-145`.

## Error taxonomy

Base `MeshError` (`geometry.py:48`); subclasses `MeshProvenanceError`,
`MeshCalibrationError`, `MeshCoverageError`, and
`DegenerateBaselineError(MeshCoverageError)` (`geometry.py:52-65`).

**MeshProvenanceError** (evidence linkage):

| Code | Raised at |
|---|---|
| `BAD_PNG_MAGIC`, `MASK_SHA256_MISMATCH`, `MASK_IMAGE_LINK_MISMATCH`, `MASK_POSE_EPOCH_MISMATCH`, `BAD_MASK_PROVENANCE`, `MASK_PNG_RESOLUTION_MISMATCH`, `MASK_PNG_UNREADABLE` | `masks.py:32-96` |
| `MIXED_POSE_EPOCH` | `carve.py:146` |
| `SEALED_MANIFEST_MISSING`, `SEALED_MANIFEST_MALFORMED:*`, `SEALED_MANIFEST_HASH_MISMATCH`, `DATASET_DIR_MISSING`, `PRODUCTION_SEAM_UNAVAILABLE:*` | `spatial_adapter.py:193-217`, `:340-363`, `:792`, `:800` |
| `MASK_DECODER_UNAVAILABLE:PIL`, `MASK_PNG_UNREADABLE:*`, `MASK_SIDECAR_MALFORMED:*`, `MASK_PROVENANCE_NOT_OPERATOR`, `MIXED_MASK_PROVENANCE:*` | `spatial_adapter.py:633-680`, `:671`, `:885` |

**MeshCalibrationError** (fail-closed geometry):

| Code | Raised at |
|---|---|
| `NONFINITE_TRANSFORM`, `BAD_TRANSFORM_BOTTOM_ROW`, `NOT_ORTHONORMAL`, `REFLECTION_DET_MINUS_ONE`, `BAD_DETERMINANT`, `BAD_REFERENCE_TO_IMAGE`, `SINGULAR_IMAGE_TRANSFORM`, `MIRRORED_IMAGE_TRANSFORM` | `geometry.py:165-217` |
| `NONFINITE_K`, `NONPOSITIVE_FOCAL_LENGTH`, `BAD_DISTORTION`, `BAD_IMAGE_SIZE`, `BAD_REFERENCE_SIZE`, `INVERTED_BOARD_Z`, `BAD_IMAGE_SHA256`, `BAD_POSE_EPOCH`, `BAD_LENGTH_UNIT` | `cameras.py:92-112` |
| `MASK_CAMERA_SIZE_MISMATCH`, `MASK_FOREGROUND_SHAPE_MISMATCH`, `MASK_PREPROCESS_NOT_GRAYSCALE` | `masks.py:90-121` |
| `POSE_TRANSFORM_MISSING` | `spatial_adapter.py:604` |
| `WRONG_LENGTH_UNIT:*` | `export.py:145` |
| `PNG_ENCODE_BAD_SHAPE`, `PNG_DECODE_*`, `BAD_CYLINDER_PARAMS`, `EMPTY_SCENE_SURFACE`, `BAD_SAMPLE_COUNT`, `BAD_SCENE_UNITS:*`, `BAD_BOX_PARAMS`, `BAD_STEPPED_PARAMS`, `STEPPED_SAMPLER_STALL`, `BAD_CAVITY_PARAMS`, `UNKNOWN_SCENE:*`, `LOOK_AT_COINCIDENT`, `FIXTURE_COUNT_MISMATCH`, `RING_TOO_FEW_VIEWS`, `RING_BAD_RADIUS`, `RING_BAD_FOCAL`, `RING_BAD_K` | `synthetic.py` |

**MeshCoverageError** (capacity/coverage) + `DegenerateBaselineError`:

| Code | Raised at |
|---|---|
| `BAD_GRID_LENGTH_UNIT`, `BAD_VOXEL_SIZE`, `BAD_GRID_BOUNDS`, `EMPTY_GRID`, `GRID_DIM_CAP_EXCEEDED`, `GRID_VOXEL_CAP_EXCEEDED`, `CAMERA_MASK_COUNT_MISMATCH`, `TOO_FEW_VIEWS`, `BAD_MIN_OBSERVED`, `MIN_OBSERVED_EXCEEDS_VIEWS`, `DEGENERATE_BASELINE:*` | `carve.py:52-70`, `:168-185` |
| `BAD_COVERAGE_CLASS:*`, `COVERAGE_OVERCLAIM` | `export.py:154-156` |
| `EMPTY_RECONSTRUCTION`, `BAD_METRIC_SAMPLES`, `BAD_METRIC_TAU` | `metrics.py:27-51` |
| `SURFACE_GRID_SHAPE_MISMATCH`, `EMPTY_OCCUPANCY`, `EMPTY_SURFACE_AFTER_CLEANUP` | `surface.py:57-88` |
| `BAD_CONFIG:not-an-object`, `GRID_CONFIG_MISSING`, `BAD_GRID_BOUNDS`, `BAD_CARVE_PARAMS*` | `spatial_adapter.py:713-744` |

**Non-mesh exceptions**: `ValueError(CAMERA_MASK_COUNT_MISMATCH |
MASK_SHA256_MISMATCH:* | IMAGE_SHA256_MISMATCH:* | UNSUPPORTED_MESH_SUFFIX:*
| BAD_REPEATS | HELDOUT_BOUNDS_MISSING:*)` (`cli.py:141-149`;
`export.py:82`; `benchmark.py:175`, `:350`); `FileExistsError`
`REFUSE_OVERWRITE` (`export.py:64`; benchmark re-run guard
`benchmark.py:353-357`); `RuntimeError` `REOPEN_FAILED:*`
(`cli.py:274`; `benchmark.py:217`).

**Adapter PENDING gates** (per-image verdicts, not exceptions;
`spatial_adapter.py:396-548`): `RECEIPT_MALFORMED`,
`RECEIPT_NOT_QUALIFIED`, `NO_SIDECAR`, `SIDECAR_FILE_MISSING`,
`SIDECAR_MALFORMED`, `SIDECAR_HASH_MISMATCH`,
`RECEIPT_SIDECAR_BINDING_MISMATCH`, `IMAGE_REHASH_MISMATCH`,
`IMAGE_UNRESOLVED`, `STAGED_BINDING_CORRUPT`, `PROFILE_UNSTAGED`,
`VALIDATOR_EXCEPTION`, `VALIDATOR_NOT_QUALIFIED`,
`OBJECT_EPOCH_MISSING`, `BOARD_BINDING_CORRUPT`, `BOARD_REJECTED`,
`BOARD_ID_MISMATCH`, `NO_RECEIPTS`. Advisory notes (never gates):
`LAYOUT_UNSTAGED`, `EPOCHS_UNSTAGED`, `BOARD_UNSTAGED`.

**CLI exit-2 JSON errors** (`cli.py:333-357`, `:429-433`):
`CONFIG_UNREADABLE:*`, `OUT_DIR_UNWRITABLE:*`, `STATUS_UNWRITABLE:*`,
plus any `MeshError` rendered as `TYPE:detail`.

## Determinism + security bounds

- No RNG in render/carve/surface/export; `evaluate` sampling is
  seeded (`seed`, GT uses `seed+1` — `metrics.py:59-60`). Benchmark
  reps are bitwise-identical except wall-clock timing and tracemalloc
  peaks (`benchmark.py:386-391`); verified: E2E re-run reproduced
  committed hashes exactly ([EVIDENCE.md](EVIDENCE.md#claim-index)).
- Voxel caps bound memory: 256/dim, 8M voxels (`carve.py:33-34`).
- No silent overwrites: `resolve_output` raises or writes `_rN`
  revisions (`export.py:54-70`); benchmark refuses to re-run into an
  occupied out dir (`benchmark.py:353-357`).
- Path confinement for real evidence comes from the production seam
  resolvers (`packages/` confinement, raw-bytes re-hash), reused
  read-only — the adapter adds no path logic of its own
  (`spatial_adapter.py:444-455`).
- Fresh-process reopen probes use fixed argv + 300 s timeout, and
  report (never swallow) failures (`cli.py:259-275`).
- Python stays offline: no network imports; PNG codec is stdlib+zlib
  (`synthetic.py:63-130`); real-mask PNG decode is PIL-only
  (`spatial_adapter.py:627-642`).
