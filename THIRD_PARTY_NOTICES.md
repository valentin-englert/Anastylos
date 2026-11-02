# ANASTYLOS third-party notices

Inventory of third-party software used by ANASTYLOS, verified 2026-10-26
against `calibration/requirements.txt`, the local `.venv` package metadata
and license files, and the installed Tesseract directory; npm development
dependencies verified 2026-10-27 against
`capture-companion/app/package.json`, its lockfile, and installed
`node_modules` metadata; the ML Kit Android pin verified 2026-10-27
against `capture-companion/android/gradle/libs.versions.toml` and the
cached Maven POM. Machine-readable
form: [`tools/compliance/spdx.json`](tools/compliance/spdx.json) (SPDX 2.3).
Module-level notes in [`pcb-ocr/THIRD_PARTY.md`](pcb-ocr/THIRD_PARTY.md)
and [`capture-companion/app/THIRD_PARTY.md`](capture-companion/app/THIRD_PARTY.md)
are preserved and referenced here, not duplicated.

This file grants no licenses. ANASTYLOS's own licensing is deliberately
deferred; nothing here declares a project license.

## Installed versus bundled

ANASTYLOS distributes source code plus version pins. No third-party
binaries are committed to this repository. Everything below is either
installed into a local virtualenv at setup time or provided as a
system tool by the operator — with one exception: the vendored
`meta-sam-parser` SOURCES are committed under `sam-tracking/vendor/`
(see "Vendored source dependencies"). The obligations in the copyleft
rows trigger when a built environment (wheel set, virtualenv,
installer image) is redistributed — not when this source tree is
shared; the vendored sources instead travel with their own license
file wherever the tree goes.

## Runtime Python dependencies (pinned)

All Python pins come from `calibration/requirements.txt` (single Python
declaration). Direct requirements per that file:
`opencv-contrib-python`, `CairoSVG`; the rest is transitive. A second
manifest, `capture-companion/app/package.json` (npm, exact pins plus a
committed lockfile), declares development-only frontend tooling; see
below. A third manifest,
`capture-companion/android/gradle/libs.versions.toml` (Gradle version
catalog, exact pins), declares the native Android dependencies; see
below. These are the only three manifests in the tree.

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

## Frontend development-only dependencies (pinned)

The shipped capture PWA has zero runtime dependencies: the production
bundle is built solely from first-party TypeScript. Everything below is
development-only (build, type checking, tests), pinned exact in
`capture-companion/app/package.json` with a committed
`package-lock.json` (lockfile v3). Licenses verified 2026-10-27 from
the `license` field of each installed `node_modules` package; the
Python validator (`capture-companion/python/`) is standard-library-only
and adds no dependency.

| Package | Version | SPDX license | Role | Evidence |
|---|---|---|---|---|
| vite | 8.3.5 | MIT | dev server + production bundler | package.json license field in node_modules |
| typescript | 7.0.2 | Apache-2.0 | static type checking (`tsc`) | package.json license field in node_modules |
| vitest | 5.0.5 | MIT | unit test runner | package.json license field in node_modules |
| fake-indexeddb | 6.2.6 | Apache-2.0 | IndexedDB emulation for storage tests only | package.json license field in node_modules |
| @types/node | 26.6.5 | MIT | Node type declarations for tests/build scripts | package.json license field in node_modules |

Transitive npm packages (lockfile closure) are not inventoried
individually; their licenses are unverified (review item below). No
transitive package ships in the PWA bundle. No telemetry, font, icon,
CSS, or analytics package is used.

## Android native dependencies (pinned)

The native Capture app (`capture-companion/android/`) resolves its
dependencies from `gradle/libs.versions.toml` (exact pins). Unlike the
Python and npm sets above, Android **runtime** libraries ship inside
the built APK, so their terms govern APK redistribution. Per-artifact
POM verification is scheduled for M5 delivery (review item 8); the
one M2-relevant artifact is inventoried individually now:

| Package | Version | SPDX license | Role | Evidence |
|---|---|---|---|---|
| com.google.mlkit:barcode-scanning | 17.3.0 | LicenseRef-MLKit-Terms (custom Google terms, not an SPDX-listed license) | Bundled on-device QR model for the pairing scanner; runs fully offline | `<license><name>` in cached `barcode-scanning-17.3.0.pom` |
| Khula font (Latin subset) | 1.002 | OFL-1.1 | App typeface, bundled in the APK (`res/font/khula_*.ttf`) and the session UI (`tools/session-ui/web/fonts/`, with the OFL text). Copyright (c) 2014, Erin McLaughlin; digitized data copyright 2010, Google Corporation. The OFL text must accompany a redistributed APK | Name table of the TTF (IDs 0, 14) |
| Lucide icons `info`, `arrow-up-right` (inline SVG in the session UI) | 1.17.0 | ISC AND MIT | Copyright (c) 2026 Lucide Icons and Contributors; Feather-derived: Copyright (c) 2013-present Cole Bemis | `tools/session-ui/web/assets/ICONS.md` |

