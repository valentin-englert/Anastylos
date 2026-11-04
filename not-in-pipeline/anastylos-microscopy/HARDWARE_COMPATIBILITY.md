# Hardware compatibility

**Status: limited Raspberry Pi display/camera smoke passed; the appliance
service and evidence handoff are not yet physically validated.**

## Target chain

| Element | Intended role | Verified characteristics |
|---|---|---|
| MUSTOOL trinocular microscope with HDMI camera | optical source | Live feed was visible at the HDMI monitor during the bounded preview smoke; optical profile and microscope calibration remain unverified |
| UGREEN 25854 HDMI-to-USB adapter | video capture bridge | USB `2b89:5854`; `/dev/video0` reports capture capability; MJPEG 1360×768@30 and 1920×1080@30 advertised |
| Raspberry Pi 3B+ with Raspberry Pi OS/Linux | runs the capture/preview node | Pi 3B+ Rev 1.3, Raspberry Pi OS 13, armv7l; GStreamer 1.26.2. Appliance packages/service not installed |
| Windows PC | runs the Microscopy Core and ANASTYLOS readers | Existing consumer; real-session handoff remains pending |
| MSI GTX 970 board | intended first real inspection target | Not photographed with this node |

## MIC-NODE appliance smoke (2026-10-30)

| Check | Result |
|---|---|
| HDMI hotplug and EDID | PASS after the operator replaced a defective HDMI cable; DRM connector `HDMI-A-1` connected, EDID 256 bytes, modes reported |
| KMS test pattern | PASS; bounded SMPTE pipeline using `kmssink`, connector 35, plane 86 and `force-modesetting=true` completed |
| Direct live camera preview | PASS while running for two bounded manual GStreamer runs; the operator confirmed the MUSTOOL feed was visible. The processes ended afterward, so preview is not currently persistent. |
| Preview stability / Pi load | not tested beyond the short smoke |
| Shared evidence + preview service | not tested; PyGObject/GStreamer Python bindings are absent and provisioning has not been approved/run |
| Automatic boot, branded startup, restart and reconnect | not tested |
| Physical cold boot | not tested |

The Core consumes a capture directory and manifest. The appliance service will
enumerate capture-capable V4L2 devices by USB identity and advertised MJPEG
formats; it will not assume a fixed device path. It does not change camera
controls. See
[`node/README.md`](node/README.md).

## Core image assumptions

* JPEG or PNG, complete files (JPEG EOI / PNG IEND checked), max 512 MiB and 120 MP.
* EXIF orientation is recorded, never applied; manifest orientation is
  metadata only.
* Calibration is valid only for the exact resolution it declares and must
  carry provenance; otherwise all geometry stays in pixels.
* No calibration or optical profile is inferred from camera or lens labels.

## Physical gates (operator-owned)

| Gate | Check | Status |
|---|---|---|
| MC-HW-01 | Core + readers run on the Windows PC, both test suites green | **Historical PASS** — v0.1.2 QA on HEAD `f98badb3`; ten reader code hashes match the pin |
| MC-HW-02 | Tesseract installed on the PC; `readers` shows OCR available | not started |
| MC-HW-03 | Real MUSTOOL frame of the GTX 970 inspected; originals byte-identical (sha256) | not started |
| MC-HW-04 | Stage-micrometer calibration measured per profile/zoom/resolution | not started |
| MC-HW-05 | Capture-node manifest from a real session accepted with zero hash mismatches | not started |
| MC-HW-06 | Real GTX 970 dev/held-out evaluation, ground truth by operator | not started |

The display smoke does not change MC-HW-03 or MC-HW-05. Both require a real
microscope capture, byte-preservation evidence and a Windows Core handoff.
A gate may be set PASS only with operator evidence (photos, hashes, command
output) recorded alongside it.
