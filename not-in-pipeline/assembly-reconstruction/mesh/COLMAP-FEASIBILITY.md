# COLMAP known-pose feasibility — mesh visual-hull geometry

Status: investigation only. No production code touched, nothing installed,
no images uploaded, no equivalence claimed between COLMAP / visual-hull / CAD.
Scope: map verified ANASTYLOS geometry onto COLMAP inputs on paper, record
the environment, and attempt a bounded run only if a backend is already
usable with zero toolchain compilation.

Verdict: **BLOCKED — no usable COLMAP backend on this machine**
(see "Attempt log / gates"). Latent feasibility once unblocked:
sparse path is CPU-runnable (FEASIBLE-SPARSE after backend install);
dense path additionally needs a CUDA-enabled COLMAP build
(BLOCKED-DENSE until then).

Date: 2026-10-29. Worktree: `C:/Users/HP/Documents/Anastylos-mesh-experiment-01`.

## 1. Environment findings (exact outputs, this machine)

| Check | Result |
|---|---|
| `colmap` on PATH | **NOT FOUND** (`Get-Command colmap` → no match) |
| `nvcc` | present: `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin\nvcc.exe`, release 12.8, V12.8.61 |
| `nvidia-smi` | driver 582.67, CUDA Version: 13.0 |
| GPU | NVIDIA Quadro P2000, 5120 MiB, compute capability **6.1** (plus Intel UHD 630 iGPU) |
| CPU / RAM | Intel i7-8700K, 6C/12T, 3696 MHz; 34181783552 B (~32 GiB) RAM |
| OS | Windows 10 Pro 2009, NT 10.0.26300.0 |
| System Python | 3.12.10 |
| Mesh venv (`assembly-reconstruction/mesh/.venv`) | present; `pycolmap` import → **None (not installed)**; pins: numpy 2.5.4, opencv-python-headless 5.0.0.93, scipy 1.18.1, scikit-image 0.26.0, trimesh 5.2.0, pillow 13.0.0, imageio 2.38.1, networkx 3.7, lazy-loader 0.6, packaging 26.3, tifffile 2026.10.15 |
| PyPI index (metadata only, no install) | `pycolmap` latest **4.2.1**; wheel `pycolmap-4.2.1-cp312-cp312-win_amd64.whl` exists (matches venv CPython 3.12 / win_amd64); `requires_python >= 3.10` |

## 2. Doc references (web-for-docs only)

COLMAP docs version cited: **4.3.0.dev0, ea470c0 (2026-10-28)**
(page footer); source refs are `colmap/main` branch at fetch time.

- Camera models (which model to use, 3-step projection, corner
  pixel convention): <https://colmap.github.io/cameras.html>
- Sparse/dense text format (`cameras.txt`, `images.txt`,
  `points3D.txt`, pose = world-to-camera Hamilton quaternion +
  translation, center = −Rᵀ·T, camera X right / Y down / Z front):
  <https://colmap.github.io/format.html>
- Known-pose workflow (hand-written sparse model, feature
  re-extraction, `point_triangulator`, dense via undistorter +
  PatchMatch + fusion, manual depth-range variant):
  <https://colmap.github.io/faq.html> ("Reconstruct sparse/dense
  model from known camera poses")
- Intrinsics handling (ImageReader priors, `single_camera`,
  fix-intrinsics `refine_*` groups, cx/cy +0.5 OpenCV→COLMAP
  conversion): <https://colmap.github.io/faq.html>
  ("Set known camera intrinsics", "Fix intrinsics")
- CLI commands/options (`database_creator`, `feature_extractor`
  `--ImageReader.*`, `exhaustive_matcher`, `mapper`,
  `point_triangulator`, `image_undistorter`, `patch_match_stereo`,
  `stereo_fusion`, CPU fallbacks `--FeatureExtraction.use_gpu 0` /
  `--FeatureMatching.use_gpu 0`): <https://colmap.github.io/cli.html>
- Install (prebuilt Windows `COLMAP.bat` exists upstream, unsigned;
  CUDA ≥ 11.X recommended dep; Windows build via
  `vcpkg install colmap[cuda,tests]:x64-windows`; repo packages lack
  CUDA/HIP): <https://colmap.github.io/install.html>
- Tutorial (CPU extraction/matching fallbacks; dense stereo is the
  GPU stage with GPU-memory failure modes):
  <https://colmap.github.io/tutorial.html>
- Exact OPENCV / FULL_OPENCV parameter order and distortion math:
  <https://github.com/colmap/colmap/blob/main/src/colmap/sensor/models/opencv.h>
