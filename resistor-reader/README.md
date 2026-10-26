# ANASTYLOS resistor color-code reader (experimental)

Reads axial through-hole resistor color bands from a macro photo crop.
Use the calibration virtualenv (OpenCV, NumPy). The decoder is
standard-library-only; only the vision pipeline needs OpenCV.

```powershell
..\.venv\Scripts\python read.py --image path\to\resistor.jpg --out result_dir
..\.venv\Scripts\python read.py --image path\to\resistor.jpg --roi 100,80,700,180 --out result_dir
```

Start with a tight crop or `--roi x,y,w,h` around the resistor body.
Output is `result.json` (also printed) plus `annotated.png` with the
detected bands marked.

## What it does

- `decode.py` — IEC 60062 decoding for 4-band and 5-band resistors
  (significant digits, multiplier, tolerance), both reading directions,
  plus optional 6-band TCR. Validity rules decide the direction; standard
  resistor values are never used to prefer a reading.
- `vision.py` — deskew, body-profile band detection, HSV color
  classification with scored alternatives, then decode. Weak evidence
  yields several candidates (`ambiguous`) or an explicit `unknown` with a
  reason. Scores are geometric means of band-color confidences, 0–1.

## Status meanings

- `ok` — one reading stands clearly above the alternatives.
- `ambiguous` — near-tied readings (e.g. a sequence valid both ways);
  every candidate is listed, nothing is picked silently.
- `unknown` — wrong band count, no valid decode, or the best score is
  below 0.5. Low-score candidates may be listed for inspection.

## Validation state

Experimental: 30 tests pass on deterministic synthetic renders only
(orientation, rotation, lighting, glare, similar colors, invalid and
missing bands, must-be-unknown cases). This says nothing about real
photos. Do not claim real-hardware recognition until a labelled
real-image set is scored.

## Test

```powershell
cd resistor-reader
..\.venv\Scripts\python -m unittest discover -s tests -v
```

`test_decode.py` runs on plain Python 3.12; `test_vision.py` needs the
virtualenv.

## Limits

- Needs a crop where the body dominates; busy backgrounds defeat the
  body mask.
- Harsh combined glare, darkness, and gradients can over-segment bands
  and return unknown.
- Gold/silver/white/gray discrimination is the weakest classifier axis.
- No memory integration yet: readings are not yet imported as
  disassembly evidence.
