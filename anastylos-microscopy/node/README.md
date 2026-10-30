# ANASTYLOS Raspberry Pi microscope node 0.2.0

The node owns the UGREEN capture device through one supervised GStreamer
pipeline. A `tee` sends untouched MJPEG buffers to the evidence appsink and
sends a separate decoded branch to the HDMI display. Display text and the
startup logo are applied only to the preview branch. The Core remains the
authoritative capture-manifest validator and consumer.

The pipeline shape and recovery approach adapt the owner's existing
GStreamer acquisition implementation from `valentin-englert/blobcam`, commit
`e180aac2480a2c038de5ee61b66702210633af87`, with the owner's authorization
for this appliance task. The adapted files are listed in
[`SOURCE_ISOLATION.md`](SOURCE_ISOLATION.md); BlobCam is not a runtime
dependency, submodule or bundled package.

## Physical smoke check so far

On 2026-10-30, the Pi 3B+ (Raspberry Pi OS 13, 32-bit ARM) was provisioned
with the systemd appliance service. The operator confirmed the branded live
preview on the connected TV and adjusted the microscope focus until the image
was sharp. The service detected a physical UGREEN 25854 disconnect and
reconnect; it returned to `ready`, and the operator confirmed the preview
returned. A software reboot also returned the enabled service to `ready` with
the camera and HDMI connector detected. A physical cold boot was then
performed by safely shutting down the Pi and power-cycling it. The enabled
service returned to `ready` automatically; the operator confirmed live video
and branding on the TV.

A real 1920×1080 MJPEG frame (509,912 bytes) was captured through the shared
service pipeline after cold boot. Pi verification and Windows Core manifest
validation passed. The frame SHA-256 was
`2a5572b2625cff6ad3331a3ce70cef6769796be5ad731d78d8e3fc62aa9cd727`.
The Core resistor reader completed with `unknown`/abstention; this verifies
evidence handoff, not component recognition. The preview process previously
sampled at about 102% of one CPU core. After cold boot the Pi was 54.2°C with
no throttle flag. No physical calibration, GTX 970 inspection or long-duration
stability test has been performed.

## Provision once from Windows

After SSH host-key and account-key enrollment, run from PowerShell:

```powershell
powershell -NoProfile -File .\anastylos-microscopy\node\deploy\provision.ps1
```

If `.local` does not resolve on the Windows PC, pass the Pi's current address
with `-PiHost <address>` and the already trusted hostname with
`-HostKeyAlias <pinned-hostname>`; strict host-key checking remains enabled.

The script probes the Pi OS and ARM architecture, verifies SSH host keys,
stages only the node source, Core source, capture schemas, logo and service
files under `Anastylos-artifacts\microscopy`, then asks before remote package
installation and systemd changes. It uses the existing SSH authentication;
it stores no password or private key. The Pi update installer keeps versioned
source releases and rolls the service back if activation fails. Captures are
stored outside the deployed tree.

The service starts at boot and dynamically finds a capture-capable MJPEG
interface for USB ID `2b89:5854`; it never assumes `/dev/video0`. It probes
the connected HDMI connector and selects its first advertised progressive mode. The preview
is scaled to that mode with its aspect ratio preserved; capture is unaffected.
The preview requires a 1920×1080 camera stream and does not upscale a smaller
stream. Recovery from a non-Full-HD camera mode is not yet physically verified.
The reported display resolution is the selected advertised mode, not a
measurement of the active HDMI signal. With HDMI disconnected it keeps
capture running where possible and exposes status through both a Unix socket
and `/run/anastylos-mic-node/status.json`.

## Node commands on the Pi

The systemd service is the only normal owner of the camera. `capture` requests
the newest fresh frame from that service; it never opens V4L2 independently.

```sh
anastylos-mic-node probe
anastylos-mic-node status
anastylos-mic-node capture \
  --camera-id mustool-hdmi-camera \
  --node-id mic-node-01 \
  --validation-class real_microscope \
  --output-root "$HOME/microscope-captures"
anastylos-mic-node verify \
  "$HOME/microscope-captures/<session>/capture-manifest.json"
```

`probe` is read-only and reports the enumerated devices and formats. The
appliance uses sysfs USB identity plus V4L2 format enumeration, rejects
ambiguous multiple adapters, and chooses a supported MJPEG mode. It does not
change camera controls. A captured timestamp is the Pi host's UTC observation
time when a complete MJPEG buffer reaches appsink, not sensor exposure time.
Every saved JPEG is byte-copied from that compressed buffer and SHA-256 hashed.

`capture` requires the running service. An optional `--device /dev/videoN`
asserts that the service selected the same path; it does not select or open a
second device. The output directory is created as a new immutable session;
the Core schema and file-confinement validator are used before publishing the
manifest.

