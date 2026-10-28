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
