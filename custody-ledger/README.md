# ANASTYLOS custody ledger (experimental)

Event-sourced part custody from one fixed overhead camera. It watches
**regions, not objects**: tray cells from the calibration kit and device
sites from an operator inventory. It records when each region was
*clean* (quiet, no hand over it) and what changed between clean states.
It then proposes removal → deposit links. Temporal exclusivity is a
conditional hypothesis, never identity, and every link needs operator
confirmation. Architecture source of truth stays in
[`ARCHITECTURE.md`](../ARCHITECTURE.md).

## What it is / is not

- Is: a read-only analyzer over a video. Its outputs go only to its own
  `--out` dir: `events.json`, `custody.json`, keyframe pairs, bindings.
- Is: a producer of change-detection *inputs*. Keyframe pairs are scored
  by the unchanged `change-detection/detect.py`, so Assembly Memory
  imports a normal `result.json` region.
- Is not: a memory writer, a confirmer, or an identity engine. It never
  calls a part "the same" part. Occluded or moving time is never read as
  "unchanged".
- Uses OpenCV + NumPy only, from the pinned `calibration/requirements.txt`.
  There are no new dependencies, and it is offline.

## Pipeline

1. **Sheet registration.** The first frame that passes the change-detection
   registration gate gives the millimetre homography.
   `DriftTracker` re-fits a marker-corner pixel homography about every 1 s,
   because a phone on a stand creeps (SNES-01: up to 8.8 px at 4K).
   Every fit's residual is recorded.
2. **Signals per sample** (5 fps, 3 px/mm rectified):
   - illumination-normalised gray;
   - motion;
   - "fresh skin": YCrCb skin that moved or appeared in the last 4 s.
     A still red button or a brown PCB stops counting as skin.
3. **Zone observers** (`observer.py`).
   - **Clean** means two things:
     - the zone's context window (zone + 20 mm) has been quiet for 0.6 s;
     - no hand covers the zone. A hand is a fresh-skin component that
       overlaps the zone's 3 mm guard box and also extends beyond it.
       A skin-coloured part lying inside the zone is not a hand.
   - **Events.** A content change of at least 8 mm², stable for 1 s while
     clean, becomes an event with `t_before` (last clean sample) and
     `t_after` (first clean sample showing the new state).
   - **Event kinds:**
     - cells: `appeared`, `changed_while_occupied` or `vacated`;
     - sites: `site_changed` or `site_restored`.
4. **Device pose** (`device_alignment.py`). The inventory's sites form one
   rigid device.
   - When a site's anchor ring (1–8 mm around it, other sites excluded)
     stops matching, the device is re-registered: ECC Euclidean,
     coarse-to-fine, with a SIFT + RANSAC seed.
   - The site's pose epoch ends (`pose_epoch_break`, site unknown from then
     on) if no fit passes the limits, or if the ring still fails afterwards.
5. **Custody linker** (`linker.py`). A deposit gets
   `candidate_temporal_exclusive` only when all of these hold:
   - exactly one removal is pending;
   - no other deposit or removal overlaps the custody window;
   - no source site was unobservable;
   - there are no sampling gaps;
   - the cell was empty.

   Otherwise the deposit is `ambiguous` (with reasons) or `unattributed`.
   Operator-declared removals (`custody-declared-removals@1`, `declared_by`
   required) are the documented human-assisted fallback. They are marked
   `basis: operator_declared`.
6. **Evidence** (`evidence.py`).
   - Exports full-resolution keyframe pairs (SHA-256 per image) for
     change-detection. For each side it picks the first candidate frame
     that passes the unchanged registration gate and records every attempt.
   - Binds the resulting regions back to their zones.

## CLI

From this directory, with the calibration venv:

