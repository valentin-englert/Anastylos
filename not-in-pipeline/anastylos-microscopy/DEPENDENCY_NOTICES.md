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
| `v4l2-ctl` (node only) | Raspberry Pi OS package version chosen by operator | consult the exact target OS package metadata | invoked as an OS executable; not bundled, installed or linked by this source |

## Raspberry Pi appliance system packages

The Windows provisioning action installs only missing packages from the target
Pi OS apt repositories and does not upgrade packages already installed. On
the tested Pi (Raspberry Pi OS 13, armhf; apt metadata checked 2026-10-30),
these candidates were observed:

| package | candidate / installed version | used for | license status |
|---|---:|---|---|
| `python3-gi` | 3.50.0-4 / not installed | GObject Introspection bindings | package copyright not inspected; `NOASSERTION` |
| `python3-gst-1.0` | 1.26.0-1 / not installed | Python GStreamer overrides | package copyright not inspected; `NOASSERTION` |
| `gstreamer1.0-x` | 1.26.2-1+rpt3+deb13u2 / not installed | Pango `textoverlay` for the display-only status and slogan | package copyright not inspected; `NOASSERTION` |
| `gstreamer1.0-tools` | 1.26.2-2 / installed | `gst-inspect` and operator diagnostics | package copyright not inspected; `NOASSERTION` |
| `gstreamer1.0-plugins-base` | 1.26.2-1+rpt3+deb13u2 / installed | GStreamer base elements and appsink | package copyright not inspected; `NOASSERTION` |
| `gstreamer1.0-plugins-good` | 1.26.2-1+deb13u2 / installed | GdkPixbuf logo overlay | package copyright not inspected; `NOASSERTION` |
| `gstreamer1.0-plugins-bad` | 1.26.2-3+rpt4+deb13u3 / installed | `kmssink` display output | package copyright not inspected; `NOASSERTION` |
| `v4l-utils` | 1.30.1-1 / installed | read-only V4L2 probe | package license remains subject to target image review |

The package manager resolves architecture-specific dependencies from the
operator's Pi OS image. No Pi OS binary is committed or included in the source
bundle. Before redistributing a configured SD-card image, review and include
the notices for the exact resolved package closure; the current candidate
versions are not a license conclusion.

The Core itself imports only the Python standard library, OpenCV and NumPy.
The capture utility uses Python's standard library and invokes `v4l2-ctl`;
the appliance pipeline additionally imports the Pi OS-provided PyGObject and
GStreamer bindings. Neither the utility nor the system libraries are bundled.
Review the exact target OS package licenses before redistributing a configured
device image.

Redistribution obligations (e.g. LGPL for FFmpeg inside the OpenCV wheel)
arise when a built environment is redistributed; this ZIP contains source
only.
