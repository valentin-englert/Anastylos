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

## 6. Minimum implementation built (2026-10-29)

Branch `feat/custody-v1.2` (worktree `Anastylos-custody-v12`): module
`anastylos_custody/proposals.py` (proposals 0.1.0) and command
`custody.py discover --video … --out …`, writing `registry.json`
(`custody-source-registry@1`: `SRC-<n>` with outline, bbox, centroid,
area, first change window, confidence, status `proposal (unconfirmed)`)
and `inventory.json` (basis `adaptive proposal`) for pass 2 with the
unchanged observer. Hands are recognised without colour: a change or
motion component that reaches outside the device mask is an occluder.
33/33 tests (5 new: arm never becomes a source, motionless arm stays an
occluder, judging by change not colour, split by time / merge by contact,
whole-device change ends the pose epoch).

E2 regression check — **not a scored experiment** (E2 is development
footage; no parameter was changed after this run). Outputs in
`C:\ANASTYLOS\data\proto-01\E2-adaptive\` (`registry.json`
`cbf7f1ce8b30456a96feec2e0588166e33e28cd173b88c02b7b59a7b152bfc37`,
`pass2\custody.json` `b88ff0eea3666c2dbeba82021a6b5c6fffa6fdb09ce98228d31db3c2b797284f`):

- Pass 1 found changes at all four wells, including W-RED, which the
  colour-based hand test had hidden in E2-D; W-YELLOW split into two
  sources (SRC-1, SRC-2) with the same time window, ~5 mm apart, not
  touching within the 1 mm merge gap. 5 sources for 4 parts, no source
  outside the wells.
- Pass 2: the duplicate makes the first deposit `ambiguous` (two removals
  pending); because the linker keeps unlinked removals pending, every
  later deposit is `ambiguous` too. 0 correct, 0 incorrect links.

Two findings for the next version, to be fixed generically and judged
only on E3-A, not on E2:

1. A part can fall apart into several change components (a well floor
   with ribs). Merge rule candidates: same pass-2 removal window plus
   spatial proximity, or one source per removal window with sub-masks.
2. The linker's pending set never clears an `ambiguous` removal, so one
   duplicate poisons every later link. Candidate: removals listed in an
   ambiguous proposal stay candidates only for that deposit's window
   unless re-observed (needs an owner decision under V3).

## 7. Rules revised (2026-10-29): linker 1.1 and proposals 0.2.0

Branch `feat/custody-v1.2`, commits `995243ef`, `e057cf4f`; 42/42 tests.

**When the linker treats a deposit as independent.** All deposits are
judged together. A *valid assignment* gives each deposit, in time order,
one removal that started before it and that no earlier deposit took; if
none is left, the part came from outside the watched sites. Each removal
serves at most one deposit. A deposit is linked
`candidate_temporal_exclusive` when only one removal is pending (as
before), or `candidate_by_elimination` when it is ambiguous locally but
every valid assignment gives it the same removal, because the other
pending removals are used up by other deposits in all of them. The
narrowed window is then judged again with every hidden-movement check
(overlapping deposits or removals, unobservable sources, sampling gaps,
occupied cell); any of these keeps it `ambiguous`. If valid assignments
disagree it stays `ambiguous`. V3 is unchanged. Tests: an initial
ambiguity does not poison an independent later link; two genuinely
competing sources stay ambiguous; a sampling gap or an unobservable
source blocks elimination; elimination never links a deposit that has no
removal.

**Clustering.** Proposals join one source when they changed in the same
observed interval (last matching sample within 0.25 s, windows
overlapping) and their mask gap is at most max(1 mm, 0.5 × √area of the
larger). Same time alone never merges distant parts, and touching parts
from different intervals stay separate. Each source keeps its submasks
and the joins (gap, limit) as provenance. Known limit: two neighbouring
parts removed in the same interval and closer than half a part size merge
into one source (the overhead view cannot separate them).

E2 development runs, retrospective and not independent:

- Named-inventory regression: linker 1.1 on the frozen v1.0 E2 events
  gives a byte-identical `custody.json` (`72c9d640…`). No regression.
- Adaptive (`C:\ANASTYLOS\data\proto-01\E2-adaptive-r2\`, registry
  `e6924ad5…`, `pass2\custody.json` `ec61919b…`): 4 sources for 4 parts
  (W-YELLOW from 3 submasks, gaps 0–1.33 mm against limits 2.65–2.98 mm),
  4 correct links (all `candidate_temporal_exclusive`), 0 incorrect, the
  C4 move `unattributed`. Improvement over proposals 0.1.0: from 0 to 4
  links. Regressions: none observed.

Implementation and thresholds are now frozen for E3-A
([`PROTO-01-E3A.md`](PROTO-01-E3A.md)).
