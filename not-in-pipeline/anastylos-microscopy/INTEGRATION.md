# Integration

## Position in the system

```
[MUSTOOL microscope] --HDMI--> [UGREEN 25854] --USB--> [ANASTYLOS file-based node on Pi 3B+]
                                                              |
                                          frames + capture-manifest.json (files, copied/shared)
                                                              v
                       [Windows PC: ANASTYLOS Microscopy Core] --imports--> [Anastylos readers @ a172fbb9]
                                                              |
                                     inspection.json / report.md / reviews/*.json (files)
                                                              v
                                    [operator review]  -> (future, manual) Assembly Memory import
```

The Core is a pure consumer of files. The only contract with a capture node
is `schemas/capture-manifest.v1.schema.json`; the only contract with
downstream tools is `schemas/microscopy-inspection.v1.schema.json` plus the
review schema.

## Python API

```python
from anastylos_microscopy.orchestrator import inspect_image
from anastylos_microscopy.runner import inspect_directory, inspect_manifest, replay
from anastylos_microscopy.evaluation import evaluate

record, path = inspect_image("r1.jpg", "out", mode="multi", readers=["resistor", "smd"],
                             roi=[10, 10, 300, 120], calibration_doc=cal_json,
                             clock="2026-10-29T12:00:00Z")        # clock -> deterministic
```

Since v0.1.1 `inspect_image(..., image_bytes=data)` accepts bytes the caller
has already read (the path is then a label only), and
`capture.read_frame(root, rel)` returns confined frame bytes plus a
confinement report.

`ocr_backend=` accepts any object with the pcb-ocr backend interface
(`available()`, `version()`, `recognize(variant)`); this is how the tests inject a
deterministic stub. With no backend the readers' own Tesseract backend is used.

## Exit codes and batch outcome (v0.1.1)

Automation must use the process exit status, then `batch.json`:

| exit | meaning | `batch.json` `outcome` |
|---|---|---|
| 0 | all entries `completed` (abstentions included) | `success` |
| 1 | empty batch, invalid manifest/input, or nothing usable | `failure` / `empty` (no file for empty dirs) |
| 3 | `ANASTYLOS_ROOT` missing/invalid (no file written) or all entries `unavailable` | `environment_unavailable` |
| 4 | at least one usable record and at least one problem entry | `partial_failure` |

Entry `processing` ∈ `completed`, `completed_with_reader_errors`,
`unavailable`, `reader_error`, `input_error`, `manifest_rejected`, `crashed`
(`runner.classify_record`, `runner.exit_code_for`). Python API callers get
the same values in `batch["exit_code"]` / `batch["outcome_counts"]`;
`inspect_batch`, `inspect_directory` and `inspect_manifest` raise
`env.ReaderEnvironmentError` before processing when the root is missing.

## Core evidence assessment (v0.1.2)

For reader `resistor`, a native `ok` is held as `ambiguous` when
`assessment.assess_resistor` finds the reader's evidence insufficient. Consumers
must read `recognizers[].status` (Core verdict) and may read
`recognizers[].native_status` / `native_result` (unchanged reader output) and
`recognizers[].core_assessment` (checks, measurements, thresholds). Nothing
downstream should treat `native_status: ok` as a supported identification.

Assessor v2 also holds a result when one decoded band has at least 10% of its
pixels with a channel clipped at 250 or above. One compromised band can change
the decoded value. This is a fixed uncertainty rule selected using the
development render grid; it is not a calibrated probability or a claim of
resistor accuracy. The held-out synthetic run and remaining false accepts are
recorded in `qa-output/run-v012/QA_REPORT.md` in the integration worktree.

## Reader adapters

`src/anastylos_microscopy/adapters.py` has one adapter per reader. Each calls
the reader's public `analyze(...)` exactly as its own CLI does and maps the
native result into observed / interpreted / candidates. The native result is
stored verbatim (`recognizers[].native_result`); a test asserts parity with a
direct reader call. Calibration is translated per reader:

