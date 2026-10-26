# ANASTYLOS third-party notices

Inventory of third-party software used by ANASTYLOS, verified 2026-10-26
against `calibration/requirements.txt`, the local `.venv` package metadata
and license files, and the installed Tesseract directory. Machine-readable
form: [`tools/compliance/spdx.json`](tools/compliance/spdx.json) (SPDX 2.3).
Module notes live on: [`pcb-ocr/THIRD_PARTY.md`](pcb-ocr/THIRD_PARTY.md)
is preserved and referenced here, not duplicated.

This file grants no licenses. ANASTYLOS's own licensing is deliberately
deferred; nothing here declares a project license.

## Installed versus bundled

ANASTYLOS distributes source code plus version pins. No third-party
binaries are committed to this repository. Everything below is either
installed into a local virtualenv at setup time or provided as a
system tool by the operator. The obligations in the copyleft rows
trigger when a built environment (wheel set, virtualenv, installer
image) is redistributed — not when this source tree is shared.

## Runtime Python dependencies (pinned)

All pins come from `calibration/requirements.txt` (single declaration;
no other manifest exists). Direct requirements per that file:
`opencv-contrib-python`, `CairoSVG`; the rest is transitive.

| Package | Version | SPDX license | Evidence |
|---|---|---|---|
| opencv-contrib-python | 4.14.0.94 | Apache-2.0 (library) + MIT (packaging) + LGPL-2.1-only (FFmpeg DLL) | License metadata, LICENSE.txt, LICENSE-3RD-PARTY.txt in wheel |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | License-Expression + licenses/ dir in wheel |
| CairoSVG | 2.9.1 | LGPL-3.0-or-later | License metadata + full LGPL-3.0 text in wheel |
| Pillow | 13.0.0 | MIT-CMU (declared; mapping under review, see below) | License-Expression; header shows PIL/HPND lineage |
| cairocffi | 1.7.1 | BSD-3-Clause | LICENSE file + classifier |
| cffi | 2.1.1 | MIT-0 | License-Expression + "MIT No Attribution" text |
| cssselect2 | 0.10.1 | BSD-3-Clause | LICENSE file + classifier |
| defusedxml | 0.7.1 | PSF-2.0 | PSFL metadata + PSF license v2 header |
| pycparser | 3.12 | BSD-3-Clause | License-Expression + LICENSE file |
| tinycss2 | 1.5.1 | BSD-3-Clause | LICENSE file + classifier |
| webencodings | 0.6.1 | BSD-3-Clause | LICENSE file + classifier |

CairoSVG/cairocffi are exercised only by calibration PDF/PNG rendering,
which degrades to SVG when native Cairo is absent. Assembly-memory is
standard-library-only; the other vision modules use OpenCV + NumPy
(+ Pillow where imported).

## Bundled native components (inside wheels, not the repo)

- OpenCV wheel: `opencv_videoio_ffmpeg4140_64.dll` (LGPL-2.1, section
  in LICENSE-3RD-PARTY.txt) plus statically linked third-party
  libraries enumerated in that file (libwebp, libjpeg-turbo, libspng,
  OpenJPEG, TIFF, OpenEXR, protobuf, and others; exact set per file).
- NumPy wheel: vendored components with texts under
  `numpy-2.5.4.dist-info/licenses/` (covered by the composite
  License-Expression above). No separate DLLs on Windows.
- Pillow 13.0.0 wheel: statically linked libjpeg-turbo 3.2.0,
  zlib-ng 1.3.1, libtiff 4.7.2 (versions via `PIL.features`;
  licenses unverified locally — review item).

## Platform, tools, and native libraries (not bundled)

| Component | Version | SPDX license | Role |
|---|---|---|---|
| CPython | 3.12.10 | PSF-2.0 | Interpreter; system install with local LICENSE.txt |
| pip | 25.0.1 | MIT | Installer only; not imported, not pinned |
| Tesseract OCR (UB-Mannheim) | 5.4.0.20240606 | Apache-2.0 | Optional CLI engine for pcb-ocr; local LICENSE + AUTHORS; eng/osd tessdata |
| Tesseract dependency DLLs (~50) | various | Unverified, review item | Installed alongside Tesseract (leptonica, ICU, cairo, font libs, …); no per-component texts ship |
| Cairo native (libcairo) | not installed | Upstream dual LGPL-2.1-only OR MPL-1.1, unverified primary | Optional; absent from `.venv`/System32. Tesseract's private copy is not on PATH and unusable by cairocffi |

No development test frameworks exist (unittest from the standard
library). No external AI services are called at runtime: no network,
HTTP, or cloud imports anywhere under the six modules (verified by
search 2026-10-26).

## Mandatory notices versus acknowledgments

Mandatory (trigger on redistribution of a built environment, not of
this source tree):

- Apache-2.0 (OpenCV library, Tesseract): preserve NOTICE/license
  texts (`cv2/LICENSE*.txt`, Tesseract `doc/`).
- LGPL-3.0-or-later (CairoSVG) and LGPL-2.1-only (FFmpeg DLL):
  provide the license texts, library sources or written offer, and
  allow relinking/replacement per LGPL terms.
- BSD/HPND/MIT-family and PSF texts: keep copyright + license texts
  (`dist-info` LICENSE files) with redistributed copies.

Optional acknowledgments: permissive-licensed transitive packages
beyond their required one-line attributions; the NumPy composite
expression already enumerates its bundle.

Required original texts are preserved where they exist locally
(`dist-info` LICENSE files, `cv2/LICENSE-3RD-PARTY.txt`, Tesseract
`doc/`). Full license texts are intentionally not copied into this
repo; packaging for redistribution must carry them from these sources.

## Redistribution-sensitive dependencies

1. CairoSVG (LGPL-3.0-or-later) — only needed for calibration
   PDF/PNG output; omitting that feature removes this dependency.
2. FFmpeg DLL inside opencv-python (LGPL-2.1) — ships with every
   install of the wheel; exact version/build flags unverified.
3. Pillow static bundle — versions observed, license mapping partly
   unverified (see review list).
4. Tesseract DLL set — only relevant if the toolset itself is
   redistributed; normal use shells out to an operator install.

## Open review items (need a human)

1. Pillow: confirm MIT-CMU versus HPND SPDX mapping with upstream.
2. FFmpeg DLL: exact version and GPL-free build flags.
3. Pillow static libs: confirm libjpeg-turbo / zlib-ng / libtiff
   license identifiers.
4. Tesseract DLL set: per-component licenses if ever redistributed.
5. Cairo: primary-source license + version once installed.

The checker prints the same list from the SPDX annotations:

```powershell
py -3.12 tools/compliance/check.py
```