- Pixel-corner convention in source:
  <https://github.com/colmap/colmap/blob/main/src/colmap/sensor/models.h>
- Pose-freeze flag `fix_existing_frames` ("If reconstruction is
  provided as input, fix the existing image poses"):
  <https://github.com/colmap/colmap/blob/main/src/colmap/sfm/incremental_mapper.h>
- pycolmap wheels (Windows CPU-only; CUDA12 wheels Linux-only;
  `patch_match_stereo` "requires compilation with CUDA"; GPU MVS
  "not available with the PyPI wheels"): <https://pypi.org/project/pycolmap/>
  (README) and <https://pypi.org/pypi/pycolmap/4.2.1/json> (file list).

ANASTYLOS primary sources (this repo):

- `calibration/spatial/anastylos_spatial/profiles.py:22-23`:
  `CAMERA_MODEL = "brown_conrady_5"`,
  `PIXEL_CONVENTION = "top_left_pixel_center_0_0_x_right_y_down"`;
  lines 100-113: K 9 finite with k[0],k[4] > 0, distortion 5 finite.
- `calibration/spatial/anastylos_spatial/geometry.py:1-9`:
  column vectors, right-handed, mm; board x right / y down /
  **z into the paper**; camera C is the **OpenCV optical frame**;
  lines 117-136: Brown-Conrady `[k1,k2,p1,p2,k3]`, depth > 0 in front;
  line 150-152: `K_image = A @ K_reference` (frozen crop/scale mapping).
- `calibration/spatial/anastylos_spatial/validator.py:252-266`:
  pixel convention gate, `reference_to_image` 3x3 finite / det > 0 /
  |det| > 1e-12, `distortion_before_image_transform is True`;
  lines 306-333: `T_board_camera` mm-only, rigid, det +1,
  camera-center z < 0.
- `assembly-reconstruction/mesh/anastylos_mesh/cameras.py`:
  `Camera` dataclass (K reference frame, dist 5, T mm, sha256, epoch).
- `assembly-reconstruction/mesh/anastylos_mesh/synthetic.py:600-601`:
  synthetic rays sampled at `arange + 0.5` with `cx = width/2`;
  lines 616-617: flat two-tone evidence images (bg 32 / fg 220).

## 3. Exact convention mappings (no implementation)

World frame choice: COLMAP world := ANASTYLOS board frame, metres
unchanged. COLMAP is unit-agnostic, so millimetres flow through
verbatim (triangulated points, depth maps, fused cloud all inherit mm).

### 3.1 Intrinsics: brown_conrady_5 → FULL_OPENCV (lossless)

COLMAP `FULL_OPENCV` parameter order (source-verified):
`fx, fy, cx, cy, k1, k2, p1, p2, k3, k4, k5, k6`, with rational radial
`(1+k1·r²+k2·r⁴+k3·r⁶)/(1+k4·r²+k5·r⁴+k6·r⁶)` plus standard OpenCV
tangential terms. Setting `k4 = k5 = k6 = 0` reduces it **exactly** to
the ANASTYLOS `[k1,k2,p1,p2,k3]` model (same formula, same application
point: normalized coords, before K). Mapping per view:

```text
cameras.txt:  <id> FULL_OPENCV <width> <height> <fx> <fy> <cx'> <cy'> \
              <k1> <k2> <p1> <p2> <k3> 0 0 0
```

- `fx = K[0]`, `fy = K[4]` (after §3.3 fold), distortion coefficients
  copied verbatim, `k4..k6 = 0`. No reordering: ANASTYLOS
  `[k1,k2,p1,p2,k3]` slots line up with FULL_OPENCV slots 5-9.
- Principal-point shift depends on the K source convention:
  - Real calibrated profiles (OpenCV pixel-center convention,
    `top_left_pixel_center_0_0`): `cx' = cx + 0.5`,
    `cy' = cy + 0.5` (per COLMAP FAQ; fx/fy/distortion unaffected).
  - Synthetic mesh fixtures (`camera_ring` uses `cx = w/2, cy = h/2`
    with corner-convention ray sampling at pixel index + 0.5):
    already in COLMAP convention, shift `+0.0`.
  - Rule: the shift must preserve the pixel-index→ray mapping; verify
    per source by projecting one known 3D point before trusting it.
- `WIDTH/HEIGHT` = observed (post-crop) image size, not the reference size.
- COLMAP `OPENCV` (8 params, no k3) would **drop k3 → lossy**.
  Do not use it for brown_conrady_5.

### 3.2 Extrinsics: T_board_camera (mm) → images.txt (lossless)

- Invert the rigid transform: `T_C_B = rigid_inverse(T_board_camera)`
  (rotation block transposed; `t = −R_C_B · C`). No axis flips: both
  frames are x-right / y-down / z-forward optical frames.
- Rotation → Hamilton quaternion `(QW, QX, QY, QZ)` (Eigen convention);
  translation `(TX, TY, TZ)` = translation part of `T_C_B`, mm verbatim.
- `images.txt` line 1:
  `<image_id> <qw> <qx> <qy> <qz> <tx> <ty> <tz> <camera_id> <name>`;
  line 2 (POINTS2D) left **empty** — features are recomputed by
  `feature_extractor` after the model is written (FAQ known-pose recipe).
- `points3D.txt`: header only, zero points.
- `image_id` (1-based) must equal the image's row id in `database.db`
  (insertion order); `camera_id` groups views sharing one
  FULL_OPENCV parameter set (`--ImageReader.single_camera 1` when
  one physical camera took all views).

### 3.3 reference_to_image crop handling (exact iff affine)

ANASTYLOS projects reference pixels then applies the 3×3
`reference_to_image` A (distortion runs before A). COLMAP has a single
K, so fold `K_img = A @ K_ref` **before** the §3.1 conversion, then
apply the +0.5 shift (real calibrations) to the folded cx/cy.

- Exact iff A is affine with last row `[0,0,1]` (crop translate +
  (an)isotropic scale): the product stays a valid pinhole K.
- The validator permits general projective A (det > 0 only). A
  projective A is **not representable** in COLMAP K → hard gate:
  reject the view for the COLMAP path (do not approximate).
- Also verify A maps `reference_image_size_px` bounds onto the
  observed `width × height` bounds; mismatch → reject (fail closed).

### 3.4 Per-image identity / hash preservation

COLMAP has no hash field; provenance must live alongside it:

- Copy each image's **exact bytes** (no re-encode) into the COLMAP
  image dir under a content-derived name (e.g. `<image_sha256>.png`)
  and use that string as the images.txt `NAME`.
- Ship a sidecar manifest mapping `NAME → image_sha256`
  (+ receipt/sidecar ids) and re-hash bytes before invoking COLMAP.
- COLMAP never sees the production bytes in place; the copy is a
  documented derivation, and the manifest is the provenance link.

### 3.5 Freezing poses (no bundle adjustment on known poses)

Primary path — `point_triangulator` performs triangulation only, no BA,
so imported poses are frozen by construction (FAQ known-pose recipe):

```text
colmap feature_extractor  --database_path $P/database.db --image_path $P/images \
  --ImageReader.camera_model FULL_OPENCV --ImageReader.camera_params "<12 csv>" \
  --ImageReader.single_camera 1
colmap exhaustive_matcher --database_path $P/database.db
colmap point_triangulator  --database_path $P/database.db --image_path $P/images \
  --input_path  $P/sparse-manual --output_path $P/sparse-triangulated
```

Notes: copy final intrinsics from `cameras.txt` into the database
(pycolmap DB API) when distortion is large, before matching (FAQ);
`--ImageReader.camera_params` spelling takes the 12 FULL_OPENCV values.

Alternative — continue with the mapper on the manual model:

```text
colmap mapper --database_path $P/database.db --image_path $P/images \
  --input_path $P/sparse-manual --output_path $P/sparse \
  --Mapper.fix_existing_frames 1
```

Intrinsics freeze: Bundle Adjustment `refine_*` groups off
(FAQ "Fix intrinsics", GUI labels `refine_focal_length`,
`refine_principal_point`, `refine_extra_params`). Exact CLI spellings
were not re-verified from `main`-branch source in this pass — confirm
with `colmap mapper -h` on the executing machine before relying on them.

Dense from known poses (for reference; not runnable here, see §5):

```text
colmap image_undistorter --image_path $P/images --input_path $P/sparse-triangulated \
  --output_path $P/dense
colmap patch_match_stereo --workspace_path $P/dense     # needs CUDA build
colmap stereo_fusion --workspace_path $P/dense --output_path $P/dense/fused.ply
```

Sparse-optional variant needs manual `--PatchMatchStereo.depth_min/max`
plus `--StereoFusion.min_num_pixels 1` and manual source-image lists
(FAQ).

## 4. Inputs / outputs / assumptions comparison (no equivalence claimed)

| Aspect | Mesh visual-hull (this repo) | COLMAP sparse (known-pose path) | COLMAP dense (MVS) | CAD reference |
|---|---|---|---|---|
| Inputs | Calibrated `Camera` set (K/dist/T/sha/epoch) + per-view operator masks + voxel grid config; single pose epoch | Same cameras converted to `cameras.txt`/`images.txt` + raw image bytes + feature matches | Undistorted images + known poses (+ sparse model for view selection, else manual depth range + source lists) | Mechanical design dimensions (Z-up via frozen 180°-about-x adapter) |
| Method | Conservative silhouette carving → surface → export; no photometry | SIFT (or ALIKED/LoMa) detect → match → triangulate into fixed poses; no BA on frozen path | PatchMatch depth/normal maps → fusion → optional meshing | N/A (ground-truth reference, not a reconstruction) |
| Outputs | Carved mesh + declared-units manifest + provenance | Sparse point cloud + visibilities; poses unchanged | Dense point cloud / mesh; inherits mm units | Nominal geometry for comparison |
| Texture needed? | **No** (silhouettes only) | **Yes** — textureless views yield ~zero features | **Yes** — photometric consistency | N/A |
| Failure mode | Over-carve on bad masks; convex-hull-ish bias on concavities | No features / bad matches → empty or noisy cloud; poses stay fixed (safe) | GPU memory / wrong depth range → crash or holes | N/A |
| Evidence | sha-pinned bytes, canonical hashes, PENDING gates | External sidecar manifest required (COLMAP stores no hashes) | Same as sparse | Design authority, not evidence |

Consequence for fixtures: the synthetic box scenes render flat
two-tone images (bg 32 / fg 220, zero texture), so a SIFT-based sparse
run on them is predicted to produce ~no keypoints. Any future
end-to-end attempt needs textured rendering (or imported synthetic
keypoints) — the current `render_fixtures` output exercises only the
visual-hull path, not a feature pipeline.

## 5. Attempt log / gates (no run performed)

Per the task contract, a bounded run was allowed only with a backend
already usable with zero toolchain compilation. Gate check:

1. `colmap` binary: **absent** (not on PATH; no `COLMAP.bat` installed).
   Upstream prebuilt Windows binaries exist (unsigned, Defender warning
   per install docs) but fetching + executing them is an install action,
   out of scope. → **backend BLOCKED (sparse CLI + dense CLI)**.
2. `pycolmap` wheel: not installed in the mesh venv; PyPI has a
   matching `cp312-win_amd64` wheel for 4.2.1, but installing it into
   the venv is out of scope (and would break `gate-freeze.txt` /
   require SPDX + `THIRD_PARTY_NOTICES.md` updates per AGENTS.md). →
   **backend BLOCKED (pycolmap sparse API)**.
3. Dense/MVS: additionally requires a CUDA-enabled COLMAP build
   (pycolmap README: `patch_match_stereo` "requires compilation with
   CUDA", GPU MVS "not available with the PyPI wheels"; tutorial shows
   no CPU MVS path). This box has CUDA toolkit 12.8 + a sm_61 Quadro,
   so a build is plausible, but compiling graphics stacks is explicitly
   forbidden. Whether upstream prebuilt Windows binaries ship CUDA
   MVS was **not verified** — do not assume. →
   **backend BLOCKED (dense), gate = CUDA build**.
4. Predicted data gate (for any future attempt): textureless synthetic
   fixtures (§4) will starve SIFT; budget textured rendering first.

Time-box spend: ~0 min of 30 (stopped at gate 1, as instructed).
No bytes written outside this report; no pip installs; no uploads.

## 6. Follow-ups (not done, out of scope)

- Install a backend on a scratch machine/venv (never the mesh venv
  without a freeze update): `COLMAP.bat` upstream *or*
  `pip install pycolmap==4.2.1` (CPU sparse only), then run the §3.5
  recipe on textured fixtures and compare reprojection residuals
  against the ANASTYLOS projector as the equivalence check.
- Confirm exact mapper BA CLI flag spellings via `colmap mapper -h`.
- Confirm whether upstream Windows binaries include CUDA MVS; else
  budget a vcpkg CUDA build (`colmap[cuda]:x64-windows`) pinned to
  sm_61 for the Quadro P2000.
- Any dependency addition must update the SPDX inventory and
  `THIRD_PARTY_NOTICES.md` (checker must stay at 0 errors) and must
  not weaken `gate-freeze.txt` silently.
