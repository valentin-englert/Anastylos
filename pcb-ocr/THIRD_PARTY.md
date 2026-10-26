# Third-party inventory: pcb-ocr

No new Python packages were added for this module. It reuses the
repository's pinned environment (`calibration/requirements.txt`) plus
one optional external binary.

| Dependency | Version observed | License | Role | Install |
|---|---|---|---|---|
| opencv-contrib-python | 4.14.0.94 | Apache-2.0 | preprocessing, rotation, annotation | pinned in requirements.txt |
| numpy | 2.5.4 | BSD-3-Clause | array handling | pinned in requirements.txt |
| Tesseract OCR | 5.4.0.20240606 (UB-Mannheim build, installed 2026-10-26, eng tessdata) | Apache-2.0 | optional OCR engine behind the `tesseract` backend | external binary + tessdata, operator-installed via winget; never fetched at runtime |

License notes: Apache-2.0 and BSD-3-Clause are permissive licenses
requiring preservation of copyright/license notices, which live with
their distributions. No GPL/AGPL code is introduced. If a different
OCR backend is added later, extend this table instead of silently
depending on it.
