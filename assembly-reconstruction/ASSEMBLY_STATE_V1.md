# Assembly State v1 — PROPOSED contract

Status: **proposed, additive only**. This document specifies a new
derived artifact. It does not modify `memory.jsonl`, detection
`result.json`, `assembly_graph.json`, or any other shared contract
(see [`ARCHITECTURE.md`](../ARCHITECTURE.md), the single status index).

## What it is

`assembly_state.json` is a deterministic, read-only projection of one
assembly-memory log plus the evidence files that log cites, optionally
joined with one read-only `assembly_graph.json`. It is regenerable from
its inputs and is **not a persistent memory system**: it stores no new
facts, invents no identities, and is never written back to the log.

Producer: `assembly-reconstruction/reconstruct.py build` (standard
library only, offline). One invocation writes `assembly_state.json` and
`exploded.svg` into its own `--out` directory.

## Top-level fields

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | Always `"assembly-state-v1"`. Consumers must reject anything else. |
| `projector` | object | `{name, version}` of the producing code. No wall-clock timestamps anywhere: identical inputs produce identical bytes. |
| `status` | string | `ok` / `ambiguous` / `unknown` / `unavailable` (same epistemic style as the recognizers). |
| `reason` | string\|null | Human-readable status reason. |
| `provenance` | object | Input paths plus SHA-256 over each input file's bytes (computed at read time, never from inside the files). |
| `coordinate_systems` | object | Declares the two disjoint systems below. |
| `components` | array | One entry per live (non-superseded) observation, in log order. Terminology: entries are observations of removal evidence, not verified parts — N entries are never N identified components. |
| `superseded` | array | Superseded observation ids (replaced, not drawn as live). |
| `relationships` | object | Graph edges copied verbatim, or an explicit unknown. |
| `missing_evidence` | array | Every unresolvable reference with its reason. |
| `warnings` | array | Non-fatal notes (cycles, contradictions, absent crops). |

## The two coordinate systems (never mixed)

- `physical`: frame millimetres stored **verbatim** from the memory log
  (`source_view`, `bbox_mm`, `centroid_mm`) and from the cited detection
  region. Origin top-left, +x right, +y down, per view frame. Never
  transformed, never reprojected. Elevated parts are paper-plane
  projections, exactly as the cited detection output states.
- `exploded`: layout-only numbers in `layout_px` for the SVG stack.
  They carry **no physical meaning** and are derived deterministically
  from timeline order (see "Exploded layout"). The renderer draws the
  two systems in separate panels.

## Component entry

```json
{
  "id": "obs-0003",
  "timeline_index": 2,
  "identity": {"status": "unknown", "label": null,
    "note": "memory records no part names; identity stays unknown until an operator asserts it elsewhere"},
  "physical": {
    "source_view": "surface_a3",
    "bbox_mm": [154.0, 81.0, 6.75, 6.25],
    "centroid_mm": [157.25, 83.84],
    "evidence": {"result_json": "…", "pair_id": "CD", "region": "R2", "result_sha256": "…"},
    "region_resolved": true,
    "region": {"bbox_mm": […], "centroid_mm": […], "bbox_px": […],
      "view": "surface_a3", "result_json": "…", "pair_id": "CD", "region": "R2",
      "result_sha256": "…", "result_sha256_current": "…",
      "provenance_verified": true, "provenance_note": "…",
      "rectified": {"px_per_mm": 4.0, "size_px": […], "origin_frame_mm": […]}},
    "missing_reason": null
  },
  "crops": [
    {"role": "before_rectified", "image": "before_rectified.jpg",
     "clip_px": [672, 380, 27, 25], "image_size_px": [1696, 1204],
     "note": "clip in rectified-image px; before/after are shown side by side, never interpreted"}
  ],
  "crop_unavailable": null,
  "tray": {"link_id": "link-0001",
    "tray": {"sheet_id": "surface_a3", "frame": "surface_a3", "number": 1, "rect_frame_mm": […]},
    "basis": {"kind": "human", "confirmed_by": "…", "note": null}},
  "exploded": {"x": 40.0, "y": 40.0, "w": 27.0, "h": 25.0,
    "units": "layout_px", "column": 0, "row": 2}
}
```

