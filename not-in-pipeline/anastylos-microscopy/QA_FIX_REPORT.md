# QA fix report — Microscopy Core v0.1.1

Input: independent Codex QA of v0.1.0 on Windows (verdict *ACCEPT WITH FIXES*),
three confirmed findings. Baseline: `ANASTYLOS_MICROSCOPY_CORE_v0.1.0.zip`
SHA-256 `1664cbfc16fad93caa3f9d5764649cbafa2bb32b37926c3bd884b575801ab83b`
(left unchanged), Anastylos pin `a172fbb9e127e7a3a93b128516b6592bfbbc81ef`.
Development and all tests below: **Linux only** (Debian 13, CPython 3.12.15,
opencv-contrib-python 4.14.0.94, numpy 2.5.4, no Tesseract). Windows
verification is **pending** (see "Independent Codex QA on Windows" in README.md).

Process: (1) unmodified v0.1.0 unzipped and each defect reproduced with
`tools/qa_repro/repro_qa0N.sh <package-dir>`; (2) fixes implemented in a
separate copy; (3) the same scripts re-run on v0.1.1. Logs:
`documentation/qa-evidence/v0.1.0_qa0N.log` and `v0.1.1_qa0N.log`.

## QA-01 — batch exit status (P1)

**Reproduced on v0.1.0** (`repro_qa01.sh`): batch of 3 images with
`ANASTYLOS_ROOT` unset → `status_counts {"crashed": 3}`, **exit 0**; batch
valid + corrupt PNG → `{"input_error": 1, "candidate": 1}`, **exit 0**. Cause:
`cli.cmd_inspect` returned a constant `0` for every batch; `runner` never
aggregated outcomes; the root check ran per image inside a catch-all.

**Fix** (`runner.py`, `cli.py`, new `schemas/microscopy-batch.v1.schema.json`):
* Per-entry `processing` class: `completed`, `completed_with_reader_errors`,
  `unavailable`, `reader_error`, `input_error`, `manifest_rejected`,
  `crashed`. Abstentions (`unknown` / `ambiguous` / `conflict`) are
  `completed` — never crashes.
* `runner.exit_code_for()` → process exit status (single image uses the same
  rule): **0** all completed · **1** empty batch or nothing usable ·
  **3** `ANASTYLOS_ROOT` missing/invalid (fatal pre-flight before any image,
  nothing written) or every entry `unavailable` · **4** partial (≥1 usable
  record and ≥1 problem entry). 2 stays "usage".
* `batch.json` keeps every v0.1.0 field and adds `outcome`, `exit_code`,
  `outcome_counts`, `reader_status_counts`, `ocr.{readers,unavailable_entries}`
  and per-entry `processing`, `reader_statuses`, `core_downgraded_readers`.
  An unavailable OCR reader is reported as `unavailable`, never as an OCR result.
* Independent inputs still continue; per-image records are kept.

**Post-fix** (`v0.1.1_qa01.log`): unset root → **exit 3**, nothing written;
valid + corrupt → **exit 4**, `outcome: partial_failure`.

**Tests** `tests/test_qa01_batch_exit_status.py` (14, all child-process exit
codes): all succeed → 0; valid + corrupt → 4; all invalid → 1; missing root
(batch / manifest / single, unset and nonexistent) → 3 with no output; reader
exception in every image → 1; reader exception beside a working reader → 4;
unexpected orchestrator exception → counted `crashed` (4 mixed, 1 when all
crash); legitimate abstention (uniform + glare) → 0; OCR-only batch without
Tesseract → 3 with `ocr.readers.smd = {"unavailable": 2}`; OCR unavailable
beside polarity → surfaced, exit 0; empty batch → 1; single-image codes;
exit-code table.

**Residual risk:** exit 4 is new; scripts that treated any non-zero as
"environment broken" must read `batch.json`. Exit codes are documented in
`cli.py`, README and INTEGRATION.

## QA-02 — capture-manifest path escape (P1)

