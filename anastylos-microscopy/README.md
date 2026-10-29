# ANASTYLOS Microscopy Core v0.1.2

> v0.1.2 integrates the Windows-verified v0.1.1 reliability patch and tightens
> resistor uncertainty when one decoded band is materially channel-clipped.
> The reader evidence and candidate remain preserved; only the Core verdict may
> become `ambiguous`. See CHANGELOG.md and the QA report in the working tree at
> `qa-output/run-v012/QA_REPORT.md`.

An offline, file-based orchestration layer around the **existing** ANASTYLOS
recognition readers. One inspection contract, one CLI, evidence-preserving
records, deterministic replay. The readers are imported and called unchanged;
none of their algorithms or thresholds were rewritten or tuned.

> **ANASTYLOS_ROOT must point at a checkout of Anastylos revision
> `a172fbb9e127e7a3a93b128516b6592bfbbc81ef`.** The Core ships no reader code.
> Every record stores the code hash of each reader it called and whether it
> matches the hash pinned for that revision (`matches_pinned_revision`). A
> different checkout still runs but is flagged in every record.

What it is **not**: not a camera driver, not a Raspberry Pi service, not
BlobCam, not a parts database. It never writes Assembly Memory, never controls
hardware, never downloads models and never calls a network service.

## Wrapped readers (10)

| key | Anastylos module | needs |
|---|---|---|
| `resistor` | resistor-reader | image |
| `smd` | smd-reader | OCR (Tesseract) |
| `pcb_ocr` | pcb-ocr | OCR (Tesseract) |
| `markings` | component-markings | OCR (Tesseract) |
| `polarity` | polarity-reader | `--polarity-family` (or hypothesis) |
| `screw` | screw-reader | image |
| `screw_measure` | screw-measurement | `--view top|side` |
| `washer` | washer-spacer | `--view`, `--washer-family` |
| `fingerprints` | component-fingerprints | `--gallery` (match) or `--gallery --enroll-id` |
| `connectors` | connector-matching | optional `--gallery` |

`tray-counter` and `missing-parts` are deliberately **not** in the microscope
registry.

## Install (Windows PC, PowerShell)

Python 3.12, a virtualenv and the pins Anastylos already uses. The Core adds
**no** new dependency beyond OpenCV and NumPy.

```powershell
git clone https://github.com/valentin-englert/Anastylos C:\src\Anastylos
git -C C:\src\Anastylos checkout a172fbb9e127e7a3a93b128516b6592bfbbc81ef
py -3.12 -m venv C:\src\mic-venv
C:\src\mic-venv\Scripts\python -m pip install -r C:\src\Anastylos\calibration\requirements.txt
# optional, for smd / pcb_ocr / markings: install Tesseract and put tesseract.exe on PATH
# (or pass --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe")

Expand-Archive ANASTYLOS_MICROSCOPY_CORE_v0.1.2.zip C:\src   # new install; to upgrade see below
$env:ANASTYLOS_ROOT = "C:\src\Anastylos"
$env:PYTHONPATH     = "C:\src\anastylos-microscopy\src"
cd C:\src\anastylos-microscopy
C:\src\mic-venv\Scripts\python -m anastylos_microscopy readers
```

Linux/macOS: the same with `export ANASTYLOS_ROOT=...` and
`export PYTHONPATH=$PWD/src`.

## Run

```powershell
$py = "C:\src\mic-venv\Scripts\python"
# one image, one reader
& $py -m anastylos_microscopy inspect --image C:\captures\r1.jpg --mode resistor --out C:\mic-out
# several readers, ROI and calibration
& $py -m anastylos_microscopy inspect --image C:\captures\ic.jpg --mode multi --readers pcb_ocr,markings,polarity `
      --polarity-family ic --roi 400,300,800,500 --calibration C:\cal\micro-05x.json --out C:\mic-out
# automatic reader selection from an operator hypothesis
& $py -m anastylos_microscopy inspect --image C:\captures\s.jpg --mode auto --hypothesis smd_resistor --out C:\mic-out
# a folder, or a capture-node manifest (hash-checked frames)
& $py -m anastylos_microscopy inspect --batch-dir C:\captures\session1 --mode auto --out C:\mic-out
& $py -m anastylos_microscopy inspect --manifest D:\node\session1\capture-manifest.json --mode resistor --out C:\mic-out
# replay, validate, review, evaluate
& $py -m anastylos_microscopy replay   --inspection C:\mic-out\mi-...\inspection.json --out C:\mic-replay
& $py -m anastylos_microscopy validate --kind inspection C:\mic-out\mi-...\inspection.json
& $py -m anastylos_microscopy review   --inspection C:\mic-out\mi-...\inspection.json --operator Valentin `
      --decision confirmed --identification "1 kOhm 5% axial" --evidence "multimeter 0.998 kOhm"
& $py -m anastylos_microscopy eval --dataset examples\synthetic_mic\dataset.json --split dev --out C:\mic-eval
```

