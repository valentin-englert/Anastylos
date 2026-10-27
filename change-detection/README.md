# ANASTYLOS change detection

Compare a before photo and an after photo of the same sheet. The pipeline reads marker positions only from `calibration/calibration-config.json`:

1. Detect `DICT_5X5_100` markers and pick the view (`fixture_front`, `fixture_back`, `surface_a3`, `surface_a4`).
2. Homography into that frame, then warp both images to the same millimetre grid.
3. Mask the ROI from the config (controller window, or work area plus trays). Marker squares are excluded.
4. Divide by a local mean, blur, absolute difference, threshold, close, open.
5. Write each region as a bounding box in pixels and millimetres.

`status` counts regions once registration succeeds: `no_change`, `single_change`, or `multiple_changes` (regions are never merged into one event). Registration verdicts are separate: `registration_invalid` (no view confirmed — including a `--view`/manifest mismatch, views from different view groups, too few markers, a failed homography, or an empty ROI), `view_mismatch` (before/after views differ but stay within one view group, e.g. `surface_a3` → `surface_a4`), and `input_error` (an unreadable image or a missing before/after file). Only the three region-count statuses carry `rectified` coordinates; the other three never do.

Use the calibration virtualenv. No extra packages.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r calibration/requirements.txt
```

## Real pairs

Name files `<id>_before.jpg` and `<id>_after.jpg` (also png, tif, bmp). Optional `manifest.csv`:

```csv
pair_id,expected,expected_bbox_mm,view,assert,notes
ctrl_01,none,,fixture_back,1,no contact
screw_01,single,"52.0,40.5,6,6",fixture_back,1,one screw
```

`expected` accepts short manifest aliases — `none`, `single`, `multiple`, `invalid`, `view_mismatch` — plus the full `registration_invalid` spelling. These are input aliases, not emitted statuses: `none`/`single`/`multiple` map to the region-count statuses, and both `invalid` and `registration_invalid` map to `registration_invalid`. Several boxes are separated by `;`. `view` confirms the sheet; a mismatch aborts that pair as `registration_invalid`.

```bash
.venv/bin/python change-detection/detect.py run --input photos/monday --out results/monday
.venv/bin/python change-detection/detect.py run --input photos/monday --out results/monday --view fixture_back
.venv/bin/python change-detection/detect.py run --input photos/monday --out results/monday --require-config-sha256 a2308f66af38385a425e8839abd15c1e5253ffb9728c2d7ff57bb1fb15885812
```

Each pair folder gets `overlay.jpg`, `diff.png`, `mask.png`, `before_rectified.jpg`, `after_rectified.jpg`, `registration.jpg`, and `result.json`. The run also writes `summary.csv`, `summary.json`, and `run_info.json`.

`--require-config-sha256` aborts unless `harness-config.json` parses to the frozen parameter set. Three hashes are involved, computed two ways (see `anastylos_diff/config.py`): the calibration hash is SHA-256 over the raw bytes of `calibration/calibration-config.json`, pinned inside the harness config as `calibration_config.sha256` — a different kit file aborts before any work. The harness hash is `canonical_sha256` over the parsed harness JSON object — `json.dumps` with sorted keys, compact `(",", ":")` separators, no ASCII escaping, hashed as UTF-8 — so file whitespace and key order do not matter, but changing any threshold changes the hash. Every `result.json` embeds both values (`harness_config_sha256`, `kit_config_sha256`). (Run metadata also records a raw-bytes `harness_config_file_sha256`, but the frozen gate is the canonical hash.) `result_sha256` is something else: the per-file hash downstream consumers compute themselves — see [`assembly-memory/README.md`](../assembly-memory/README.md) and [`ARCHITECTURE.md`](../ARCHITECTURE.md). Treat a hash change as a new run. Do not retune on the scored set.

Current freeze (this tree):

- harness canonical sha256: `a2308f66af38385a425e8839abd15c1e5253ffb9728c2d7ff57bb1fb15885812`
- calibration file sha256: `a5f32cb54b57ed49096fa7c5d4a11be6ef2e2b932b6d3d248fcf4be59c17357e`

## Synthetic selftest

```bash
.venv/bin/python change-detection/detect.py selftest --seed 20261026
cd change-detection && ../.venv/bin/python -m unittest discover -s tests -v
```

`selftest` builds planar scenes from the kit config (markers, a flat placeholder controller, perspective, light, noise, JPEG). It is not a photograph of a real controller. The report lists every pair, including false alarms and misses. Expected labels are fixed when the images are built.

Reference synthetic run, seed `20261026`: **16/16** asserted cases, **0** false alarms on unchanged pairs, **8/8** expected boxes hit, max centroid error **0.46 mm**, max registration RMS **0.115 mm**. One unscored soft-shadow pair stayed `no_change`. That shadow result is a single synthetic example. Re-run the selftest locally; do not treat these numbers as measured on your machine.

## Limits

- The homography is exact only on the paper plane. Controller height is still unmeasured. Moving the camera between shots can create parallax that this synthetic set does not include. Keep the camera fixed.
- Hand shadows, glare, and focus jumps are not covered. `local_mean_division` flattens broad illumination and also weakens the interior of a very large removed part.
- A region is not a part name.
- `measured_print_scale` in the kit config is not applied. Print the sheets at 100% and check the 100 mm lines first.
