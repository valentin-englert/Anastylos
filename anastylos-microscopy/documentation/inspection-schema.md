# Inspection record (`anastylos-microscopy-inspection/1`)

Formal schema: `schemas/microscopy-inspection.v1.schema.json` (validated by
the built-in standard-library validator, `python -m anastylos_microscopy
validate --kind inspection FILE`).

| section | content |
|---|---|
| `inspection_id` | `mi-` + sha256(image sha, config sha, capture context, ingestion time)[:20] |
| `source` | origin (file/batch/capture_manifest), camera_id, node_id, optical_profile, capture_utc, manifest ref, validation_class |
| `image` | original path, file name, size, sha256, format, resolution, EXIF orientation, preserved copy |
| `orientation` | declared rotation (metadata only), `stored_pixels_modified: false` |
| `roi`, `quality` | validated ROI; advisory quality indicators and flags (`channel_clipped_fraction`, non-blocking `channel_clipping`) |
| `calibration` | status absent/valid/invalid, px/mm, provenance, reasons |
| `selection` | mode, readers chosen, readers skipped and why |
| `recognizers[]` | per reader: status (Core verdict), native status, code hash, pinned match, OCR backend, calibration adaptation, verbatim native result, artifacts; resistor: `core_assessment` (assessor, action none/kept/downgraded, checks with measurements, thresholds) |
| `observed_evidence[]` | what was seen (raw OCR text, bands, contours) |
| `interpretations[]` | decoded values per reader |
| `candidates[]` | hypotheses, always `confirmed: false` |
| `reconciliation` | agreements and conflicts across readers |
| `uncertainty` | overall_status, reasons, reader statuses, statement |
| `human_review` | pointer only; reviews live in `reviews/review-NNN.json` |
| `provenance` | Core and Anastylos identity, config sha, replay block, `assembly_memory_writes: false`, `blobcam_runtime_dependency: false` |
| `artifacts` | relative paths + sha256 of every written file |
| `run` | volatile timings/host, excluded from digest |
| `digest` | sha256 of canonical JSON of everything except `digest` and `run` |

Overall status rules, in order (`orchestrator.overall_status`):

1. intake rejected the file -> `input_error` (no reader runs);
2. every reader unavailable / errored / not applicable -> `unavailable` (if any
   was unavailable) else `error`;
3. any cross-reader conflict -> `conflict`;
4. any reader `ok` while a blocking quality flag is set -> `ambiguous`;
   (since v0.1.1, a native resistor `ok` that the Core evidence assessment
   does not support is held as reader status `ambiguous`; v0.1.2 also holds it
   when at least 10% of any single decoded band is channel-clipped at 250+)
5. any reader `ambiguous` -> `ambiguous`;
6. any reader `ok` -> `candidate` (still not a confirmation);
7. only enrollments/observations -> `recorded`;
8. otherwise -> `unknown` (abstention).