Rules:

- `identity.status` is always `"unknown"` and `label` always `null` in
  v1. The projector names no parts. (`ambiguous` is reserved for a
  future version fed by an explicit operator assertion file.)
- `tray` is `null` when the observation has no live tray link
  (`unassigned`). When several live links exist, all are listed under
  `trays` instead and the entry is flagged ambiguous — the projector
  never picks one.
- `crops` lists only image files that exist on disk at projection time,
  resolved as siblings of the cited `result.json` via its `output_files`
  names. `clip_px` is the region's `bbox_px` verbatim (rectified-image
  pixels). When nothing qualifies, `crops` is `[]` and
  `crop_unavailable` carries the reason. Before/after clips are always
  presented as an uninterrupted pair — the projector never states which
  one shows the component.
- `exploded` positions come only from the layout algorithm below.
- Provenance: the current SHA-256 of each cited `result.json` is
  recomputed and compared with the cited `result_sha256`. A mismatch
  rejects the region (`missing_reason`, plus a `missing_evidence`
  entry carrying both `cited_sha256` and `current_sha256`); no crops
  are attempted from rejected bytes. A region with no usable cited
  hash still resolves, but with `provenance_verified: false` — never
  as verified.

## Relationships (verbatim or unknown)

When `--graph assembly_graph.json` is given and readable, `confirmed`,
`candidates`, `contradictions`, `cycles`, `orderings`, `reassembly`,
`missing_confirmations`, and the graph `status`/`reason` are copied
verbatim with the file's SHA-256. `confirmed` edges are therefore
operator-confirmed by construction (the graph builder only confirms
from a confirmations file); `candidates` stay unconfirmed proposals.
Without a graph input, `relationships.source` is `null` and every edge
list is empty with `status: "unknown"`.

The projector never derives, reorders, or reinterprets edges.

## Exploded layout (deterministic)

Inputs: live components in log order. Constants: `SCALE = 4`
layout-px per mm, `COLUMN_ROWS = 10`, margins/gaps fixed in
`projector.py` (`LAYOUT`).

- `column = timeline_index // COLUMN_ROWS`,
  `row = timeline_index % COLUMN_ROWS`.
- Symbol size: `w = bbox_mm[2] * SCALE`, `h = bbox_mm[3] * SCALE`
  (physical width/height only; position is layout-only).
- `x = MARGIN_X + column * (COLUMN_WIDTH + COLUMN_GAP)`,
  `y = MARGIN_Y + sum of (preceding row heights + label band + gap)`
  within the column. Row heights depend only on the components in that
  column, in order — no randomness, no input-order dependence beyond the
  log order, no floating-point time source.

Two projections of the same inputs are byte-identical.

## SVG rendering

`exploded.svg` is a pure function of `assembly_state.json`:

- Left panel(s): physical map per `source_view` — verbatim mm boxes at
  a stated px/mm scale, tray cells for linked trays in the same frame,
  centroid dots, observation ids.
- Right panel: exploded stack at the `exploded` coordinates — one
  symbol per component with before/after clips where available (or an
  explicit "crop unavailable" placeholder), tray + basis badges,
  `identity: unknown` on every card, confirmed edges solid,
  candidate edges dashed, contradictions/cycles as text warnings.
- A provenance footer cites `schema_version`, input file SHA-256
  prefixes, and the layout-vs-physical disclaimer.

## What v1 never does

- Names, counts, merges, or tracks parts across observations.
- Promotes candidates to confirmed, or guesses at missing evidence.
- Transforms physical coordinates.
- Writes to the memory log, the graph input, or any evidence file.
