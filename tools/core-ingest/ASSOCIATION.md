# Assembly Memory association — gap and proposed extension

**Status:** Core Capture Evidence Index is complete for MVP lookup.
Assembly Memory is **not** written by this worker. Owner approval is
required before any additive memory contract change.

## Why not write `observation` events today

Frozen Assembly Memory `observation` events require:

- `source_view`
- `bbox_mm` (4 numbers)
- `centroid_mm` (2 numbers)
- evidence reference to a change-detection `result.json` (`result_json`,
  `pair_id`, `region`, plus file SHA-256)

A Guided Capture / sampling JPEG is evidence of a **scene**. It does not
supply removal geometry or a `result.json`. Inventing mm boxes, part
identities, tray cells, operator confirmations, or Core request IDs that
were absent from the capture would violate evidence guarantees.

Capture packages may carry `assembly_memory_session` as optional plain
text on the capture session record. That field is **not** a live link
and is not treated as authorization to mutate `memory.jsonl`.

## What Core consumers can do now

1. Ingest via `tools/core-ingest` into `--core-dir`.
2. Look up by capture session id, optional `--associate-session` Core
   session id, or `observation_id`.
3. Read original JPEG bytes by SHA-256 (`verify-blob` / blob store).
4. Treat records as `capture_scene_evidence` — staged, not confirmed.

## Smallest additive contract (proposal — not implemented)

Owner-review candidate only. Do not ship without approval.

```text
event type: capture_evidence_ref
fields:
  id, session, timestamp
  capture_session_id   # UUID from the capture package
  observation_id       # capture observation UUID
  image_id             # capture image UUID
  image_sha256         # lowercase hex of original bytes
  byte_length
  package_id
  captured_at          # from package, may be null
  association_basis    # "operator_provided" | "capture_session"
  note?                # free text; no identity keys
forbidden:
  part / part_name / part_id / name
  bbox_mm / centroid_mm invented without cited calibration evidence
  tray_link / confirmed review promotion
```

Semantics: append-only pointer from an Assembly Memory **session** to
already-stored Core capture evidence. It asserts that the photo exists
and is associated; it does **not** assert a removal. Corrections would
append a superseding event, matching existing memory rules.

Until approved, keep association in the Core evidence index only.
