# ANASTYLOS Observation Contract v1 (WITHDRAWN)

Status: WITHDRAWN 2026-10-28 — superseded pending owner decision.
A second document claims the same schema id `anastylos-observation-v1`:
`tools/retrospective-custody/matcher/SCHEMA.md` (Muse B, implemented in
`validate.py` + tests). The two are mutually incompatible (verified by
executing Muse B's validator over this document's §9 example: rejected
with `session_ref must be a string`, then `location.camera_id must be
a non-empty string`).

Until the owner decides: DO NOT produce or consume observation files in
this format. The proposed canonical contract is `matcher/SCHEMA.md`;
this document's portable strengths (evidence frames with roles,
basis_event_ids twin links, mandatory file hashes) are offered as
additive optional fields. No observation files, no matcher-contract
changes, no ARCHITECTURE.md edits until the decision lands.

Original freeze note (historic): FROZEN 2026-10-28 by Muse A
(SNES-02 observability lane).
Schema id: `anastylos-observation-v1`.
Matcher output id: `anastylos-correspondence-v1` (consuming lane).

This is a minimal freeze. Any incompatible change requires coordination
between lanes before either proceeds. Do not expand into a general
event platform: no new kinds, statuses, or clocks without a version bump
and joint agreement.

Lane ownership: Muse B owns schema implementation and validation
(code + machine checks). Muse A emits compliant observations.
This document is the normative field contract both lanes implement.

## 1. Dataset envelope

A runtime observation file is one JSON object:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | string | yes | Exact value `anastylos-observation-v1`. |
| `dataset_id` | string | yes | Unique dataset id, e.g. `snes02-obs-20261028`. |
| `sync_manifest_ref` | object \| null | yes | `{path, sha256}` of the independently validated synchronization manifest, or `null` when the dataset contains a single camera clock. |
| `events` | array of event | yes | Observation events (section 2). May be empty. |

## 2. Event envelope

All keys below are REQUIRED on every event. No extra top-level keys
in v1 (consumers must reject or ignore unknowns per Muse B's validator;
producers must not emit them).

| Key | Type | Meaning |
|---|---|---|
| `schema_version` | string | Exact value `anastylos-observation-v1`. |
| `event_id` | string | Unique within the dataset, e.g. `snes02-obs-001`. |
| `kind` | enum | One of `tool_interaction`, `source_state`, `tray_state`, `visibility_gap`, `pose_change`. Closed set in v1. |
| `status` | enum | `observed` (directly supported by cited evidence frames) or `inferred` (derived from other observations; MUST cite basis). |
| `time` | object | Section 3. |
| `location` | object \| null | Section 4. `null` when not localizable; reason goes in `limitations`. |
| `state` | object \| null | Kind-specific payload (section 5). |
| `evidence` | object | Section 6. |
| `limitations` | string array | Known limits, occlusions, alternative explanations. MUST be present; SHOULD explain every `unknown` / `null` in the event. |

Consistency rules:

- `observed` events MUST cite at least one frame in `evidence.frames`.
- `inferred` events MUST cite at least one basis id in
  `evidence.basis_event_ids` and MUST state the inference in
  `limitations` (what was assumed beyond the basis).
- `event_id` values referenced in `basis_event_ids` MUST exist in the
  same dataset.

## 3. Time (camera-local PTS only in v1)

`time = { clock_id, start_ms, end_ms }`, all required.

- `clock_id`: `cam1-pts` or `cam2-pts`. Closed set in v1. There is no
  common-clock timestamp in v1: cross-camera temporal comparison is the
  consumer's job and REQUIRES the independently validated
  synchronization manifest (offset direction, magnitude, uncertainty,
  drift bounds) referenced by `sync_manifest_ref`.
- `start_ms`, `end_ms`: integers, `start_ms <= end_ms`, derived from
  decoder PTS as `round(pts_seconds * 1000)`. Millisecond rounding
  never replaces evidence: exact frame indices and raw PTS ride in
  `evidence.frames`.
- A single-instant observation uses `start_ms == end_ms`.

## 4. Location (camera-specific display pixels, nullable)

`location = null` or `{ camera, x, y, uncertainty_px, pose_epoch, frame }`,
all sub-keys required when non-null.

- `camera`: `CAM1` or `CAM2` — the camera whose display pixels the
  coordinates are measured in.
- `x`, `y`: numbers. Decoded display pixels (decoder-applied rotation
  included; SNES-02 coded frames are landscape with a -90° display
  matrix, so display frames are portrait — CAM1 720×1280, CAM2
  1080×1920 — see sync manifest), origin top-left, +x right, +y down.
  A point estimate of the candidate physical location (e.g. fastening
  site, tray cell).
- `uncertainty_px`: non-negative number. Radius in the same display
  pixels inside which the true location is believed to lie.
- `pose_epoch`: string identifying the camera-pose interval the
  coordinates belong to (e.g. `cam1-full` when the camera never moves;
  a `pose_change` event opens a new epoch). Coordinates from different
  epochs MUST NOT be compared as pixels.
- `frame`: integer frame index (decoder order, 0-based) on which the
  point was measured.
