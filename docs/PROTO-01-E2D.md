# PROTO-01-E2D — automatic source-site discovery on existing E2 footage (preregistered)

Status: preregistered 2026-10-29 before the discovery method was run on
E2. Owner decision 2026-10-29 (P2). Retrospective: E2 footage and its
sealed ground truth already exist, so this tests the method, not a new
capture; a pass only justifies an independent new recording.

## 1. Question

Can source sites be found without any operator-named or hand-placed
inventory, so that the unchanged custody linking still yields correct
source-to-deposit links on E2?

## 2. Inputs the method may use

- `C:\ANASTYLOS\incoming\E2.mp4` (`bfc1bb49…`) and the sheet registration
  (generic calibration, same for every device).
- Generic parameters fixed here: device mask = working-field pixels that
  are not paper-white (HSV: not (V ≥ 150 and S ≤ 40)), largest connected
  component after a 3 mm closing; grid pitch 8 mm, site size 5 mm, a site
  is kept when ≥ 80 % of it lies on the mask; tray area (x > 310 mm)
  excluded. Clustering: `site_changed` events of 8-neighbouring grid
  sites whose time windows overlap form one discovered source region.

Not allowed as input: `E2.inventory.json`, well names or coordinates,
part colours, part counts, SNES geometry, the E2 score or `actual` files.

## 3. Pipeline (all v1.1, versioned)

1. `tools/discovery/discover.py grid` writes an anonymous
   `custody-site-inventory@1` (sites `G-<row>-<col>`, basis
   `automatic grid`, start occupancy declared `occupied`).
2. `custody.py observe` (observer v1.1, commit `86122666`) with that grid.
3. `discover.py cluster` merges site events into source regions `S-<n>`
   and writes a removal-level events file; the unchanged custody linker
   (`linker.py`, hash as in `PROTO-01.md` §2) proposes links.
4. Outputs are hashed and committed before step 5.
5. Evaluation only: compare regions and links with the E2 ground truth.

## 4. Criteria (fixed now)

A discovered region matches a well when its centroid lies inside the
well rectangle grown by 3 mm, during that well's true removal window.

| Outcome | Criterion |
|---|---|
| PASS | ≥ 3 of 4 wells discovered, ≤ 2 false-positive regions, ≥ 3 correct links, 0 incorrect links |
| STRONG PASS | 4 of 4 wells, 0 false positives, 4 correct links, 0 incorrect |
| FAIL | any incorrect link, or < 3 wells discovered |

Reported regardless: missed wells, false positives, interventions, and
every assumption that would not hold for another device.

## 5. Result run 1 (2026-10-29): FAIL