```powershell
$py = '..\.venv\Scripts\python'
& $py custody.py observe --video <clip.mp4> [--inventory <sites.json>] --out <dir> [--trace <zone ids>]
& $py custody.py score   --events <dir>\events.json --gt <gt.json> --out <dir>\score.json
& $py custody.py link    --events <dir>\events.json [--declared-removals <declared.json>] --out <dir>\custody.json
& $py custody.py export-pairs --video <clip.mp4> --events <dir>\events.json --event-ids E-0029 [--declared-removals <f> --declared-search-s 1] --out <demo>
& $py ..\change-detection\detect.py run --input <demo>\pairs --out <demo>\results --view surface_a3 --manifest <demo>\pairs\manifest.csv --require-config-sha256 <sha>
& $py custody.py bind --results <demo>\results --keyframes <demo>\keyframes.json [--inventory <sites.json>] --out <demo>\bindings.json
```

Memory writes stay with `assembly-memory/memory.py` (`import --region`,
`record tray-link --confirmed-by`) and are run by an operator. They are
never run by this module.

## Schemas (v1, module-local)

- `custody-config@1`: frozen parameters. Its canonical SHA-256 is cited in
  every `events.json`.
- `custody-site-inventory@1`: sites as sheet-frame mm rectangles at
  `inventory_t_s`, plus who made the inventory (`basis`). Observer v1.1
  adds two optional fields: `initially: "occupied" | "empty"` per site and
  `tray_initially: {"cell-N": "occupied" | "empty"}`. Undeclared zones keep
  the v1.0 convention (cells start empty, sites start occupied).
- Event names (occupancy is always measured against the zone's first
  reference, and the name states what that means):

  | Zone, start state | away from start | back to start |
  |---|---|---|
  | cell, empty (default) | `appeared` | `vacated` |
  | cell, declared occupied | `vacated` | `appeared` |
  | site, occupied (default) | `site_changed` | `site_restored` |
  | site, declared empty | `site_filled` | `site_emptied` |

  Observer v1.2 adds `content.ring_change_with_verified_pose` (default
  `pose_break`, identical to v1.1). With `content`, a site whose anchor ring
  still differs after an accepted device re-registration adopts the ring
  and keeps observing (`anchor_ring_changed`) instead of ending its pose
  epoch: meant for dense anonymous site grids, where a neighbouring part
  lies inside the ring. `custody-config.grid.json` sets it.
  Observer v1.3 adds `content.cell_occupancy` (default `start_relative`,
  identical to v1.2). With `paper_model`, a tray cell is occupied when its
  non-paper area, after an opening of `cell_print_stroke_mm` that removes
  printed lines and digits, reaches `min_part_area_mm2`; event names then
  follow that measured occupancy (`appeared`, `vacated`,
  `changed_while_occupied`, `changed_while_empty`), and the zone summary
  reports `initially_measured` and any contradiction with a declaration.
  `custody-config.paper.json` sets it (3 mm, 25 mm², values taken from
  E2/E2R development footage). Limit: parts with little grey contrast
  against white paper (yellow, white) are near the threshold.
  A change between two states that both differ from the start is
  `changed_while_occupied` (empty-start cell) or
  `changed_while_away_from_start` (occupied-start cell).
- `custody-events@1`: the main event log, which also records:
  - provenance (video SHA-256, config and kit hashes, inventory hash,
    `observer_version` since 1.1.0);
  - registration, drift fits, sampling gaps;
  - per-zone clean/not-clean seconds and status;
  - device realignments and optional traces.
- `custody-links@1`: proposals, `removals_without_linked_deposit`, and
  `source_blind_spots`.
- `custody-declared-deposits@1` (linker 1.2): operator-declared deposits
  outside the watched cells, `{id, location, t_s, declared_by, evidence,
  source?: {site | removal_event}}`. Only a declaration that names its
  source with evidence closes that removal (for deposits observed after
  `t_s`); without a named source it closes nothing; a named source with no
  removal before `t_s`, a second closure of the same removal, or a closure
  of a removal an automatic link already used is rejected or flagged as a
  conflict. Links carry `autonomy` (`automatic` / `operator_assisted`) and
  `counts` keep them apart. Command: `custody.py link … --declared-deposits <file>`.
- `custody-declared-removals@1`, `custody-keyframes@1`,
  `custody-bindings@1`, `custody-gt/events@1` (for scoring).

## Assembly Memory contract: incompatibility and proposal (not implemented)

