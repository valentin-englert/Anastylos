# ANASTYLOS Microscopy Core v0.1.2 — Integration and Reliability QA

**Run date:** 2026-10-29  
**Recommendation:** **ACCEPT WITH LIMITATIONS** for the integrated software package and continued controlled evaluation. Resistor values are not qualified for automatic identification. No hardware validation is claimed.

## Executive result

The Windows-verified v0.1.1 payload is integrated into the main worktree as v0.1.2. All 76 files listed in the staged v0.1.1 delivery manifest were present and hash-correct before intentional v0.1.2 edits. The v0.1.0 installation and its prior QA history were preserved separately: all 283 recorded file hashes match, including 204 prior QA files; the historical fixture junction still maps to the same relative target. The prior v0.1.1 extraction and its Windows QA report also remain in place.

The final Core suite passed 76 tests and skipped 7 platform/capability cases; reader regression passed 543/543; CLI smoke passed 20/20. Actual Windows NTFS junction acceptance/rejection, validation/inspection agreement for the outside-root case, and a junction replacement identity check passed. A separate hash-mismatch helper reported one failed assertion because it expected a `manifest_rejected` entry; inspection instead emits a schema-valid `input_error` record, reports the SHA mismatch, and runs no readers. This is disclosed and adjudicated below.

The v0.1.2 resistor gate improved false acceptance on the frozen synthetic held-out grid from 43 to 23, while accepted correct candidates fell from 24 to 15. Precision among retained candidates is still only 39.5% (15 correct, 23 wrong). This is insufficient for automatic resistor identification.

## Locations, preservation, and prior report link

