# Calibration (`anastylos-microscopy-calibration/1`)

Schema: `schemas/microscopy-calibration.v1.schema.json`. Example (fictitious
value, labelled): `examples/calibration.SYNTHETIC-EXAMPLE.json`.

```json
{"schema": "anastylos-microscopy-calibration/1",
 "pixels_per_mm": 160.0, "pixels_per_mm_std": 1.5,
 "image_resolution": [1920, 1080], "optical_profile": "micro-05x",
 "frame": "custom:microscope", "view": "microscope_top",
 "provenance": "stage micrometer 1 mm/100 div, photo sha256 ..., operator V.",
 "measured_utc": "2026-11-02T10:00:00Z"}
```

Rules enforced by `calibration.resolve` (no mm output unless all hold):

* a non-empty `provenance` (what was measured, how, by whom);
* `image_resolution` equal to the inspected image's resolution;
* a scale from `pixels_per_mm`, `px_per_mm`, or `reference
  {length_mm, pixels}`; if two spellings are given they must agree within 1 %;
* 0 < px/mm <= 1000 (sanity range);
* optional `origin_frame_mm` [x0, y0]; for pcb-ocr it defaults to `[0, 0]` (image top-left) and the default is noted in the record.

An invalid calibration is recorded with its reasons and the inspection
proceeds in pixels. The Core never derives mm from pixel sizes, sensor specs
or the BlobCam FOV estimate.

## Per-reader spellings (preserved, not normalised in the readers)

* `pixels_per_mm`: polarity-reader, washer-spacer (`frame_metadata`),
  screw-measurement calibration file (written per inspection under
  `readers/screw_measure/` and loaded with the reader's own
  `load_calibration`).
* `px_per_mm`: pcb-ocr (`frame_meta`), component-fingerprints,
  connector-matching.

Each reader block records `calibration_adaptation.spelling` used.

## Measuring (operator procedure, not yet done)

1. Fix lens, zoom and capture resolution; note the optical profile.
2. Photograph a stage micrometer; measure a long span in pixels.
3. px/mm = pixels / mm; repeat >= 3 times for `pixels_per_mm_std`.
4. Store the photo and its sha256 in `provenance`. Re-measure after any zoom
   or lens change.