| reader | spelling passed | source |
|---|---|---|
| polarity, washer | `frame_metadata.pixels_per_mm` | calibration.for_reader("pixels_per_mm") |
| screw_measure | calibration file (`pixels_per_mm`, provenance) loaded by its own `load_calibration` | "screw_measurement_file" |
| pcb_ocr | `frame_meta.px_per_mm` (+ origin) | "px_per_mm" |
| fingerprints, connectors | `px_per_mm` | "px_per_mm" |
| resistor, smd, markings, screw | none (pixel-only readers) | - |

## Capture-node contract

A capture node writes original frames (never re-encoded by the Core) and a
manifest listing for each frame: relative path, sha256, optional
`captured_utc` (UTC, `Z`), `seq`, `orientation_cw_deg` (metadata only, pixels
never rotated), `roi_xywh`, `component_observation_id`; plus `source`
(camera_id, optical_profile, capture device/format), an optional calibration
and a `validation_class` (`synthetic`, `sample`, `real_microscope`, ...).
The Core rejects absolute paths, drive letters, UNC paths, `..`/`.`, NTFS
stream and device names, and any frame whose real path (after symlinks,
junctions, mount points) is outside the manifest's directory, plus hash
mismatches, per frame, and continues with the rest. Each accepted frame is
read once; the same bytes are hashed, decoded and preserved (v0.1.1, QA-02).
A capture node should therefore write plain files (no links, no hard links)
inside the session folder. See
`documentation/capture-adapter.md`.

The Pi appliance in `node/` has one supervised GStreamer owner for the V4L2
device. Its `tee` sends the compressed MJPEG buffer unchanged to an appsink for
capture and a separate decoded branch to KMS for display. The `capture` CLI
requests a fresh frame from that owner over a local Unix socket; it never opens
the camera while the preview service is active. HDMI branding and status are
display-only. If the display branch fails, the service retries it while the
capture branch remains available where possible. The runtime state is
available through `anastylos-mic-node status` and the service status file.

The owner-authorized pipeline and deployment adaptation is disclosed in
[`node/SOURCE_ISOLATION.md`](node/SOURCE_ISOLATION.md). The implementation
keeps the existing capture manifest, reader adapters and evidence contracts.

## Upstream revisions

| dependency | revision | role |
|---|---|---|
| valentin-englert/Anastylos | `a172fbb9e127e7a3a93b128516b6592bfbbc81ef` | runtime (ANASTYLOS_ROOT); reader code hashes pinned in `src/anastylos_microscopy/pinned_reader_hashes.json` |
| valentin-englert/Anastylos | `f98badb38515cbc415a8da466189218fc84d8794` | revision of the independent Windows QA of v0.1.0 (context only; not the pin) |

## Remaining integration steps (not done in v0.1.2)

The 2026-10-30 hardware smoke subsequently confirmed a short direct preview on
the Pi after replacing a defective HDMI cable. The shared appliance service is
not yet installed; its package installation and service activation still
require the owner's confirmation.

1. The current Windows HEAD `f98badb3` is not the exact reader pin
   `a172fbb9`; all ten reader code hashes match. Repeat against an exact pin
   if release policy requires an exact checkout.
2. Install Tesseract on the PC; re-run MIC-01..03 with the real engine.
3. Capture a real MSI GTX 970 dataset with the MUSTOOL chain (development and
   separately held-out images, ground truth by the operator), freeze the
   harness fingerprint, then evaluate held-out once.
4. Measure a stage-micrometer calibration per optical profile/zoom/resolution
   and store it as `anastylos-microscopy-calibration/1`.
5. Run MC-HW-03 and MC-HW-05 with a real microscope frame and retain the
   operator evidence described in `node/README.md`.
6. Decide, with the operator, an import path from confirmed review records
   into Assembly Memory (out of scope here; the Core never writes it).
7. Optionally upstream the adapter layer into Anastylos once reviewed.
