# ANASTYLOS component marking reader

Recognizes visible markings on electronic components from tight
crops and extracts structured information with evidence. OCR comes
from the `pcb-ocr` backend, preprocessing, and orientation search
through its public interface (no second engine, no downloads);
interpretation is deterministic stdlib-only parsing.

```powershell
..\.venv\Scripts\python markings.py parse LM358N 2413
..\.venv\Scripts\python markings.py read --image path\to\crop.png --out markings_out
..\.venv\Scripts\python markings.py read --image path\to\crop.png --tesseract-cmd 'C:\Program Files\Tesseract-OCR\tesseract.exe' --mapping codes.json --out markings_out
..\.venv\Scripts\python markings.py eval --labels labels.json --images-dir crops\ --out markings_eval_out
```

## What is read

- Integrated circuits: part-number candidates plus date/lot-code
  candidates per text line. A short top marking shared across parts
  never resolves to one component here.
- Capacitors: explicit capacitance (`100n`, `4u7`, `10p`),
  voltage (`25V`), observed tolerance letters, and `+`/`-`
  polarity marks. Bare 3-digit codes keep their code form with
  the quantity explicitly unconfirmed.
- Fuses: current (`T2A`, `F500mA`) with observed speed letter,
  voltage, and combined or word-spanning `T 2A 250V` markings.
- Diodes and transistors: visible identifiers transcribed as
  candidates (`1N4148`, `BC547`); short SOT-style codes stay
  unresolved without a mapping.
- Anything else: raw alphanumeric text transcribed with no
  interpretation.

Only well-defined notation is parsed. Tolerance is never inferred
from a letter, a series prefix never proves a component family,
and a bare number is never guessed to be capacitance, voltage,
a date code, or a manufacturer ID.

## Evidence levels

Every line reading separates four levels: the observed OCR words
(raw text, box, confidence, orientations), parsed marking values
(quantities with units), candidate identities (transcriptions,
resolved only through an operator-supplied `--mapping` file of
`{"codes": {"1AM": "MMBT3904"}}`), and the verified specification,
which is always null here — confirming a part needs a datasheet
step outside this module.

OCR-confusion alternatives (0/O, 1/I/L, 5/S, 8/B) are tried only
when raw text transcribes without parsing; raw always wins ties,
repairs are labeled with substitutions, and any repair forces
`ambiguous`.

Verdicts: `ok` (coherent reading), `ambiguous` (rivals, repairs,
short codes, unconfirmed quantities, low confidence), `unknown`
(no text or nothing interpretable), `unavailable` (no OCR engine).

`interpret_words` accepts an optional `assistant` callable for
future provider-agnostic vision/model help. It runs locally, its
notes are recorded verbatim, and it never changes a verdict. No
cloud service is called.

## Evaluation

Labels: `{"images": [{"file": "crop.png", "marking": "LM358N",
"status": "ok"}]}`. `marking` is the expected top-line match text
(null skips the text check); `status` defaults to `"ok"`. The
report counts exact matches, misses, false confident readings,
and undecided cases per labelled set. Stub fixtures prove the
harness only.

## Test

```powershell
cd component-markings
..\.venv\Scripts\python -m unittest discover -s tests -v
```

`tests/test_parse.py` is stdlib-only and also runs on plain
`py -3.12`.

## Limitations

- Validated with stub OCR backends only. No real-hardware
  accuracy is claimed; connect the Tesseract backend and label
  real crops before trusting any reading.
- Words the OCR engine merges or splits differently than the
  label lines will group or parse differently; the raw words are
  always preserved for review.
- Manufacturer-dependent codes resolve only through an explicit
  mapping file; an incorrect mapping resolves incorrectly, so
  the mapping itself is operator-owned evidence.
- Non-Latin scripts, logos, and symbol-only marks (aside from
  `+`/`-`) are not interpreted.