`assembly-memory` observations must carry finite `bbox_mm` / `centroid_mm`
and a `result.json` region (`store.py: validate_observation`). That is a
**sheet-plane** location. A device-frame site anchor cannot be represented
honestly:

- the device moves (SNES-01: 1.7 mm at 175 s, about 12 mm and 1° at 190 s),
  so a site's sheet-mm position is only valid for one pose epoch;
- a raised surface (the shell interior) is a paper-plane projection, not a
  position;
- there is no field for the pose epoch, the device warp, or the site id.

This module therefore does **not** write device-frame anchors. The demo uses
sheet-plane `result.json` regions, exactly as HW-02 did.

The smallest additive, versioned change I propose, for owner decision in
`ARCHITECTURE.md` before any consumer is built:

- a new optional observation field `device_anchor@1`:
  `{epoch_id, site_id, reference_frame_sha256, rect_reference_px, warp_reference_to_sheet}`;
- `bbox_mm` and `centroid_mm` stay mandatory, so existing readers keep working.

## Evaluation on genuine footage (SNES-01, dev set)

Inputs:

- video `C:\ANASTYLOS\incoming\20261027_172426.mp4`, SHA-256 `2e103bfc…b51bc9`;
- agent-annotated ground truth (not operator-sealed), written and hashed
  before the detector existed: `gt\tray_and_site_events.gt.json`
  `27e00923…`, `gt\site_inventory.json` `5fda6e7c…`.

Frozen run: `C:\ANASTYLOS\data\snes-01\custody-v1\final\`, with
`events.json` `22f81b19…` and config `10b58c2d…`.

| Check | Result |
|---|---|
| Camera drift compensation | 179/179 fits accepted, drift ≤ 8.84 px, residual ≤ 0.93 px |
| Sampling | 1064 samples, max interval 0.35 s, no gaps |
| U2 tray events, overlap rule (±2 s) | 13/15 certain events; 2 false positives (rocker overflow into cells 3 and 4); missed G-C03/G-C04 (merged into one 39 s cell-1 window) |
| U2 time resolution | Only 3/15 within ≤7 s (cells 1, 2, 3). The rest are 20–39 s windows: hands hovered over the tray, and the detector refused to call those cells clean. |
| U1 device sites | 0/5 detected, 0 false removals. D-pad: `inventory_not_observable` (never clean within ±2 s of the inventory). Red, yellow, green and blue: removed while hands covered them; the device was re-registered after a 1.7 mm nudge, but the next ~12 mm move was not → pose epoch ended → abstained. |
| U3 custody | 17/17 deposits `unattributed` (no observed removal). Correct given U1. |

**Dev-set iterations.** All of these were made on SNES-01 and are disclosed;
there is no holdout yet:

- drift compensation instead of stopping on drift;
- area instead of fraction for content changes;
- the hand-shape intrusion rule instead of the border-touch rule;
- a device-wide pose with a SIFT seed.

One variant, a paper-masked device alignment, created 2 false site removals
and was reverted.

## Limits

- Fixed camera and A3 marker sheet required. A handheld phone is not supported.
- SNES-01 shows the protocol gap: the device was hand-held or re-handled for
  every removal, and the screws went into one shared cell. Per-site removal
  is not observable there. That needs a resting device (or a jig) plus one
  part per cell, which is what the U1–U3 experiments in the plan specify.
- The change-detection gate rejects most full-marker SNES-01 frames
  (RMS 0.54–0.56 mm > 0.5 mm, the known marker-10 corner bias). Frames pass
  mostly when a hand hides a marker. This is a calibration/change-detection
  issue, outside this module. The gate was not changed.
- Skin colour is a heuristic. Red and yellow parts are skin-coloured, which
  the shape rule handles. Gloves would defeat it.
- Clean-state checks for sites use the device-aligned frame. Tray cells use
  the sheet frame.

## Tests

```powershell
..\.venv\Scripts\python -m unittest discover -s tests -v
```

20 deterministic synthetic tests (observer, linker, scoring, device
alignment). They involve no video and no network.
