# Frozen shared contract — `anastylos-observation-v1` / `anastylos-correspondence-v1`

Status: **FROZEN**. Muse B owns schema implementation and validation;
Muse A emits compliant observations. Any incompatible change requires
coordination before either lane proceeds. This is a minimal matching
contract, not a general event platform.

Scope: retrospective source-to-tray correspondence only. No ground-truth
labels, no operator annotations, no seeded source identities, no event
answers in runtime observations. No `VERIFIED` output from inference.
No Assembly Memory writes.

## Files

| Artifact | `schema_version` | Producer |
|---|---|---|
| Observation file | `anastylos-observation-v1` | Muse A (emits), Muse B (validates) |
| Sync manifest | `anastylos-sync-v1` | independent calibration/measurement |
| Correspondence output | `anastylos-correspondence-v1` | matcher (this lane) |

All three are UTF-8 JSON. Timestamps are milliseconds. Camera-local
timestamps use actual PTS values (`pts_ms`); the matcher never invents
cross-camera order.

## Observation file

```json
{
  "schema_version": "anastylos-observation-v1",
  "session_ref": "observer session label, free text",
  "events": [ { "event": "..." } ]
}
```

`session_ref` is required (may be `""` when unknown). `events` is a
non-empty array, at most 50,000 entries. Event ids must be unique across
all observation files merged into one matcher run.

## Event (all kinds)

Every event carries exactly these fields:

| Field | Type | Rule |
|---|---|---|
| `schema_version` | string | must equal `anastylos-observation-v1` |
| `event_id` | string | non-empty, unique per run |
| `kind` | string | `tool_interaction`, `source_state`, `tray_state`, `visibility_gap`, `pose_change` |
| `status` | string | `observed` or `inferred` |
| `time` | object | `clock_id` (non-empty string), `start_ms`, `end_ms` (numbers ≥ 0, `start_ms` ≤ `end_ms`) |
| `location` | null or object | camera-specific display pixels only (below) |
| `state` | null or object | kind-specific (below); `null` means unusable, never implicit |
| `evidence` | object | camera/frame/PTS/source references (below) |
| `limitations` | string array | may be empty; known caveats for this event |

`status: inferred` marks reasoning output (e.g. an inferred removal),
not direct observation. Inferred events can explain a chain but can
never alone support a `PROPOSED` correspondence: the deposit, the
source-present and the source-absent evidence must all be `observed`.

### `location` (nullable)

```json
"location": {
  "camera_id": "cam-a",
  "xy_px": [412.5, 208.0],
  "uncertainty_px": 6.0,
  "pose_epoch": "epoch-1",
  "frame": "display"
}
```

- `camera_id`: non-empty string. Coordinates are valid only for this camera.
- `xy_px`: `null` or `[x, y]` finite numbers (display pixels,
  origin top-left, +x right, +y down).
- `uncertainty_px`: `null` or number ≥ 0.
- `pose_epoch`: `null` or non-empty string. Opaque device-pose label
  assigned by the observer. `null` means unstated, not stable.
- `frame`: `null` or the string `display`. Only display pixels exist
  in v1. Never equate pixel coordinates across cameras: there is no
  calibrated cross-camera transform in this contract version.

`location: null` means no position is claimed.

### `evidence`

```json
"evidence": {
  "camera_id": "cam-a",
  "frame_index": 812,
  "pts_ms": 27012.5,
  "source_ref": "clip-a.mp4",
  "source_sha256": null
}
```

