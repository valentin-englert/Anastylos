# Capture adapter (`anastylos-microscopy-capture-manifest/1`)

Schema: `schemas/capture-manifest.v1.schema.json`. Example:
`examples/synthetic_mic/capture_node_example/`.

```
session1/
  capture-manifest.json
  frames/frame_000001_20261029T120001000Z.jpg   (original bytes, never rewritten)
```

Manifest fields: `node_id`, `session_id`, `validation_class`, `source`
(camera_id, optical_profile, capture_device, capture_format,
capture_resolution, camera_settings_note), session-level
`orientation_cw_deg`, optional `calibration` (calibration/1 document) and
`frames[]` with `file` (relative, `/` or `\`), `sha256`, `seq`,
`captured_utc` (ISO-8601 UTC ending in `Z`), `orientation_cw_deg`
(0/90/180/270, metadata only), `roi_xywh`, `component_observation_id`.

Core behaviour (`inspect --manifest`):

* each frame is resolved inside the manifest directory; absolute paths,
  drive letters, UNC paths, `..`, `.`, `:` (NTFS streams), reserved device
  names and trailing dots/spaces are rejected;
* (v0.1.1) the frame's **real path** - after every symlink, junction or mount
  point - must lie inside the real path of the manifest directory; other
  Windows reparse points and hard-linked files are rejected (fail-closed);
  a broken link, loop or missing file is rejected as `manifest_rejected`
  before any byte is read;
* (v0.1.1) the frame is opened once, its identity (device + inode / NTFS file
  index) re-checked against the in-root path after opening, and read once;
  those bytes are hashed, decoded and preserved - never a second open;
* the bytes' sha256 must equal the declared one, otherwise that frame is an
  `input_error` record and the batch continues;
* `validate --kind capture_manifest` applies exactly the same checks
  (`capture.verify`), so validation and inspection cannot disagree;
* the original is copied byte-identically into the inspection's
  `evidence/` (content-addressed) and the record links the manifest sha256
  and frame index;
* orientation is recorded and never applied to pixels;
* the manifest calibration applies unless `--calibration` overrides it.

A capture node (e.g. a future exporter on the Pi 3B+) only needs to write
files; it needs no Core code and the Core needs none of its code. Nothing in
this document changes BlobCam; generating such a manifest there would be a
separate, reviewed change in that project.
