# Dependency notices

This package declares **no license of its own** and relicenses nothing.
ANASTYLOS licensing is deliberately deferred (see Anastylos
`THIRD_PARTY_NOTICES.md` at a172fbb9, which is the authoritative inventory).

## Runtime

| dependency | version used | license (per Anastylos inventory) | how obtained |
|---|---|---|---|
| Python | 3.12 | PSF-2.0 | operator install |
| opencv-contrib-python | 4.14.0.94 | Apache-2.0 + MIT packaging + LGPL-2.1-only (bundled FFmpeg) | `pip install -r <ANASTYLOS_ROOT>/calibration/requirements.txt` |
| numpy | 2.5.4 | BSD-3-Clause AND others (see wheel) | same |
| Tesseract OCR (optional) | Anastylos inventory lists UB-Mannheim 5.4.0.20240606; not installed here | Apache-2.0 (its ~50 DLLs: unverified, per Anastylos review item) | operator install; used only through pcb-ocr's own backend |
| Anastylos readers | a172fbb9 | no license declared | separate checkout at ANASTYLOS_ROOT, **not bundled** |

The Core itself imports only the Python standard library, OpenCV and NumPy.
No dependency was added; nothing is vendored; no model is downloaded.
BlobCam is **not** a dependency: generic ideas (original-frame preservation,
per-frame sha256, UTC `Z` timestamps, orientation as metadata, operator-owned
gates) were reimplemented independently; no BlobCam code, architecture or
deploy script is included.

Redistribution obligations (e.g. LGPL for FFmpeg inside the OpenCV wheel)
arise when a built environment is redistributed; this ZIP contains source
only.
