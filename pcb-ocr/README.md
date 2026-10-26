# ANASTYLOS PCB silkscreen OCR

Extracts PCB reference designators (R12, C5, U3, …) from photographs as
evidence-backed, spatially localized candidates. Raw OCR text, normalized
readings, alternates, and verdicts are all preserved; a suggested
component type never proves anything about a visible part.

## Backend (read this first)

OCR needs an installed engine. The only backend shipped is `tesseract`,
which shells out to a locally installed Tesseract binary and its
bundled tessdata. Nothing is downloaded or installed at runtime. With
no engine installed, `read` returns `"status": "unavailable"` instead
of guessing. See `THIRD_PARTY.md` for dependency licenses.

```powershell
..\.venv\Scripts\python ocr.py read --image path\to\pcb.jpg --out ocr_out
..\.venv\Scripts\python ocr.py read --image path\to\pcb.jpg --roi 100,80,700,180 --out ocr_out
..\.venv\Scripts\python ocr.py read --image rectified.jpg --frame-meta frame.json --out ocr_out
..\.venv\Scripts\python ocr.py read --image path\to\pcb.jpg --tesseract-cmd 'C:\Program Files\Tesseract-OCR\tesseract.exe' --out ocr_out
```

The Windows installer does not add Tesseract to `PATH`; either add it
or pass `--tesseract-cmd` (also accepted by `eval`).

`--frame-meta` is optional JSON `{"view": ..., "px_per_mm": ...,
"origin_frame_mm": [x0, y0]}` (same contract as change-detection
`rectified` blocks). Bounding boxes are always reported in source
pixels; millimetres appear only when valid metadata is supplied.

## Pipeline

1. Crop (`--roi`) and deterministic preprocessing: 2.5× upscale,
   CLAHE, normal plus inverted polarity.
2. Four-orientation search (0/90/180/270) with exact box mapping back
   to source coordinates. Same-ink repeats across attempts arbitrate
   deterministically: clean parses beat corrected ones, then higher
   engine confidence, then upright; identical readings merge with
   occurrence counts and orientations seen.
3. Designator parsing: normalization plus O/0 and I/L/1 alternates
   (max two corrections). Any corrected reading is `ambiguous`, never
   `ok`. Distant duplicate labels are flagged, not merged.
4. Verdicts: `ok`, `ambiguous` (low confidence, corrections, or
   duplicates), `unknown` (no designator-like text), `unavailable`
   (no engine). Output is `result.json` plus `annotated.png`.

## Real-photo evaluation

Score labelled photos without claiming synthetic accuracy:

```powershell
..\.venv\Scripts\python ocr.py eval --labels labels.json --images-dir photos\ --out ocr_eval_out
```

Labels: `{"images": [{"file": "img.jpg", "labels": [{"text": "R12",
"bbox_xywh": [x, y, w, h]}]}]}` (boxes optional). The report gives
per-label hits, false positives, precision, and recall for that set
only.

## Test

```powershell
cd pcb-ocr
..\.venv\Scripts\python -m unittest discover -s tests -v
```

26 deterministic tests: parser cases (prefixes, alternates, false
positives) run on plain Python; pipeline and eval-harness cases use
programmable stub backends (rotation search, polarity, duplicates,
low confidence, missing backend, millimetre math, CLI, overlays).

## Limits

- Recognition on real photos is unverified: Tesseract 5.4.0 is
  installed and exercised on rendered text panels only (recall 0.7,
  precision 0.875 on that synthetic set, measured 2026-10-26). Run
  `eval` on labelled real photos before any accuracy claim.
- Engine misreads (e.g. C5→Co on thin fonts) surface as `ambiguous`
  or misses; the pipeline flags them but cannot correct the engine.
- Orientation search covers 90° steps only; skewed text needs a
  deskew stage that does not exist yet.
- Tesseract TSV word boxes are trusted as given; no box refinement.
- No Assembly Memory writes in this iteration.