The bundled ML Kit model performs no network calls for detection:
scanning works airplane-mode offline, and no image or scan content
leaves the phone through this library. The custom terms still need a
human read before the APK is shared (review item 7).

## Vendored source dependencies (committed)

Unlike everything above, these third-party SOURCES are committed to
this repository (no PyPI/npm/Gradle pin; no purl in the SPDX file by
design). Module-level notes in
[`sam-tracking/THIRD_PARTY.md`](sam-tracking/THIRD_PARTY.md) are
preserved and referenced here, not duplicated.

| Package | Version | SPDX license | Role | Evidence |
|---|---|---|---|---|
| meta-sam-parser (vendored) | 0.1.0 | LicenseRef-SAM-License (custom Meta terms, not an SPDX-listed license) | Official SAM 3.x wire-grammar parser + mask decoder used by `sam-tracking/` only | `sam-tracking/vendor/VENDOR.md` (pinned commit `40318970…`, per-file SHA-256) + `sam-tracking/vendor/LICENSE.meta-sam` |
| COCO API mask module port (inside meta-sam-parser) | cocoapi `maskApi.c` / `_mask.pyx` | BSD-2-Clause-Views | RLE routines ported in `meta_sam_parser/_mask_conversion.py` | Copyright (c) 2014, Piotr Dollar and Tsung-Yi Lin; full notice, conditions and disclaimer in `sam-tracking/vendor/THIRD_PARTY_NOTICES.md` (upstream file, verbatim) |

The SAM License file travels with the tree per its section 1.b.i, and
the COCO API notice travels in `sam-tracking/vendor/THIRD_PARTY_NOTICES.md`
as BSD-2-Clause condition 1 requires. The SAM License was reviewed by
the owner on 2026-10-29 and distribution in the owner's private
repository approved; section 1.b.ii (acknowledge the SAM Materials in
any research publication) remains a duty for future publications.
Historical caveat: commits `3e8cb59c` … `89aad323` contain the vendored
files without the COCO API notice; the notice was added forward-only
and history was not rewritten.

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

Python tests use unittest from the standard library; the capture PWA's
TypeScript tests use vitest (development-only, see above). The offline
core calls no external AI services. Python has no network, HTTP, or
cloud imports anywhere except the explicitly isolated
`sam-tracking/anastylos_sam/client.py` (verified by search 2026-10-28;
it speaks to `https://api.meta.ai/v1` only under explicit
`--live --upload-ok` operator opt-in with a ledgered budget); the
PWA's only network use is same-origin service-worker caching of its
own app shell (no evidence upload, no third-party calls).

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
- MIT / Apache-2.0 (capture devDependencies): preserve copyright and
  license texts with redistributed copies of those tools; they do not
  ship in the PWA bundle.

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
5. ML Kit barcode-scanning (custom ML Kit Terms of Service, not an
   SPDX-listed license) — ships in the Android APK; human review
   required before sharing the APK beyond the developer workstation.
6. Android Maven closure (AndroidX, CameraX, Kotlinx, …) — ships in
   the APK; per-artifact licenses unverified until M5.
7. meta-sam-parser sources (LicenseRef-SAM-License, with a
   BSD-2-Clause-Views COCO API port) — committed under
   `sam-tracking/vendor/`; shared with every copy of this tree; SAM
   License human-reviewed 2026-10-29 for the private repository.

## Open review items (need a human)

Every pinned dependency is directly inventoried (11 Python pins, 5 npm
devDependencies, 1 Maven pin, 1 vendored source tree, the
CPython/pip/Tesseract platform entries, and the observed native
bundles); the items below are the license questions that stay open.
The checker prints 10 `REVIEW:` annotations from the SPDX file — this
list groups them into 8 open narrative items (the three Pillow
static-library annotations share item 3); item 9 is human-reviewed:

1. Pillow: confirm MIT-CMU versus HPND SPDX mapping with upstream.
2. FFmpeg DLL: exact version and GPL-free build flags.
3. Pillow static libs: confirm libjpeg-turbo / zlib-ng / libtiff
   license identifiers.
4. Tesseract DLL set: per-component licenses if ever redistributed.
5. Cairo: primary-source license + version once installed.
6. npm transitive closure: per-package licenses unverified; confirm
   before redistributing `node_modules` (direct deps are MIT/Apache-2.0
   per installed metadata).
7. ML Kit Terms of Service: custom Google terms (not an SPDX-listed
   license); a human must confirm redistribution, attribution, and
   data-processing obligations before the APK is shared beyond the
   developer workstation.
8. Android Maven closure: per-artifact POM license fields unverified;
   inventory each runtime-shipped artifact individually before M5 APK
   distribution.
9. SAM License — human-reviewed 2026-10-29 by the owner: redistribution
   with the license text (1.b.i) is met, distribution in the owner's
   private GitHub repository approved; publication acknowledgment
   (1.b.ii) applies to any future publication; any wider distribution
   (public repository, packages) needs a new decision.

The checker prints the same list from the SPDX annotations:

```powershell
py -3.12 tools/compliance/check.py
```
