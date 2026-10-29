# P2 — adaptive spatial discovery: architectural assessment

Owner requirement 2026-10-29: the fixed grid of E2-D is an experimental
baseline and an implementation detail, never the product's spatial model.
Status: assessment only, nothing implemented beyond the E2-D grid
(`tools/discovery/discover.py`) and observer v1.2's ring rule.

Evidence so far, kept as recorded: E2-D run 1 FAIL (ring interference),
run 2 PASS retrospective with observer v1.2 (3 of 4 sources, W-RED missed
because a skin-coloured part was read as a hand). See
[`PROTO-01-E2D.md`](PROTO-01-E2D.md).

## 1. Can it replace the grid incrementally?

Yes. The custody pipeline already separates *where to look* (an inventory
of sites) from *what happened there* (zone observers, linker, memory). An
adaptive layer only has to produce better sites; observer, linker,
change-detection evidence and Assembly Memory stay. The grid becomes one
proposal source among others.

## 2. Minimum viable implementation (offline, two passes)

1. **Proposal pass.** On clean samples, compare the device in its
   reference pose (existing `DevicePose` alignment) with the hand-free
   reference; keep connected components of change that persist for the
   confirm time while not occluded. Each is a *proposal*: mask, bounding
   box and centroid in device-relative mm, time window, contrast/area
   confidence, provenance (frames, video hash, parameters). Grid sampling
   may still seed candidates, but identity comes from the change mask.
2. **Merge / split.** Merge proposals whose masks overlap or touch and
   whose time windows overlap (one persistent change). Split a region only
   when its parts change in demonstrably different time windows. Anything
   in between stays `ambiguous` with both candidates kept.
3. **Source registry.** A proposal confirmed by a source-state change gets
   a stable id `SRC-<n>`, its geometry (mask + device-relative
   coordinates + pose epoch) and confidence. The registry is append-only:
   refined geometry is a new version with `supersedes`; earlier events keep
   pointing at the version they used.
4. **Observation pass.** The registry becomes an inventory (basis
   `adaptive proposal`, one entry per source version) and the existing
   observer re-reads the take from the start, so every source has a
   before-state. Linker and change-detection evidence run unchanged.

Live (single-pass) discovery comes later; the two-pass form is honest
about needing the whole take and fits the "processing pause" the demo may
show.

## 3. Interfaces

| To | Change needed | Kind |
|---|---|---|
| Observer | site entries with an optional mask and per-site `valid_from_s` (today one `inventory_t_s` for all) | additive inventory fields |
| Observer | `ZoneGeometry.content` as mask instead of rectangle | additive, rectangle stays default |
| Linker | none: it already links any `site_changed` to deposits | — |
| Assembly Memory | optional `source_ref {id, version, device_frame, confidence}` on an observation; unconfirmed proposals never enter memory as confirmed | additive field, existing confirmation rules |
| Guide (Muse) | package lists `SRC-<n>` with geometry and confidence instead of inventory labels; unknown labels stay anonymous | already supported (anonymous sources) |

## 4. Frozen assumptions that remain

- Fixed overhead camera and a registered calibration sheet (mm frame).
- The device rests between interactions; movement starts a new pose
  epoch (P3 tracking is a separate step).
- Planar view: hidden or stacked parts are invisible; no 3D.
- Parts contrast with what lies beneath them and exceed the minimum change
  area (8 mm² today).
- Hands leave the zone so that a clean state exists; hand detection is
  still colour-based until H1 is implemented.
- One part per deposit cell, and cell geometry from the mat.

## 5. Experiment that separates discovery from fitting E2

**E3-A, sealed new arrangement, parameters frozen before recording.**

- Object: not the SNES front shell — an unfamiliar simple device or a
  rigid board carrying objects of very different size and shape (for
  example a 3–4 mm screw, a coin, an irregular plastic clip, a 40 mm lid).
- Owner rolls and seals source positions, order, cells and controls; the
  method sees no inventory and no part count.
- Built-in challenges: two neighbouring parts removed in separate windows
  (split), one large irregular part (merge), one part moved within the
  device but not removed (no removal may be claimed), one skin-coloured
  part (expected failure until H1, reported), one deliberate device shift
  mid-take (must give a new pose epoch or `unknown`, never a wrong link).
- Scored on source recall and precision, link correctness and correct
  use of `ambiguous` / `unknown`, with the PASS / FAIL logic of E2-D.

No parameter of the method may be tuned on E2 any further; E2 stays a
regression check only.