Modes: `resistor smd pcb_ocr markings polarity screw screw_measure washer
fingerprints connectors multi auto`. Hypotheses for `auto`: `axial_resistor
smd_resistor ic diode capacitor pcb_region screw washer connector unknown`.
Exit codes (same rule for one image and for batches; introduced in v0.1.1):

| code | meaning |
|---|---|
| 0 | every image completed - abstentions (`unknown`, `ambiguous`, `conflict`) are completed work |
| 1 | input/validation error, empty batch, or no usable record at all (all `input_error` / `reader_error` / `crashed` / `manifest_rejected`) |
| 2 | usage error |
| 3 | environment: `ANASTYLOS_ROOT` missing/invalid (checked before any image; nothing written) or every entry `unavailable` (e.g. Tesseract missing for an OCR-only mode) |
| 4 | partial: at least one usable record and at least one problem entry - read `batch.json` `outcome_counts` |

`batch.json` (`schemas/microscopy-batch.v1.schema.json`) carries `outcome`,
`exit_code`, `outcome_counts`, per-reader status counts and OCR availability.
An OCR reader reported `unavailable` never counts as an OCR result.

## Output per inspection

```
<out>/<inspection_id>/
  inspection.json          anastylos-microscopy-inspection/1 (schemas/)
  report.md                human summary
  evidence/<sha256>.<ext>  byte-identical copy of the original image
  roi_crop.png             only when --roi is given
  readers/<key>/result.json, annotated.png
```

Each record separates **observed_evidence** (raw OCR text, bands, contours),
**interpretations** (decoded values), **candidates** (always
`confirmed: false`) and **uncertainty**. Human confirmation lives only in
separate `reviews/review-NNN.json` files. Overall status is one of
`candidate ambiguous conflict unknown unavailable error recorded input_error`.

Since v0.1.1 a native resistor `ok` is checked by the Core evidence assessment
(`recognizers[].core_assessment`: band count/boxes, band-width consistency,
colour majority, colour stability along each band and channel clipping). In
v0.1.2, channel clipping at or above 10% in any one decoded band is enough to
hold the interpretation as `ambiguous`: a clipped band can change the decoded
value. This is a conservative uncertainty gate, not an accuracy estimate.
`native_status`, the verbatim `native_result` and the unconfirmed candidate
stay in the record; operator confirmation remains separate.
Capture-manifest frames must resolve (after symlinks / junctions) inside the
manifest's directory and are read exactly once (QA_FIX_REPORT.md, QA-02).

## Tests

```powershell
& $py -m unittest discover -s tests -v                 # Core tests (83: 36 v0.1.0 + 47 v0.1.1/v0.1.2)
& $py tools\run_reader_regression.py                   # unchanged reader suites (543 tests)
& $py tools\smoke_cli.py                               # CLI exit codes, junction escape, eval
& $py tools\qa03_resistor_sweep.py --grid heldout      # QA-03 false-acceptance/-rejection sweep
```

## Upgrading an existing directory safely

The package is self-contained; nothing outside its folder is written by an
upgrade. Keep the existing installation intact until v0.1.2 passes its tests.

```powershell
# 1. verify the archive
(Get-FileHash ANASTYLOS_MICROSCOPY_CORE_v0.1.2.zip -Algorithm SHA256).Hash
# 2. extract next to (not over) the old version
Expand-Archive ANASTYLOS_MICROSCOPY_CORE_v0.1.2.zip C:\src\mic-0.1.2
# 3. test the new copy against the SAME Anastylos checkout (a172fbb9)
$env:ANASTYLOS_ROOT = "C:\src\Anastylos"
$env:PYTHONPATH     = "C:\src\mic-0.1.2\anastylos-microscopy\src"
cd C:\src\mic-0.1.2\anastylos-microscopy
& $py -m unittest discover -s tests -v ; & $py tools\run_reader_regression.py ; & $py tools\smoke_cli.py
# 4. switch: rename, never delete, the old folder
Rename-Item C:\src\anastylos-microscopy anastylos-microscopy-0.1.0
Move-Item C:\src\mic-0.1.2\anastylos-microscopy C:\src\anastylos-microscopy
```

Existing output folders (`inspection.json`, reviews) stay valid and are
not rewritten; replaying them under a newer Core reports `identical: false` because
the Core version and record fields changed. Scripts that call the CLI must
handle exit code 4. Roll back by renaming the folders back.

## Independent Windows QA

The v0.1.1 payload was verified and exercised on Windows before promotion.
v0.1.2 core, reader-regression, CLI, batch, manifest-confinement and synthetic
resistor checks were then rerun in the main worktree. The report and exact
commands are stored at `qa-output/run-v012/QA_REPORT.md` in that worktree.
The report is a repository QA artifact and is not included in the standalone
ZIP. No real microscope or GTX 970 image was used.

See TEST_RESULTS.md (what ran, where), LIMITATIONS.md, INTEGRATION.md,
HARDWARE_COMPATIBILITY.md, DEPENDENCY_NOTICES.md and `documentation/`.

Licensing: this package declares **no** project license (Anastylos licensing is
deliberately deferred); see DEPENDENCY_NOTICES.md.
