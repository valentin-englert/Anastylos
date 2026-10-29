# ANASTYLOS calibration kit

Five black-on-white sheets at 1:1 millimetres. Marker positions live only in `anastylos/layout.py`, which writes `calibration-config.json`. The same file drives the SVG, PDF, and preview.

| PDF | Paper | What it is |
|---|---|---|
| `print/anastylos_surface_a3.pdf` | A3 landscape, 420 × 297 mm | Work surface, trays 1–12, marker IDs 10–16 (42 mm) |
| `print/anastylos_surface_a4.pdf` | A4 landscape, 297 × 210 mm | Compact surface, trays 1–4, IDs 0–4 (35 mm) |
| `print/anastylos_fixture_front.pdf` | A4 landscape | Fixture board FRONT, IDs 20–25 (21 mm) |
| `print/anastylos_fixture_back.pdf` | A4 landscape | Fixture board BACK, IDs 30–35 (21 mm) |
| `print/anastylos_charuco_a4.pdf` | A4 portrait, 210 × 297 mm | ChArUco board, `DICT_6X6_250`, 5 × 7 squares of 30 mm |

Registration markers use `DICT_5X5_100`. IDs 40–99 are reserved. Frames are millimetres, x right, y down. Origins are the top-left of marker 0, 10, 20, and 30. Front and back do not share a transform. The fixture board is 230 × 150 mm with a 172 × 92 mm window and fits the A3 work area only. `controller.dimensions_mm` and `height_mm` stay null until the controller is measured. Each sheet has a horizontal and a vertical 100 mm check line. Ink stays at least 10 mm from the paper edge.

## Install

Python 3.12, isolated virtualenv. From the repository root:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r calibration/requirements.txt
```

Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r calibration\requirements.txt
```

## Native prerequisites on Windows

Two system components sit outside `requirements.txt`:

- `libcairo-2.dll`, loadable by `cairocffi` (a directory on `PATH`).
  Any Windows Cairo build works, e.g. the `bin` directory of the GTK 3
  Windows runtime or a conda-forge `cairo` install. Without it,
  `generate` still writes every SVG and skips PDF/PNG with a warning,
  and 4 of the 13 kit tests fail. The Tesseract install ships a
  private copy that is not on `PATH` and cannot be used.
- `pdftoppm` (poppler) on `PATH`, test-only: two kit tests rasterize
  fresh PDFs through it. Product code never calls it.

Verify, then re-run the kit tests:

```powershell
.\.venv\Scripts\python -c "import cairocffi; print('cairo ok')"
where.exe pdftoppm
cd calibration
..\.venv\Scripts\python -m unittest discover -s tests
```

Print from the SVGs only after installing Cairo, or use the PDFs
already in `print/`.

## Generate and test

```bash
.venv/bin/python calibration/generate.py
cd calibration && ../.venv/bin/python -m unittest discover -s tests -v
```

Windows, from the repository root, then from `calibration`:

```powershell
.\.venv\Scripts\python calibration\generate.py
cd calibration
..\.venv\Scripts\python -m unittest discover -s tests -v
```

## Copy shop

Print the **PDF**, not the preview PNG.

1. Open the PDF in Adobe Reader, SumatraPDF, or similar.
2. Scale: **Actual size / 100%** (German dialog: **Tatsächliche Größe**). Do not use Fit, Shrink to printable area, or borderless zoom (not "An Seite anpassen", not "Auf Druckbereich verkleinern").
3. Paper: the size in the table above. Do not substitute A4 for the A3 surface.
4. Black on matte white paper, about 100 g/m² or heavier. Turn toner-save / eco mode off.
5. After printing, measure both 100 mm lines with a steel ruler, end tick to end tick. Both must read 100 mm. If one does not, reprint at 100%. Record a real measurement later under `measured_print_scale` in `calibration-config.json` as `{"horizontal_mm": ..., "vertical_mm": ...}`.
6. Fixture sheets: the outer dashed rectangle is the board cut. The inner dashed rectangle is the controller window. Glue the board flat. FRONT and BACK are different sheets and different coordinate frames.

ChArUco is the portrait A4 sheet. Glue that flat as well.

## Spatial evidence validation (ANASTYLOS Guided Capture Wave 1)

`spatial/` holds the CAD-0 spatial sidecar validator, replay evaluator,
dataset builder, and the ANDROID handoff JSON (calibration profile +
guidance sign vectors). Stdlib only; see `spatial/README.md`. It consumes
the frozen G0 contract and this kit's `calibration-config.json` read-only
and never edits print masters or config.
