# ANASTYLOS CAPTURE — data model, schema version 1

This document is the contract between the PWA, the export package and the
Python validator. All three implement it independently; the round-trip test
(`app/tests/roundtrip.test.ts` plus `python/tests/test_validate.py`) proves
they agree.

Canonical field definitions live in `app/src/lib/schema.ts`
(TS, with `validate*` functions) and are mirrored in
`python/anastylos_capture_validate.py` (Python, same rules, same messages
where practical).

## Conventions

- `schema_version` is `1` on every record and manifest. Readers refuse any
  other version instead of guessing.
- Identifiers (`session_id`, `observation_id`, `image_id`, `package_id`)
  are UUIDs (128-bit, collision-resistant), lowercase hex.
- Timestamps (`created_at`, `captured_at`, `exported_at`, …) are ISO 8601
  UTC with a `Z` suffix, e.g. `2026-01-15T10:30:00.000Z`.
- Optional text is `null` when absent, never an empty string.
- Unknowns stay `null` with an explicit `unknown` state. Nothing invents
  values for missing information.

## Field states

Every interpreted piece of information carries its epistemic state:

| State | Meaning | May become identity? |
|---|---|---|
| `observed` | Seen by the camera or read by the operator (photos, markings) | No |
| `interpreted` | Derived locally without a model (photo counts, previews) | No |
| `suggested` | Proposed by a future identification provider, unreviewed | No |
| `confirmed` | Explicitly asserted by the operator | Only via review |
| `unknown` | Not known; the value MUST be `null` | No |

`ConfirmedField<T>` is `{ value: T | null, state: 'confirmed' | 'unknown' }`:
a `confirmed` field always has a value, an `unknown` field never does.
Identification candidates are always `suggested`; promoting one to
`confirmed` category/quantity is an explicit operator act in the inspector.

## Records

### CaptureSession

Local grouping of observations on the phone. Optionally names a
previously known Assembly Memory session id as plain text (no fetching,
no linking, no creation of Core records).

```
schema_version, session_id, name, created_at, updated_at,
assembly_memory_session: string | null,
tray_default: string | null
```

### ComponentObservation

One removed component and everything the operator recorded about it.

```
schema_version, observation_id, session_id,
component_reference: string | null,   # operator-entered, optional
tray_reference: string | null,        # manual tray id, never auto-linked
captured_at, capture_source,          # camera | file | gallery
review_state,                         # unreviewed | reviewed | confirmed
operator_notes: string | null,
markings: ObservedMarking[],          # { text, state: 'observed' }
category: ConfirmedField<string>,     # operator-confirmed class or unknown
measurements: Measurement[],          # { label, value, unit, provenance }
quantity: ConfirmedField<number>,     # positive int when confirmed
candidates: IdentificationCandidate[],# suggested only, [] in v1
provenance: { app, app_version, user_agent },
export_history: [{ package_id, exported_at, via? }]   # via is 'sync' for workstation-confirmed transfers, absent for local file exports
```

Rules: `quantity` is NEVER inferred from the photo count. `measurements`
require an explicit `unit` and `provenance`
(`operator-measured` | `operator-estimated` | `unknown`). `via` is advisory
transport metadata only: validators check that `export_history` is an array
and ignore the marker, so packages with and without it validate identically.

### EvidenceImage

Metadata for one stored photograph. The original bytes live beside it in
the `binaries` store under `orig/<image_id>` (plus an optional derived
preview under `prev/<image_id>`).

```
schema_version, image_id, observation_id, role,  # front | back | detail | other
filename,                       # sanitized, unique within the observation
mime,                           # jpeg | png | webp | heic | heif
byte_length,                    # > 0, at most 25 MiB
sha256,                         # lowercase hex over the ORIGINAL bytes
captured_at,
crop: { x, y, width, height } | null   # fractions 0..1, preview only
```

Original bytes are immutable: `updateImage` refuses any change to `sha256`
or `byte_length`. Crops are preview rectangles only; exports always ship
the untouched originals.

## Export package

A ZIP archive (stored entries, no compression — valid ZIP, readable by
Python `zipfile`, Explorer, Archive Utility and `unzip`):

```
anastylos-capture-<id8>.zip
├── manifest.json          # schema_version, package_id, exported_at,
│                          # generator, sessions[], observations[] (+images[]),
│                          # files[]
├── assets/<observation_id>/<filename>…     # originals, byte-identical
├── previews/<observation_id>/<image_id>.jpg…  # optional derived JPEGs
└── annotations.json       # optional, only when crops exist: { crops: … }
```

`manifest.json → files[]` entries carry `path`, `kind`
(`original` | `preview`), `observation_id`, `image_id`, `sha256`, `bytes`
and `mime`. Export re-hashes every original and refuses to ship evidence
whose bytes no longer match.

The Python validator additionally requires: no duplicate ids, no unsafe
ZIP paths (absolute, drive-letter, backslash, dot-dot, control chars), no
unlisted members, size caps (package 512 MiB, manifest 5 MiB, member
100 MiB, 5000 entries), and per-observation acceptance only when structure
is valid and every referenced file verifies.

## BOM draft CSV

`bomRowsToCsv` exports **confirmed-review observations only** with columns:

```
session_name, session_id, observation_id, captured_at,
component_reference, category, category_state, quantity, quantity_state,
markings, tray_reference, review_state, photo_count, photo_sha256
```

`photo_count` is labeled as what it is; quantities come solely from the
operator-confirmed field. No part numbers, ratings or suppliers are ever
invented. (RFC 4180 escaping, CRLF line endings.)

## Provider-neutral identification (future)

`app/src/lib/identify.ts` declares the interface without implementing any
provider:

- `IdentificationRequest`: `observation_id`, image refs
  (`image_id`, `sha256`, `role`), optional operator context.
- `IdentificationResult`: `provider`, `model`, `requested_at`,
  `candidates[]`, provenance, `review_status: 'pending-review'`.
- `IdentificationProvider`: `{ providerId, modelId, identify(request) }`.

The registry is empty; the UI labels identification as future
functionality and no photo leaves the device.