**Reproduced on v0.1.0** (`repro_qa02.sh`, POSIX emulation of the reported
junction: `capture/frames` → directory symlink to `outside/`): `validate
--kind capture_manifest` → `valid: true`, exit 0; `inspect --manifest` →
the outside file was hashed, decoded, copied into `evidence/` and reported as
`candidate`, exit 0. Cause: `capture.safe_relative` was purely lexical;
`validate` only schema-checked; inspection reopened the path several times.

**Fix** (`capture.py`, `runner.py`, `evidence.py`, `orchestrator.py`,
`cli.py`, `evaluation.py`):
* Stricter lexical rules: no `..`/`.`, absolute, drive (`C:`/`C:x`), UNC,
  `\\?\`, NUL/control chars, `:` (NTFS streams), reserved device names,
  trailing dot/space.
* Capture root = real path of the manifest's directory. `resolve_frame`
  follows symlinks/junctions with `os.path.realpath(strict=True)` and requires
  the result inside the root (`commonpath` on normalized, case-folded paths,
  extended-length prefixes stripped; different drive/share → outside).
  Windows reparse points other than symlink/junction are rejected; regular
  file and `st_nlink == 1` required (fail-closed).
* `read_frame` opens the resolved file once (`O_NOFOLLOW` where available),
  re-resolves the in-root path **after** opening and requires the same
  device+inode/NTFS file index, reads the bytes once; those exact bytes are
  hashed, decoded and preserved (`inspect_image(image_bytes=...)`,
  `evidence.intake(data=...)`, `preserve_original(data=...)`).
* `validate --kind capture_manifest` now runs the same checks plus per-frame
  SHA-256 (`capture.verify`). `replay` of a manifest record re-reads through
  the same confinement. Dataset images in `eval` use it too.

**Post-fix** (`v0.1.1_qa02.log`): validate → `outside_capture_root`, exit 1;
inspect → `manifest_rejected`, no record, no evidence copy, exit 1.

**Tests** `tests/test_qa02_capture_confinement.py` (20; 18 run, 2 skipped on
Linux): normal frame; nested path; `../`; absolute outside; missing frame;
symlink inside root (file and directory) allowed with link provenance;
file symlink outside rejected; **directory link outside (junction
emulation)** rejected with no unauthorized byte in any output; broken link
and link loop; hard link to outside; validated bytes == inspected bytes
even if the file is replaced afterwards; race emulations (directory swapped
before open; swapped and restored around open; final component swapped to a
symlink) all rejected; validate/inspect agreement; confined replay; lexical
table. **SKIPPED on Linux:** `test_windows_junction_outside_root_rejected`,
`test_windows_junction_inside_root_allowed` (real `mklink /J`) —
**Windows junction verification pending.** `tools/smoke_cli.py` also builds a
real junction on Windows.

**Residual risk** (LIMITATIONS.md §3): no claim of full TOCTOU immunity.
A process that can write into the capture root while the Core reads can
still swap a frame for another *in-root* file; escapes outside the root are
detected after opening, and nothing is decoded before that check. Filesystems
that report no file ID (`st_ino == 0`) fall back to path comparison
(`identity_check: path_only`, recorded per frame). Cloud-placeholder reparse points (e.g. OneDrive
"Files On-Demand") are rejected — copy captures to a local folder.

## QA-03 — false-confident resistor read (High)

**Reproduced on v0.1.0** (`repro_qa03.sh`): `unknown_glare.png` (rendered
2.2 kΩ = red-red-red-gold, `glare=True, brightness=1.9`) → native `ok`,
22.9 kΩ, score 0.926, bands red-red-**white**-red-gold, Core
`glare_fraction 0.10007`, no flags, overall `candidate`.

**Investigation** (intermediate reader evidence, not assumed):
* The reader found **5** runs for **4** printed bands. Band 2 (`white`, 0.997)
  is a **4 px** run between bands of 27–31 px — the desaturated edge of the
  specular highlight (profile HSV ≈ (18, 40, 255)), not a printed band.
* **Exposure clipping**: every band's profile value is V = 255; 34.7 % of all
  pixels have a channel ≥ 250. The Core metric (gray ≥ 250) saw only 10 %
  because saturated *coloured* pixels (R = 255, G/B low) have low gray; the
  reader's own glare mask (S < 25 ∧ V > 245) saw 0.1 %. Value information
  (brown vs red vs orange) is destroyed.
* The reader's score is the geometric mean of per-band colour scores; it has
  no notion of band geometry, so a confidently-coloured sliver scores high.
  The failure is **segmentation + clipping**, not colour-classifier
  instability on real bands (bands 0, 1, 3, 4 are classified correctly).

**Fix** (new `assessment.py`, `orchestrator.py`, `evidence.py`, `report.py`;
resistor reader **unchanged**): a Core evidence assessment of every native
resistor `ok`, using only the reader's own reported evidence and the source
pixels inside its band boxes. Checks fixed a priori: `evidence_complete`,
`band_geometry` (band < 40 % of median width), `band_color_determinacy`
(decoded colour must be the band's majority top colour), `band_color_consistency`
(decoded colour top in ≥ 2 of 3 segments along the band, via the reader's
public `classify`), `band_color_fidelity` (≥ half the bands ≥ 50 % clipped).
Any failure → block `status` `ambiguous` (native `ok` kept in
`native_status` and `native_result`), `core_assessment` with all measurements
and reasons, candidates kept and marked `not_supported_by_band_evidence`,
review stays pending. Assessment errors fail closed. New non-blocking quality
indicator `channel_clipped_fraction` / flag `channel_clipping`.

**Post-fix** (`v0.1.1_qa03.log`): native still `ok` 22.9 kΩ; Core status
`ambiguous` (failed `band_geometry`, `band_color_fidelity`); MIC-05 glare
scores `correct_abstention`.

**Tests** `tests/test_qa03_resistor_assessment.py` (12): glare fixture
(native evidence kept, raw `result.json` not rewritten, report shows the hold);
8 known-good renders incl. 5/6-band, rotated, blue body, noise stay
`candidate`; low-quality-but-interpretable (dim + gradient + noise; mild glare
at normal exposure) stay `candidate`; native `ambiguous` untouched; blurred,
uniform and obscured → no candidate; ambiguous colour evidence → downgraded;
invalid ROIs → `input_error`; ROI on background → no candidate; 6 kinds of
incomplete reader evidence → downgraded; assessment crash → fail closed;
anti-overfitting guard (no fixture name, hash, 2.2/22.9 kΩ or glare-fraction
constants in the assessment).

**False acceptance / false rejection** (`tools/qa03_resistor_sweep.py`,
SYNTHETIC reader renders; only native `ok` counted; thresholds frozen before
the held-out grid was run — `documentation/qa03-sweep-summary.json`):

| grid | renders | native ok | native ok but wrong (v0.1.0 false-confident) | false acceptance after v0.1.1 | false rejection (correct ok held) |
|---|---|---|---|---|---|
| dev (design) | 1200 | 933 | 369 | **58** (15.7 % of wrong) | **226** (40.1 % of correct) |
| held-out | 512 | 196 | 134 | **43** (32.1 % of wrong) | **38** (61.3 % of correct) |

Precision of what remains `candidate`: dev 85.4 % (v0.1.0: 60.5 %);
held-out 35.8 % (v0.1.0: 31.6 %). False rejections concentrate on
over-exposed renders (channel clipping), which is intended conservatism.
Residual false acceptances are *internally consistent* misreads (e.g. a dark
band uniformly blended into a blue body under a diffuse highlight, or
mis-classified bands on rotated bodies) that no evidence-consistency check can
see without ground truth.

**Residual risk:** this is a safeguard against one failure *class*, not a fix
of resistor recognition. The sweep also shows that the unchanged reader's `ok`
is wrong far more often under synthetic stress than v0.1.0 documentation
implied. **Real-image validation is pending**; thresholds are uncalibrated.

## Scope / integrity

No reader code modified (all 10 code hashes match the a172fbb9 pin); no new
runtime dependency; no Git operation on any repository; no BlobCam code; no
Assembly Memory access. Lint-only tools (pyflakes 3.2.0, jsonschema 4.23.0)
ran from a throw-away venv outside the package.