- Integrated package: `C:\Users\HP\Documents\Anastylos\anastylos-microscopy\`.
- Original extracted v0.1.1 copy: `C:\Users\HP\Documents\Anastylos\ANASTYLOS_MICROSCOPY_CORE_v0.1.1\anastylos-microscopy\` (76/76 payload hashes verified).
- v0.1.1 QA staging copy: `C:\Users\HP\Documents\Anastylos\.microscopy-v011-qa\anastylos-microscopy\` (76/76 payload hashes verified).
- Actual previous Windows report: `C:\Users\HP\Documents\Anastylos\.microscopy-v011-qa\qa-output\QA_REPORT.md`.
- Byte-verified v0.1.0 backup: `C:\Users\HP\Documents\Anastylos\.microscopy-v011-qa\preserved-v0.1.0-exact\`.
- Stable v0.1.2 report/evidence: `anastylos-microscopy/qa-output/run-v012/`.

The old Windows report exists in the staging QA output; it is not missing. There is no `QA_REPORT.md` at the package root. The prior broken link therefore points to the wrong location or uses the wrong relative-path base. The exact earlier link target was not available for distinguishing a malformed path from a relative working-directory mismatch. The new report uses a stable package-relative location.

The preserved v0.1.0 manifest covers 283 regular files; all SHA-256 values verified. Its 204 historical QA files are byte-identical in the active package's retained `qa-output/`. The prior junction at `qa-output/run-20261029-main/fixtures/capture-junction/frames` is still a junction to `qa-output/run-20261029-main/fixtures/outside-frames`; the exact backup preserves the same relative link structure. See `integration-preservation.json`.

The v0.1.1 staging delivery manifest lists 76 payload files, all correct in both v0.1.1 copies. The original v0.1.1 ZIP was unavailable, so its ZIP-level hash remains **NOT VERIFIED**. No claim is made that the prior expected ZIP hash was checked. The current v0.1.2 ZIP was independently opened and checked against its embedded manifest: 76 payload files plus the manifest, no mismatches, and no `qa-output/` content.

## Environment and revision

| Item | Observed |
|---|---|
| OS | Windows 11, Windows build 10.0.26100, AMD64 |
| Python | Existing repository `.venv`, CPython 3.12.10, 64-bit |
| OpenCV | 4.14.0 |
| NumPy | 2.5.4 |
| Pillow | 13.0.0 |
| Tesseract | Not installed / not found on PATH |
| `ANASTYLOS_ROOT` | `C:\Users\HP\Documents\Anastylos` |
| `PYTHONPATH` | `C:\Users\HP\Documents\Anastylos\anastylos-microscopy\src` |
| Branch / HEAD | `main` / `f98badb38515cbc415a8da466189218fc84d8794` |
| Required reader pin | `a172fbb9e127e7a3a93b128516b6592bfbbc81ef` |

HEAD differs from the reader pin. The branch was not changed. All ten reader package code hashes match the v0.1.1 pinned hashes and all reader regression tests passed against this checkout. This proves hash compatibility for the reader code, not execution at the exact pinned commit. No original ANASTYLOS reader files were modified. No BlobCam code was copied, imported, run, or deployed. No dependency was installed.

## Commands and execution

Commands were run in PowerShell from the package directory using the existing venv. The isolated temp directory is outside `ANASTYLOS_ROOT`; this is required by the isolation test. The junction smoke and deep fixtures were run in a process permitted to create junctions. No symlink privilege or Windows security setting was changed.

```powershell
$root = 'C:\Users\HP\Documents\Anastylos'
$pkg = Join-Path $root 'anastylos-microscopy'
$py = Join-Path $root '.venv\Scripts\python.exe'
$env:ANASTYLOS_ROOT = $root
$env:PYTHONPATH = Join-Path $pkg 'src'
$env:TEMP = 'C:\Users\HP\.codex\visualizations\2026\10\29\01a18905-9ad2-7f73-9318-d970f67c6dea\microscopy-v012-temp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
Set-Location $pkg
& $py -m unittest discover -s tests -v
& $py tools\run_reader_regression.py
& $py tools\smoke_cli.py
& $py -m anastylos_microscopy readers
& $py tools\qa03_resistor_sweep.py --grid dev --json qa-output\run-v012\qa03-dev.json
& $py tools\qa03_resistor_sweep.py --grid heldout --json qa-output\run-v012\qa03-heldout.json
& $py -m anastylos_microscopy inspect --image examples\synthetic_mic\capture_node_example\frames\frame_000001_20261029T120001000Z.jpg --mode multi --readers resistor,screw --clock 2026-10-29T12:00:00Z --out qa-output\run-v012\multi-out
& $py -m anastylos_microscopy validate --kind inspection qa-output\run-v012\multi-out\mi-f5402007204340f7bc26\inspection.json
& $py -m anastylos_microscopy replay --inspection qa-output\run-v012\multi-out\mi-f5402007204340f7bc26\inspection.json --out qa-output\run-v012\multi-replay
& $py -m anastylos_microscopy eval --dataset examples\synthetic_mic\dataset.json --split dev --out qa-output\run-v012\mic-eval-dev
```

The Windows-specific extended capture harness was run as:

```powershell
& $py qa-output\run-v012\windows-junction-deep.py
```

That harness is a QA-only copy of the prior Windows junction probe pointed at a fresh `qa-output/run-v012/qa02-deep/` directory. It refuses to overwrite existing fixtures. The actual invocation/output is captured in `qa-output/run-v012/qa02-deep/windows-junction-qa.json`. Core and smoke logs are also retained in this report directory.

The final full-suite command used the isolated environment above. An earlier attempt with temp files under the repository triggered the intended isolation guard; an earlier non-elevated smoke run skipped junction creation. Those setup attempts are retained as `core-tests*.log` and `cli-smoke*.log`; they were superseded by the final isolated run and elevated junction run, not counted as final passes.

## Test results

| Suite | Result |
|---|---:|
| Microscopy Core | 83 total: 76 passed, 7 skipped, 0 failures, 0 errors |
| Existing reader regression | 543 passed, 0 skipped, across 10 readers |
| CLI smoke | 20/20 exit-code steps passed |
| Manual multi-reader flow | Resistor `ok`, screw `unknown`, overall `candidate`, exit 0 |
| Inspection schema | Valid |
| Replay | Same inspection ID/digest; identical sections |
| Synthetic MIC dev harness | Completed; synthetic-only, no hardware claim |
| QA-03 development sweep | 1,200 synthetic renders |
| QA-03 frozen held-out sweep | 512 synthetic renders |

Core skips, individually:

| Test | Status/reason |
|---|---|
| `test_broken_link_rejected` | SKIP — Windows denied symlink creation (`WinError 1314`) |
| `test_directory_link_outside_root_rejected` | SKIP — same symlink permission limit |
| `test_file_symlink_outside_root_rejected` | SKIP — same symlink permission limit |
| `test_symlink_inside_root_allowed` | SKIP — same symlink permission limit |
| `test_final_component_swapped_to_symlink_rejected` | SKIP — `O_NOFOLLOW` unavailable on Windows |
| `test_swap_and_restore_around_open_rejected` | SKIP — POSIX rename/symlink race emulation |
| `test_swap_between_check_and_open_rejected` | SKIP — POSIX rename/symlink race emulation |

The Windows junction tests `test_windows_junction_inside_root_allowed` and `test_windows_junction_outside_root_rejected` ran and passed. Symlink cases remain **PENDING**, not PASS. No settings were changed to grant symlink support.

Reader regression counts: resistor-reader 30; smd-reader 31; pcb-ocr 31; component-markings 54; polarity-reader 60; screw-reader 45; screw-measurement 81; washer-spacer 82; component-fingerprints 61; connector-matching 68. Total 543, all passed.

The `readers` registry listed ten supported readers. OCR capability was unavailable because Tesseract is absent. An OCR-only batch returned exit 3 with two `unavailable` entries and zero completed entries; this is expected environment handling, not an OCR quality test.

The manual multi-reader inspection validated with no schema errors. Replay produced inspection `mi-f5402007204340f7bc26` with the same SHA-256 digest `0e3db8abf7e56f4d5715d4c25ddf900ce309046768345dc41ff7feb13458d949` and no differing sections. The synthetic MIC dev harness explicitly reported `validation_class: synthetic` and `hardware_validation: not_claimed`.

## QA-01 — batch exit-code matrix

All requested process outcomes passed; batch files were schema-checked and counters inspected.

| Case | Exit | Batch result/counters |
|---|---:|---|
| All entries complete | 0 | `success`; 2 completed, 1 candidate, 1 abstention, 1 Core downgrade, 0 problems/crashes |
| Mixed valid + corrupt | 4 | `partial_failure`; 1 completed/usable, 1 `input_error`, 1 problem, 0 crashes |
| All invalid | 1 | `failure`; smoke batch counted 2 input errors; unit coverage also asserts 3/3 invalid inputs |
| Missing `ANASTYLOS_ROOT` | 3 | Fatal preflight; tests assert nothing is written |
| Empty batch | 1 | Empty failure |
| OCR-only, no Tesseract | 3 | `environment_unavailable`; 2 unavailable, 0 completed |
| Legitimate abstention | 0 | Completed work; abstention is not a processing error |

This confirms the v0.1.1 QA-01 behavior remains in the integrated package. The successful batch has `crashed: 0`; the mixed case reports its partial failure rather than returning success.

## QA-02 — Windows filesystem confinement

The extended Windows probe against the promoted v0.1.2 package exercised normal frames, in-root and outside-root NTFS junctions, missing/broken targets, unsafe path strings, file replacement, file identity, and hash mismatch. Thirteen checks passed; one helper assertion failed on the hash-mismatch classification and is described below.

| Scenario | Observed result |
|---|---|
| Normal in-root frame | Validation and inspection accept; preserved bytes/hash match input |
| Junction to directory inside capture root | Validation and inspection accept; provenance says `kind: junction`; bytes/hash match |
| Junction outside capture root | Validation and inspection both exit 1 with `outside_capture_root`; no inspection record; outside frame bytes/hash absent from output |
| Junction replacement during open | Rejected as `changed_during_read`; opened and restored targets had distinct nonzero NTFS file IDs; no inspection evidence was emitted |
| Missing target | Validation and inspection reject as `frame_missing` |
| Broken junction target | Validation and inspection reject as `unresolvable_link` |
| Traversal, absolute/UNC/drive paths, ADS, reserved names, trailing dot/space | Rejected by path validation |
| Normal file identity | Nonzero `st_dev` and `st_ino`; `identity_check: device+inode`; `st_nlink: 1` |
| Junction metadata | Python reports `os.path.isjunction=True`, `os.path.islink=False`; reparse tag `0xA0000003` (`IO_REPARSE_TAG_MOUNT_POINT`) and reparse attributes present |
| Hash mismatch | Validation rejects; inspection exits 1, runs no recognizers, produces a schema-valid `input_error` record with `manifest_sha256_mismatch` and actual-byte SHA |
| Symlink escape | PENDING — creation denied with `WinError 1314` |

For the direct outside-junction attempt, no unauthorized frame bytes or SHA were copied to evidence output. The separate replacement probe was rejected by the post-open identity check and emitted no record/evidence. `st_dev`/`st_ino` were available and nonzero on this NTFS volume. The code may read bytes into a process buffer before the post-open identity comparison during the race window; this QA verifies that the race is rejected and the bytes are not returned or persisted as evidence.

**Hash mismatch helper assertion:** `windows-junction-qa.json` reports `FAIL` for the helper check that expected the batch entry's `rejection` field to equal `hash_mismatch`. The product instead reports `processing: input_error`, `overall_status: input_error`, and error `manifest_sha256_mismatch`; the batch exits 1, the inspection schema validates, and zero recognizers run. The inspection record preserves the actual in-root input bytes under their computed SHA for provenance. The validation CLI also rejects the declared hash. This is a classification/contract difference, not acceptance of the mismatched image. `qa02-hash-mismatch-adjudication.json` records the independent checks. A future QA/API follow-up should settle whether the inspection entry should be represented as `manifest_rejected` or as a schema-valid `input_error`; do not treat the helper's failed literal assertion as a passed assertion.

**Severity and root-cause hypothesis:** the hash-mismatch behavior is low-severity API consistency (`P2`): validation rejects at manifest validation, while inspection catches the same SHA mismatch in its per-entry intake path so it can retain a schema-valid error record. Both return nonzero and no recognizer runs. The failed helper assertion tested one error label rather than the fail-closed behavior.

## QA-03 — resistor uncertainty and synthetic evidence

The native resistor reader remains unchanged. v0.1.2 assessor policy `anastylos-microscopy-resistor-evidence/2` downgrades an otherwise-`ok` interpretation when any decoded band has at least 10% of its pixels with a channel value at or above 250. The native result and candidate remain preserved, and the Core verdict is `ambiguous`. This threshold is a conservative evidence heuristic, not an empirically calibrated probability.

The threshold was chosen using the 1,200-render development grid. `policy-freeze.json` records the source hashes and development-result hash before the 512-render held-out run; the implementation was not tuned after seeing held-out results.

| Grid/metric | v0.1.1 | v0.1.2 |
|---|---:|---:|
| Dev renders | 1,200 | 1,200 |
| Dev accepted correct | 338 | 294 |
| Dev accepted incorrect | 58 | 25 |
| Dev rejected correct | 226 | 270 |
| Dev rejected incorrect | 311 | 344 |
| Dev candidate precision | 85.4% | 92.2% |
| Dev candidate coverage of native `ok` | 42.4% | 34.2% |
| Held-out renders | 512 | 512 |
| Held-out accepted correct | 24 | 15 |
| Held-out accepted incorrect | 43 | 23 |
| Held-out rejected correct | 38 | 47 |
| Held-out rejected incorrect | 91 | 111 |
| Held-out candidate precision | 35.8% | 39.5% |
| Held-out candidate coverage of native `ok` | 34.2% | 19.4% |

For v0.1.2, the held-out native-`ok` pool contains 62 correct and 134 incorrect reads. The rule accepted 15 correct and 23 incorrect, rejected 47 correct and caught 111 incorrect. Its held-out false-acceptance rate among wrong native-`ok` readings fell from 43/134 (32.1%) to 23/134 (17.2%); it also rejects more correct reads. Candidate precision is 15/(15+23) = 39.5%, which means more retained candidates were wrong than right in this synthetic held-out set.

The known glare case still produces the original reader's incorrect 22.9 kOhm result for a synthetic 2.2 kOhm target. The Core reports `ambiguous`, preserving original reader evidence and not confirming the candidate. `qa03-glare-evidence.json` confirms the native `result.json` is unchanged, the original image hash matches its preserved copy, and the candidate remains `confirmed: false`.

Post-run held-out diagnostics found remaining false-confident color ambiguity: expected 82 kOhm gray-red-orange-gold rendered as 8.2 Ohm gray-red-gold-gold, with native scores around 0.90–0.91, underexposed brightness 0.85, and no band clipping. All current evidence checks pass because the wrong interpretation is internally consistent. This cannot be solved by the single-band clipping rule. It demonstrates that reader score is not calibrated reliability and that a more general color-interpretation policy needs a separately labeled development set and a new untouched evaluation set.

The policy reduces a known failure mode but does not establish accurate resistor identification. Do not use inferred resistor candidates as confirmed electrical specifications. Keep human confirmation separate.

**Severity and root-cause hypothesis:** residual false-confidence is high priority (`P1`) for any workflow that would use inferred resistance without review. The unchanged reader can assign a high heuristic score to a consistently wrong band sequence. The Core assessment checks evidence completeness, band geometry, local color majority/stability, and clipping; it does not yet compare plausible full color-code interpretations or calibrate the reader score against labeled outcomes. The v0.1.2 gate addresses visible channel clipping but cannot resolve the unclipped orange/gold confusion in the remaining examples. The held-out evidence supports that narrow clipping gate; it does not support a general resistor reliability claim.

## Compatibility and unexpected behavior

- v0.1.2 is schema-compatible with the current inspection contract; the new assessor metadata is already represented by `core_assessment` in the schema. Inspection validation and replay passed.
- Current HEAD is not the required pin, though all ten reader code hashes match. Run against exact pin only if release policy requires exact commit identity.
- Missing Tesseract is surfaced as unavailable; OCR-only exit 3 is correct.
- Hash mismatch is rejected but classified differently by validation and inspection, as detailed in QA-02.
- QA-03 assessor update changed the Core package version and assessor identifier; no source in the resistor-reader module or any other original reader changed.
- No synthetic result is hardware validation.

## Prioritized follow-ups

1. **P1 — Keep automatic resistor identification disabled for decisions.** The held-out synthetic candidate precision is 39.5%, with 23 incorrect versus 15 correct retained values. Keep these as hypotheses requiring operator confirmation.
2. **P1 — Collect ground-truthed real microscope images before broadening acceptance.** Measure/calibrate each optic/zoom/resolution, capture controlled lighting and glare cases, split development and held-out images before tuning, and freeze thresholds before the real held-out evaluation. Do not reuse this synthetic held-out set for tuning claims.
3. **P2 — Investigate color-code ambiguity and score calibration on a new development set.** Compare plausible color-band mappings/orderings and ROI/lighting quality while preserving native evidence; reject only interpretations unsupported by evidence rather than blanket-rejecting every resistor.
4. **P2 — Standardize hash-mismatch outcome semantics.** Decide whether inspection should emit a `manifest_rejected` item or a schema-valid `input_error` record, then align validation, tests, and documentation. The current path fails closed and runs no recognizer.
5. **P3 — Complete Windows symlink coverage only where the environment permits it**, and qualify OCR using an existing approved Tesseract installation. No account or security changes are required for this QA result.

Before real GTX 970 evaluation, MC-HW-01 is now PASS on the Windows workstation for Core/reader software regression with matching reader hashes. MC-HW-02 through MC-HW-06 remain not passed: Tesseract, real MUSTOOL/UGREEN capture, optics-specific calibration, real capture-manifest session, and operator-labeled real dev/held-out evaluation are still required. No physical device was accessed here.

## Release artifact and Git state

Standalone archive: `C:\Users\HP\Documents\Anastylos\ANASTYLOS_MICROSCOPY_CORE_v0.1.2.zip`  
SHA-256: `7a4810b09ae318337ae0b6b020d60116075f4565f941eb04de14455df88bd151`  
Delivery manifest SHA-256: `00afd57287df7749d133c91cc6cd1eb6c07dfcb7b24e2a8266011e900f20a3da`  
Archive verification: 77 ZIP entries (76 payload + manifest), 0 file hash mismatches; `qa-output/`, caches, VCS metadata, secrets, and unrelated repositories excluded. See `delivery-verification.json`.

Git status before this work:

```text
## main...origin/main [ahead 6]
?? .microscopy-v011-qa/
?? ANASTYLOS_MICROSCOPY_CORE_v0.1.1/
?? anastylos-microscopy/
?? calibration/print/anastylos_surface_a3_v2.pdf
?? snes-02/work/cam1.mono48k.wav
?? snes-02/work/cam2.mono48k.wav
?? snes-02/work/manifest.bak.json
```

Git status after this work:

```text
## main...origin/main [ahead 6]
?? .microscopy-v011-qa/
?? ANASTYLOS_MICROSCOPY_CORE_v0.1.1/
?? ANASTYLOS_MICROSCOPY_CORE_v0.1.2.zip
?? anastylos-microscopy/
?? calibration/print/anastylos_surface_a3_v2.pdf
?? snes-02/work/cam1.mono48k.wav
?? snes-02/work/cam2.mono48k.wav
?? snes-02/work/manifest.bak.json
```

No tracked or staged changes were present; the existing untracked entries were preserved. No commit, push, merge, rebase, reset, branch switch, reader modification, BlobCam change, or Assembly Memory change occurred.

## Modified and created files

Intentional package source/documentation changes relative to the verified v0.1.1 stage:

- `CHANGELOG.md`
- `DELIVERY_MANIFEST.json`
- `HARDWARE_COMPATIBILITY.md`
- `INTEGRATION.md`
- `LIMITATIONS.md`
- `README.md`
- `TEST_RESULTS.md`
- `documentation/inspection-schema.md`
- `src/anastylos_microscopy/__init__.py`
- `src/anastylos_microscopy/assessment.py`
- `tests/test_qa03_resistor_assessment.py`

QA report, fixtures, logs, JSON evidence, preservation checks, and hashes are all under `qa-output/run-v012/`; they are inventoried by `evidence-sha256.json` and excluded from the delivery ZIP. The package report file is `qa-output/run-v012/QA_REPORT.md`.

