# ANASTYLOS SMD resistor marking reader

Reads printed markings on surface-mount resistors from tight crops and
decodes possible resistance values with exact decimal arithmetic. The
decoder is standard-library-only; the image pipeline reuses the
`pcb-ocr` backend, preprocessing, and orientation search through its
public interface (no second OCR engine, no new dependencies).

```powershell
..\.venv\Scripts\python smd.py decode 472
..\.venv\Scripts\python smd.py read --image path\to\smd_crop.png --out smd_out
..\.venv\Scripts\python smd.py read --image path\to\smd_crop.png --roi 20,20,160,80 --tesseract-cmd 'C:\Program Files\Tesseract-OCR\tesseract.exe' --out smd_out
..\.venv\Scripts\python smd.py eval --labels labels.json --images-dir crops\ --out smd_eval_out
```

## Markings

- 3-digit (`472` → 4700 Ω) and 4-digit (`4702` → 47000 Ω).
- R-notation (`4R7` → 4.7, `R47` → 0.47, `R010` → 0.01).
- EIA-96 with the full 96-entry table (`01C` → 10000 Ω). Default
  multipliers are A–F only (1 … 100000). Sub-unity letters vary by
  manufacturer (X/Y versus S/R charts, H, Z), so they decode only via
  explicit `--aliases`, e.g. `--aliases S=0.1,R=0.01`. An alias that
  disagrees with the default chart yields an extra candidate and an
  ambiguous verdict instead of overwriting anything.
- Zero jumpers (`0`, `00`, `000`, `0000`).

Every candidate carries the original code, scheme(s), exact-ohm string
plus float, a decoded explanation, and evidence. Tolerance is never
inferred: `tolerance_percent` is always null with typical classes
quoted as context only. E-series values never prefer a reading.

## Image pipeline

`read` runs `pcb-ocr` over the crop (both polarities, 0/90/180/270
with arbitration) and decodes every observed word, including
0/O, 1/I/L, 5/S, 8/B confusion alternatives (capped, always labeled
with substitutions). Repairs are never silent: a reading that needs
OCR correction is `ambiguous`, and the raw text is preserved with its
box, confidence, and orientation. Boxes are source-frame pixels;
crops are uncalibrated, so no millimetre output is claimed.

Verdicts: `ok` (one unrepaired value), `ambiguous` (repairs, rivals,
or duplicates), `unknown` (no text or no valid decode), `unavailable`
(no OCR engine). A valid code never proves the object is a resistor;
that hypothesis stays operator-verified (see `object_hypothesis`).

## Evaluation

Labels: `{"images": [{"file": "crop.png", "marking": "472"}]}`.
The report counts exact matches, misses, false confident readings,
and undecided cases per labelled set.

## Test

```powershell
cd smd-reader
..\.venv\Scripts\python -m unittest discover -s tests -v
```

31 deterministic tests: every notation, EIA-96 table integrity and
boundaries, aliases, invalid codes, confusion pairs, orientation
ambiguity, missing backend, empty crops, eval accounting, CLI.

## Limits

- Not validated on real hardware; rendered-sample runs are
  integration checks, not accuracy claims.
- Single tight crops only; neighboring markings become rival
  candidates, and non-resistor markings are not recognized as such.
- Inherits pcb-ocr arbitration: same-ink conflicts resolve by
  parse cleanliness, engine confidence, then upright orientation,
  which can drop a correct rotated read next to confident garbage.
- Millimetre coordinates unsupported (crops carry no calibration).
- No Assembly Memory writes in this iteration.
