# Hardware compatibility

**Status: no hardware was used or validated in this release.** All images in
this package are synthetic renders, explicitly labelled. No physical gate is
marked PASS by this work.

## Intended chain

| Element | Role | Source of facts |
|---|---|---|
| MUSTOOL 24MP HDMI microscope camera | optics + sensor; settings only on the device / IR remote | BlobCam `docs/hardware.md` @ e180aac2 |
| UGREEN 25854 HDMI-to-USB capture (`2b89:5854 MACROSILICON`) | UVC capture, MJPEG 1920x1080 listed | same |
| Raspberry Pi 3B+ (BlobCam) | **future** capture source only; writes files + manifest | same |
| Windows PC | runs the Core and the Anastylos readers | this package |
| MSI GTX 970 board | intended first real inspection target | not yet photographed |

The Core never opens a camera device, never talks to the Pi, never installs
or modifies BlobCam. Any capture tool that produces JPEG/PNG files plus a
`capture-manifest.json` can feed it.

## What the Core assumes about images

* JPEG or PNG, complete files (JPEG EOI / PNG IEND checked), max 512 MiB and 120 MP.
* EXIF orientation is recorded, never applied; manifest orientation is
  metadata only.
* Calibration is valid only for the exact resolution it declares and must
  carry provenance; otherwise all geometry stays in pixels.
* The micro-05x FOV (~10 x 6 mm) quoted in BlobCam docs is marked there as
  "re-measure before trusting"; the Core ships no calibration for it.

## Physical gates (operator-owned)

BlobCam's own gate table (G-001..G-004 PASS; G-005 READY_FOR_PHYSICAL_TEST;
G-006..G-012 not started) belongs to BlobCam and was only read, not changed.
Gates for this Core, all **NOT PASSED / not started**:

| Gate | Check | Status |
|---|---|---|
| MC-HW-01 | Core + readers run on the Windows PC, both test suites green | **PASS** — v0.1.2 QA on HEAD `f98badb3`; ten reader code hashes match the pin |
| MC-HW-02 | Tesseract installed on the PC; `readers` shows OCR available | not started |
| MC-HW-03 | Real MUSTOOL frame of the GTX 970 inspected; originals byte-identical (sha256) | not started |
| MC-HW-04 | Stage-micrometer calibration measured per profile/zoom/resolution | not started |
| MC-HW-05 | Capture-node manifest from a real session accepted with zero hash mismatches | not started |
| MC-HW-06 | Real GTX 970 dev/held-out evaluation, ground truth by operator | not started |

A gate may be set PASS only with operator evidence (photos, hashes, command
output) recorded alongside it.