Method `tools/discovery/discover.py` (grid, cluster), observer v1.1
`86122666`. Outputs in `C:\ANASTYLOS\data\proto-01\E2D\`, hashed before
the comparison: `grid.inventory.json` `5b25d130051cd08c65ac227d87f147a548c54a1ba431f7252935cd8f47558575`
(121 anonymous sites, no device-specific input), `run1\events.json`
`16a8eecc723d843567740265f23d27a628776c3cccff621e1986203bb5b52947`,
`run1\regions.json` `dc4b320b16c2dd2d560b4bae62d9d061b0d93c83496c3c0902df1e3233b36e95`,
`run1\custody.json` `34da5e35bda68e96c33ceb1f8442d0f7b85b995030f17a3aa2e1c7b0e8cc9070`.

| Measure | Value |
|---|---|
| Wells discovered (via `site_changed`) | 0 of 4 |
| False-positive regions | 0 |
| Links | 0 correct, 0 incorrect, 5 deposits `unattributed` |
| Grid sites | 111 observing, 17 `epoch_broken`, 5 `inventory_not_observable` |
| Interventions | none (no inventory, no names) |

Cause: with a dense grid, a part lies inside the anchor rings of its
neighbouring sites. Its removal changes those rings; the global device
re-registration is accepted (shift ≈ 0.08 px, the device did not move), but
the local ring still differs, so the observer declares `pose_epoch_break`
instead of a content change.

Exploratory only, not scored (uses ground truth after the fact): the
epoch breaks cluster at the right times and places — 2.0 mm from W-YELLOW
(5 sites), 1.3 mm from W-BLUE (8), 2.8 mm from W-GREEN (3), 9.2 mm from
W-RED (1 site; 5 sites near it were unobservable from the start).

Assumptions that would not hold for every device: the device contrasts
with white paper (a white device would vanish from the mask); parts are
larger than about one grid pitch (8 mm); the device rests still on the
sheet; a fixed overhead camera.

## 6. Run 2 preregistration (2026-10-29, before running)

Method revised after the run 1 failure and evaluated on the same E2
footage. A pass here is retrospective and only justifies an independent
new recording; it does not show generalization.

- Observer **v1.2**: branch `feat/custody-v1.2`, commit `30718b86`,
  worktree `Anastylos-custody-v12` (v1.1 stays untouched in
  `Anastylos-custody` for E2R). `observer.py`
  `61ee588519ad75db52ddde7d79de3106e6352fc3b5b2d472d16952457178bfdf`,
  `session.py` `c87c4fb8e954e8b54fb3b204deb1d6bb70395597882c119bb463344e04dd0b60`,
  28/28 tests. With the default config, v1.2 on E2 gives events and links
  identical to v1.1 (`E2\regression-v1.2\`).
- Config `custody-config.grid.json`
  (`3a16c58e8f3f3c67141d85fdce2d4257671cc4c765c4e0bbab24bdeadb1153a5`,
  canonical `085f2d97237a192f904a165c533f84aa3414c47590e480eb0283c90e77a918e5`):
  the default config plus `ring_change_with_verified_pose = content`.
- Unchanged from run 1: the grid inventory `grid.inventory.json`
  (`5b25d130…`), `discover.py cluster`, the unchanged linker, and the
  criteria of §4. No parameter of the grid or the clustering is changed.

Run 2 outputs, hashed before comparison with ground truth
(`C:\ANASTYLOS\data\proto-01\E2D\run2\`): `events.json`
`5e08a9e80ee575dc2ea5fca3c60769d210c19e1ac2cb8832e38517bb797d83ce`,
`regions.json` `7baf99dbf2dc63480854606b283bfe5e7adf05dbae2655c991438300ed0a0027`,
`region-events.json` `2da470d2c3f5ebf0e227041ac583b0c23d61034d366e0e7b03a6759ae70c97fa`,
`custody.json` `1bb2d1efe04c718884e3a11e7285c6436a97f7b6e6527f3eb16763e4bebe47a6`.

## 7. Result run 2 (2026-10-29): PASS (retrospective)

| Measure | Value |
|---|---|
| Wells discovered | 3 of 4: S-1 → W-YELLOW, S-2 → W-BLUE, S-3 → W-GREEN; each region's time window equals the true removal window |
| Missed | W-RED: the five grid sites over it (G-10-25, G-10-26, G-11-25, G-11-26, G-11-27) were `inventory_not_observable` from the start (never clean within the 2 s initialisation tolerance); cause not yet diagnosed |
| False-positive regions | 0 |
| Links | 3 correct (S-1 → cell-6, S-2 → cell-1, S-3 → cell-5, all `candidate_temporal_exclusive`, all matching the sealed E2 actual file), 0 incorrect; cell-9 (C4 move) and cell-4 (red, source missed) `unattributed` |
| Pose epoch breaks | 0 (21 `anchor_ring_changed`) |
| Interventions | none: no inventory, no names, no coordinates |

Outcome by §4: **PASS** (not STRONG: one well missed). Retrospective on
footage whose results were known; it justifies an independent recording,
not a generalization claim. SNES-specific assumptions: none in code or
parameters; the assumptions listed in §5 (contrast with white paper,
parts larger than about 8 mm, device at rest, fixed camera) remain.

Next: diagnose the unobservable sites over W-RED, then preregister an
independent take (new device or new arrangement, no inventory) before
any generalization claim.

### Diagnosis of the missed W-RED source (2026-10-29)

Traced with v1.2 grid config (`E2D\diag-red\`): the five grid sites over
W-RED report `intrusion = true` on every sample from t = 0 while motion is
0, so they never become clean and are never initialised. The red part is
skin-coloured; the hand test treats a fresh-skin component as a hand when
it overlaps a zone's guard and reaches outside it. A 13 mm named site
contained the whole part (the "skin-coloured part inside the zone" case),
a 5 mm grid site does not. Generic consequence: any skin-coloured part
larger than a grid site is unobservable. Candidate fix for a later
version (not applied): treat a skin component as a hand only if it moves
or connects to the edge of the device region.