- No millimetres in v1 (no calibration is cited for SNES footage).
- NEVER equate pixel coordinates across cameras without a calibrated
  transformation. No such transformation exists in v1.

## 5. Kind-specific `state` payloads

`state` is `null` only where the table allows it.

- `tool_interaction`: `{ interaction, tip_visible, contact }`
  (all required).
  - `interaction`: `engagement` (tool at a fastening site, turning
    unverified) | `loosening` (rotation/unscrewing motion seen) |
    `lift` (tool lifts an object clear) | `touch` (contact without
    fastening action) | `reposition` (tool/object moved, no fastening
    action) | `unknown`.
  - `tip_visible`: `true` | `false` (working end of the tool resolved
    in cited frames).
  - `contact`: `observed` (physical tool-to-site contact seen in cited
    frames) | `unknown`. There is no `inferred` contact value: a mask
    endpoint near a site is `unknown` contact, not contact.
- `source_state`: `{ presence }` (required).
  - `presence`: `present` | `absent` | `unknown`, judged at the cited
    location/time. Before/after linkage rides on `time` ordering plus
    `evidence.frames` roles (`before` / `after`) and `basis_event_ids`.
- `tray_state`: `{ delta }` (required).
  - `delta`: `added` | `removed` | `unchanged` | `unknown`, judged
    over `[start_ms, end_ms]` at the cited location.
- `visibility_gap`: `{ cause }` (required).
  - `cause`: `hand` | `tool` | `object` | `out_of_frame` | `unknown`.
- `pose_change`: `{ kind }` (required).
  - `kind`: `moved` | `bumped` | `unknown`. Opens a new `pose_epoch`.

## 6. Evidence references

`evidence = { camera, source_sha256, frames, basis_event_ids }`,
all required.

- `camera`: `CAM1` or `CAM2` — the camera this event is observed in.
  One event cites one camera. The same physical occurrence seen in
  both cameras is TWO events linked by `basis_event_ids`, never one
  event with two pixel frames.
- `source_sha256`: lowercase hex SHA-256 of the exact original video
  file bytes the frames were decoded from.
- `frames`: array of `{ frame, pts_seconds, role }` (all required per
  entry). `frame`: 0-based decoder frame index. `pts_seconds`: raw
  decoder PTS in seconds. `role`: `primary` | `before` | `after` |
  `context`. Non-empty for `observed` events.
- `basis_event_ids`: string array of same-dataset `event_id` values
  this event derives from or pairs with. Non-empty for `inferred`
  events.

## 7. Separation rules (normative)

- Ground-truth labels and operator annotations live in separate files
  with separate schema ids. They MUST NOT appear in, or be merged
  into, runtime observation files.
- No seeded source identities or event answers: `event_id`,
  `location`, and `time` MUST be produced from evidence available to
  the runtime lane, never copied from GT or evaluator judgments.
- Evaluator judgments (correct/incorrect, scoring) are never fields of
  observation events.

## 8. Matcher output: `anastylos-correspondence-v1` (minimal freeze)

The retrospective matcher consumes `anastylos-observation-v1` datasets
and emits `anastylos-correspondence-v1` records. Minimal record:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | string | yes | Exact value `anastylos-correspondence-v1`. |
| `correspondence_id` | string | yes | Unique within the output. |
| `verdict` | enum | yes | `PROPOSED` \| `AMBIGUOUS` \| `UNKNOWN` \| `REJECTED`. Closed set. No `VERIFIED` value exists in v1: only an operator confirms. |
| `observation_refs` | string array | yes | Referenced observation `event_id`s. Non-empty. |
| `evidence` | object | yes | Supporting evidence refs (frames, manifest refs). |
| `alternatives` | string array | yes | Unresolved alternative explanations. |
| `limitations` | string array | yes | Known limits of the correspondence claim. |

Matcher rules: no automatic `VERIFIED` status exists; the matcher
MUST NOT write to Assembly Memory (no `memory.jsonl` appends);
`REJECTED` MUST state the disqualifying evidence in `limitations`.

## 9. Worked minimal example (illustrative, not evidence)

```json
{
  "schema_version": "anastylos-observation-v1",
  "event_id": "example-001",
  "kind": "tool_interaction",
  "status": "observed",
  "time": {"clock_id": "cam1-pts", "start_ms": 12000, "end_ms": 15500},
  "location": {"camera": "CAM1", "x": 640.0, "y": 360.0,
    "uncertainty_px": 25.0, "pose_epoch": "cam1-full", "frame": 360},
  "state": {"interaction": "engagement", "tip_visible": true,
    "contact": "unknown"},
  "evidence": {"camera": "CAM1", "source_sha256": "<hex>",
    "frames": [{"frame": 360, "pts_seconds": 12.0, "role": "primary"}],
    "basis_event_ids": []},
  "limitations": ["Tip resolved but contact occluded by hand."]
}
```

## 10. Freeze record

- Frozen: 2026-10-28. Frozen by: Muse A.
- Change rule: any incompatible change (new/removed/renamed key,
  kind, enum value, clock, or rule) requires explicit coordination
  between lanes BEFORE either lane proceeds, and a version bump to a
  new schema id. Backward-compatible clarifications (wording, examples)
  require notice, not re-freeze.
