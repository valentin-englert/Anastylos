# Physical acquisition checklist — mesh experiment

Protocol for the first real-hardware mesh run. Goal: a sealed capture
set + operator masks that reach `ingest-spatial` READY, so real-image
metrics can be compared against the synthetic baselines in
[EVIDENCE.md](EVIDENCE.md). Nothing here fabricates evidence: any
missing item yields PENDING/ERROR, never a guessed mesh.

## Object + ground truth

- [ ] Object is **matte** (no specular/mirror/transparent surfaces;
      silhouettes must be unambiguous), **rigid** (no flex between
      views — one capture is one pose epoch), and **asymmetric**
      (offset feature or marking so rotation sense is checkable, cf.
      `tests/test_case_c_stepped.py:58-81`).
- [ ] Object sits on the calibration board, fully inside the board area.
- [ ] **Caliper sealing:** measure the object's bounding dimensions with
      calipers BEFORE photography; record values + instrument ID in the
      session notes. These are the ground-truth dims for `evaluate`.
      Do not "refine" them after seeing the mesh.

## Capture geometry (8-16 views)

- [ ] 8-16 unaltered stills, full azimuth spread (e.g. every 45° for 8
      views, every ~22.5° for 16).
- [ ] Elevation diversity is **mandatory**, including LOW-elevation
      views a few degrees above the board plane (aim ≈5° as the lowest
      bracket, plus mid ≈25° and high ≈45°). Reason: the Case-A z-floor
      finding ([EVIDENCE.md](EVIDENCE.md#open-fail-case-a)) proves
      high-only rings leave height unconstrained (7.125 mm z-error on a
      30 mm box); synthetic probes show ≈5° cuts that to 3.0 mm and
      2° to <1 mm.
- [ ] Stay OUT of the grazing regime: never shoot at board level
      (`el=0` hard-fails `INVERTED_BOARD_Z`; ≤2° yields thin-sliver
      silhouettes that real mask noise will corrupt). If a low view
      looks edge-on in preview, raise the camera.
- [ ] Object must not move between views (single `object_pose_epoch`;
      mixed epochs reject with `MIXED_POSE_EPOCH`, no registration
      path exists — `anastylos_mesh/carve.py:133-149`).
- [ ] All views share one camera + one calibration profile (staged
      profile is mandatory: `PROFILE_UNSTAGED` gates otherwise).

## Board / calibration visibility

- [ ] Calibration board (or ChArUco markers) visible in EVERY
      reconstruction photo — poses come from the board solution, and
      every sidecar must validate `accept` + `metric_ready`.
- [ ] No motion blur, no rolling-shutter wobble, no over/underexposure
      that eats the silhouette edge. Fixed focus and fixed exposure
      across the set if the camera allows it.

## Calibration photos vs reconstruction photos

- [ ] **Calibration photos** (many views of the board / ChArUco sheet
      for the camera-profile solve) are a SEPARATE set from the
      **reconstruction photos** (the 8-16 object views above). Do not
      mix them: the calibration set builds the profile; the
      reconstruction set is the sealed capture set the adapter consumes.
- [ ] Reconstruction photos must be **unaltered stills**: no crop,
      no rescale, no re-encode, no beautification. The adapter re-hashes
      staged bytes against receipt pins (`IMAGE_REHASH_MISMATCH`
      gates); any edit breaks the seal.

## Per-image pose + masks

- [ ] Every image gets a sealed sidecar with a production pose
      (`T_board_camera`), `reference_to_image`, and an
      `object_pose_epoch_ref` — via the normal calibration/capture
      pipeline, not hand-written.
- [ ] **Operator masks:** one `<image_sha256>.png` + `.json` pair per
      image in `mask_dir`, following
      `tests/fixtures_spatial/mask-meta.synthetic-template.json`
      (`mask_sha256`, `provenance: manual|auto`, `editor`,
      `edited_bool`). Trace the true silhouette; when in doubt, exclude
      ambiguous pixels and note it — over-inclusive masks inflate the
      hull, under-inclusive masks over-carve it. `synthetic` provenance
      is rejected on the real path (`MASK_PROVENANCE_NOT_OPERATOR`).
- [ ] Mask PNG resolution must equal the image resolution exactly
      (`MASK_PNG_RESOLUTION_MISMATCH` gates).

## ingest-spatial run

```powershell
cd assembly-reconstruction/mesh
$PY = ".venv/Scripts/python.exe"
& $PY -m anastylos_mesh.cli ingest-spatial --dataset <dataset_dir> --masks <mask_dir> --config <grid-carve.json> --out <out_dir>
echo "exit=$LASTEXITCODE"
```

- [ ] `dataset_dir` layout: `capture_set.json` + `sidecars/` +
      `packages/` + `spatial/` (profiles required; layouts/boards
      optional; see `anastylos_mesh/spatial_adapter.py:9-27`).
- [ ] `grid-carve.json`: `grid.bounds_mm` ROI bracketing the object
      (measure from the board frame; there is no production ROI
      estimator) + `grid.voxel_size_mm` (start ≈1-2 mm; respect the
      256/dim and 8M-voxel caps) + optional `carve` overrides.
- [ ] Exit 0 (READY): `manifest.json` + `mesh.ply/stl` written; record
      the manifest sha256 pins in the session notes.
- [ ] Exit 1 (PENDING): read `<out_dir>/ingest-status.json` gates and
      fix the named evidence (missing sidecar, unstaged profile,
      missing mask...). Do NOT hand-edit a manifest into existence:
      PENDING writes no manifest by design.
- [ ] Exit 2 (ERROR): typed validation failure; the JSON error names
      the code — see [ARCHITECTURE-NOTES.md](ARCHITECTURE-NOTES.md#error-taxonomy).

## No-fabrication warnings

- NEVER photograph a screen/render and pass it off as a physical view.
- NEVER copy a synthetic fixture hash into a real sidecar, rename a
  synthetic PNG to a real sha256, or reuse `tests/fixtures_spatial/`
  content as real evidence — it is labelled
  `SYNTHETIC_TEST_FIXTURE_ONLY` for exactly this reason.
- NEVER "complete" a PENDING ingest by writing `mesh-evidence-v0`
  outputs by hand. The manifest requires real mesh bytes; without them
  there is no manifest.
- Report `metric_status`, `coverage_class`, and every gate/note
  alongside the numbers. A watertight mesh with `metric_status:
  invalid` is a visualization, not a measurement
  (`tests/test_case_j_units.py:103-110`).