## Windows handoff

Copy the complete session directory from the Pi to Windows. In PowerShell:

```powershell
$root = 'C:\Users\HP\Documents\Anastylos'
$env:ANASTYLOS_ROOT = $root
$env:PYTHONPATH = Join-Path $root 'anastylos-microscopy\src'
$manifest = 'D:\captures\<session>\capture-manifest.json'
python -m anastylos_microscopy validate --kind capture_manifest $manifest
python -m anastylos_microscopy inspect --manifest $manifest --mode auto `
  --hypothesis axial_resistor --out D:\microscopy-out
```

Choose the hypothesis that the operator actually recorded. The image files are
handed off unchanged; display branding never enters the evidence branch.
The `live_stream` SSH bridge and Windows `live_client` pull the
newest JPEG from the service socket, one request at a time, without opening a
second camera or saving analysis frames. They require a trusted SSH host key
and non-interactive authentication. On Windows, the optional worker starts SSH
without a console and retries offline connections with backoff up to 30 seconds.
`deploy/install-live-windows.ps1` installs desktop shortcuts to start and stop
the worker and disables the former logon task. The worker runs only when started.
The worker keeps bounded diagnostics in
`%USERPROFILE%\Documents\Anastylos-artifacts\microscopy\live-windows.log`.
`live_analysis` runs the existing resistor
reader and its Core assessment on Windows and sends short-lived, unconfirmed
text results to the Pi. The mode assumes an axial resistor; it is not a general
component detector. A coarse image-difference check suppresses results when it
detects a scene change; this is not tracking and has not been validated for all
motion, lighting, or occlusion. No spatial highlights or confidence scores are
drawn. Results expire if updates stop. The preview and evidence branches stay
separate. On 2026-10-30 the operator observed `unknown`, a candidate and a
response to movement on HDMI. In a short hardware test, 163 distinct frames
arrived in 10.05 seconds while the operator observed smooth preview; a concurrent
capture passed Pi hash verification and Windows manifest validation. The Windows
worker published 5.3–6.0 updates/s and measured 135–145 ms from complete frame
receipt to Pi acknowledgement. These are not display FPS or end-to-end display
latency. A separate ten-second sink-counter sample measured 13.7 rendered
preview frames/s and about 30.2 source frames/s; the HDMI mode was 1080p60.
The Pi reported 58°C and `0x80000` (the soft temperature limit had occurred;
no current throttle bits were set). The operator also confirmed live preview and the offline message with
the Windows worker stopped, and CV recovery after restarting it. Real-world
recognition accuracy, motion coverage, actual display FPS, full display latency
and unattended cold-start behavior remain unverified.

The operator reported a wrong candidate for a 3.3 kΩ resistor, and `unknown`
with a 1 kΩ resistor in view, including after placing a single resistor and
observing poor color rendition. In a diagnostic frame showing four components, the
reader found too few bands in the full image; manually selected individual
regions produced two `unknown` and two `ambiguous` outputs. Those regions are
diagnostic, not automatic localization or proof of each component's value.
This real-world observation is not a calibrated accuracy evaluation.
A later neutral-paper frame (SHA-256
`3bfdbbc6529570733b9d057305203fc8fc0b328ff097426fa0f68c45472fc302`)
contained one visible resistor. Both the full image and a manually selected body
region yielded `unknown` with three detected bands. Two paper patches had median
RGB values 123/126/116 and 123/127/120, respectively, without clipped pixels in
those patches. Ordinary paper and one frame are not a color calibration.

The optional Windows scheduled-task installer is `deploy/install-live-windows.ps1`.
It starts the worker for the current Windows account at logon, not while Windows
is off or before that account logs in. The worker needs the existing `.venv`,
the checkout, and working passwordless SSH with a pinned Pi host key. If the
workstation or network is unavailable, the Pi still runs the microscope.
The currently provisioned address is an IP address; if it changes, update the
scheduled task or restore resolution of the pinned Pi hostname.

## Requirements and limits

- Raspberry Pi OS/Debian on ARMv7 or ARM64, Python 3, V4L2, GStreamer,
  PyGObject/GStreamer bindings, and the system packages listed in
  [`../DEPENDENCY_NOTICES.md`](../DEPENDENCY_NOTICES.md).
- No desktop environment is required. The logo is the existing ANASTYLOS PNG
  copied byte-for-byte into `node/assets/`.
- `anastylos-mic-node status` is the diagnostic interface when HDMI is absent.
- Evidence capture remains an operator-triggered node command; this release
  does not add a physical button, touchscreen or remote UI.
- No real GTX 970 inspection, metric calibration or long-duration stability
  validation has been performed.