- `camera_id`: `null` or non-empty string (original camera).
- `frame_index`: `null` or integer ≥ 0 (decoded frame in that camera).
- `pts_ms`: `null` or number ≥ 0 (actual PTS; must lie inside the
  event's `time` interval when both are stated).
- `source_ref`: required non-empty string (file, clip, or review
  session identifier). Never empty: no anonymous evidence.
- `source_sha256`: `null` or 64 lowercase hex. Stated when the source
  is a hashable file; `null` otherwise (e.g. live review session).
  Hashes are cited provenance; the matcher verifies the observation
  files it reads, not externally referenced media.

### `state` by kind

`tool_interaction` — timestamped tool/hand candidate. Contact alone
never proves removal; the matcher enforces this.

```json
"state": {
  "actor": "hand:left",
  "action": "grasp",
  "target_ref": "site-3",
  "contact_verified": true,
  "handoff_from": null,
  "handoff_to": null
}
```

- `actor`: non-empty string (`hand:left`, `hand:right`,
  `tool:screwdriver-1`, ...). Opaque; compared by equality only.
- `action`: `contact`, `torque`, `grasp`, `release`,
  `pickup_candidate`, `handoff`, `carry`, `unknown`.
- `target_ref`: `null` or non-empty string (observer's alleged site;
  `null` = location unattributed).
- `contact_verified`: boolean.
- `handoff_from` / `handoff_to`: required non-null strings when
  `action` is `handoff`, otherwise `null`.

`source_state` — presence/absence of a part at a site. `site_ref` is
the observer's track label for one physical site, not a ground-truth
identity. `loosened` is never removal.

```json
"state": {
  "site_ref": "site-3",
  "presence": "present",
  "verified": true,
  "appearance": {
    "component_class": "screw",
    "head_drive": "phillips",
    "length_mm": null,
    "diameter_mm": null,
    "marks": null
  }
}
```

- `presence`: `present`, `absent`, `loosened`, `unknown`, `occluded`.
- `verified`: boolean. Only `observed` + `verified` present/absent
  states can support a proposal.
- `appearance`: `null` or object with the nullable fields above
  (`length_mm` / `diameter_mm` numbers > 0 when stated).
  Appearance is compared only when stated on both sides; unstated
  fields are neutral, never assumed.

`tray_state` — tray change candidate. Only a supported, observed
addition triggers retrospective matching.

```json
"state": {
  "tray_ref": "tray-1",
  "cell_ref": "A3",
  "change": "addition",
  "supported": true,
  "appearance": null
}
```

- `change`: `addition`, `removal`, `unchanged`, `unknown`.
- `supported`: boolean. `false` = weak/uncertain candidate; the
  matcher answers `UNKNOWN` and does not propose from it.
- `cell_ref`: `null` or non-empty string.
- `appearance`: same shape as `source_state`.

`visibility_gap` — declared blindness (occlusion, missing frames,
out-of-frame). Gaps are cited against proposals, never silently
bridged.

```json
"state": {
  "scope": "source",
  "scope_ref": "site-3",
  "reason": "occlusion"
}
```

- `scope`: `source`, `tray`, `camera`, `global`.
- `scope_ref`: site/tray/camera identifier, or `null` for `global`.
- `reason`: `occlusion`, `missing_frames`, `out_of_frame`,
  `blur`, `unknown`.

`pose_change` — device rotation / pose epoch change, plus explicit
cross-epoch site tracking. Same `site_ref` across epochs is not
trusted unless linked here.

```json
"state": {
  "device_ref": "unit-1",
  "from_epoch": "epoch-1",
  "to_epoch": "epoch-2",
  "site_links": [
    {"site_ref": "site-3", "from_epoch": "epoch-1", "to_epoch": "epoch-2"}
  ]
}
```

- `site_links`: array (may be empty). Each entry asserts the observer
  tracked one physical site across the stated epochs.

## Sync manifest (`anastylos-sync-v1`)

Cross-clock temporal comparisons require a validated entry. No
manifest, no validated entry, no inference: ordering stays unknown.

```json
{
  "schema_version": "anastylos-sync-v1",
  "offsets": [
    {
      "from_clock": "cam-a",
      "to_clock": "cam-b",
      "offset_min_ms": 118.0,
      "offset_max_ms": 124.0,
      "validated": true,
      "basis_ref": "clap-sync measurement, session notes p2"
    }
  ]
}
```

Semantics: `t_to = t_from + d` with `d` in
`[offset_min_ms, offset_max_ms]`. Direction and uncertainty are
explicit. Only `validated: true` entries are usable. No transitive
chains in v1: a comparison needs a direct validated entry in either
direction (the matcher inverts reversed entries). Same-`clock_id`
comparisons need no manifest.

## Correspondence output (`anastylos-correspondence-v1`)

One decision per tray addition, deposit-triggered. Decisions:
`PROPOSED` (one surviving candidate, still requires operator
confirmation), `AMBIGUOUS` (several viable), `UNKNOWN` (insufficient
evidence). Per-hypothesis verdicts add `REJECTED` (contradicted by
evidence). `VERIFIED` never appears; the matcher never writes to
Assembly Memory.

```json
{
  "schema_version": "anastylos-correspondence-v1",
  "inputs": [{"path": "obs.json", "sha256": "..."}],
  "sync": {"manifest_sha256": null, "validated_pairs": []},
  "engine": {"name": "retrospective-custody-matcher", "version": "1.0.0"},
  "counts": {"deposits": 1, "proposed": 0, "ambiguous": 0, "unknown": 1},
  "deposits": [
    {
      "deposit_event_id": "tray-add-1",
      "decision": "UNKNOWN",
      "proposed_site_ref": null,
      "ambiguity_set": [],
      "reasons": ["SOURCE_ABSENCE_UNVERIFIED"],
      "cited_event_ids": ["tray-add-1"],
      "requires_confirmation": true,
      "superseded_by": null,
      "custody_chain": [],
      "hypotheses": [
        {
          "site_ref": "site-3",
          "verdict": "INSUFFICIENT",
          "reasons": ["SOURCE_ABSENCE_UNVERIFIED"],
          "cited_event_ids": ["src-present-1", "tray-add-1"],
          "missing_evidence": ["verified absent observation for site-3"]
        }
      ],
      "unresolved_alternatives": ["site-3: absence never verified"]
    }
  ],
  "unusable_events": [],
  "limitations": ["..."]
}
```

Reason codes are stable strings (see matcher README for the list).
`requires_confirmation` is always `true`: a proposal is a proposal.

## Compatibility

Backward-incompatible changes (renamed fields, new required fields,
changed semantics) require a new `schema_version` value and lane
coordination. Additive optional fields are allowed without a version
bump but must be ignored by consumers that do not understand them.
Unknown event kinds are rejected by validation: v1 has exactly five.
