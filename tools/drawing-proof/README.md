# tools/drawing-proof — single-part technical drawing from one real frame (research)

The existing product drawing job uses Muse frame choice and boxes, hosted SAM 3.1 masks, deterministic gates,
then SVG and photo overlay. It is wired into the session UI; operator binding remains separate from memory.
See [`RESULTS.md`](RESULTS.md) for real-image/API validation and known failures. The classical proof below is
the earlier offline baseline, not a replacement for Muse/SAM.

When processing a copied session, set `ANASTYLOS_DRAWING_CACHE` to a separate writable cache folder before
starting the UI or drawing worker. This redirects sampled frames and hash memos; the default remains the
existing `C:\ANASTYLOS\data\drawing-cache`. Original videos and memories stay read-only.

## Pipeline

1. `source.py`: read one exact frame, register it with the unchanged custody sheet reader
   (`custody-config.v15.json`, view `surface_a3`), warp the part region to sheet millimetres (10 px/mm).
2. `segment.py`: Otsu on L* (seed) → cast-shadow rejection (warm b* and nearly textureless inside the
   seed's outer band) → GrabCut seeded from the seed with the shadow as sure background. Optional manual
   polygons in sheet mm (`outline_corrections`) are applied last.
3. `draw.py`: Douglas–Peucker outline with a measured maximum deviation; light tone regions (membranes) with
   their enclosed round openings; manual detail boxes (GrabCut from a documented rectangle).
4. `proof.py`: photo overlay, SVG (1 unit = 1 mm, `data-line-role` outline/edge/detail), raster preview of
   the same geometry, `report.json` with edge fit, timings and every manual step.

```
python tools/drawing-proof/proof.py --spec tools/drawing-proof/specs/front_shell_p3_f300.json --out C:\tmp\anastylos-drawing-proof\run-front
python -m unittest discover -s tests        # from tools/drawing-proof
```

## Result on FULL-01-P3 frame 300 (front shell, interior side), 2026-11-01

| Measure | Value |
|---|---|
| Outline points | 114 (dense boundary 3654), max deviation 0.2 mm |
| Boundary within 2 px (0.2 mm) of a Canny edge | threshold only 73.2 % → final 99.4 % (simplified 98.2 %) |
| Shadow rejected | 654.5 mm² |
| Regions | 2 membranes (auto) with 5 + 6 enclosed openings; rubber pad from 1 manual box |
| Runtime | 6.2 s on i7-8700K (rectify 0.6 s, outline 2.8 s, details 2.6 s) |
| Reproducibility | two runs give identical SVG, geometry and mask bytes |

Manual steps: one rectangle for the start/select rubber pad (its light grooves split it under any lightness
threshold, and shadowed shell floor is as dark). No outline correction was needed.

## Limits

- Sheet-plane millimetres: the raised rim is shifted by parallax; these are not part dimensions.
- One top view only: heights, the outer side and anything under the part are unknown and not drawn.
- Openings touching a membrane edge are not drawn (one D-pad contact on this frame).
- No identity: owner memory S has no single front-shell record; the drawing binds none.
- Shadow rule thresholds are measured on this frame and lighting; other takes need their own check.
- Development data, visual check plus edge-fit numbers; no independent ground-truth contour.

## Missing captures for the three-assembly explosion

No FULL-01 frame shows the back shell or the PCB hand-free: fingers cover both short ends of the back shell
(P2 ≈ 17 s) and both ends of the PCB, with the cable across it (P2 frame 2040, ≈ 68 s); the aftermath photo
shows only the emptied front shell. Their outlines would have to be invented at the ends, so no explosion is
drawn yet.

| Capture | Purpose | Success criterion (checked with `proof.py`, no outline correction) |
|---|---|---|
| Back shell, exterior up, flat inside the dashed work area, hands out of frame ≥ 2 s, same camera stand | outline of the back shell | outline closed, not touching the region border; ≥ 95 % of the boundary within 0.2 mm of an image edge; no skin-coloured pixels inside the mask |
| PCB, component side up, cable laid straight away from the board (not across it), hands out ≥ 2 s | PCB outline and cable exit side | same as above for the board; cable outside the board outline |
| Operator confirmation, once | common view side and layer order | operator confirms: front shell interior up, PCB component side toward the front shell, back shell interior toward the PCB; cable exit on the same edge in all three drawings |

Until confirmed, an explosion layout is a proposal: layer order front shell → PCB → back shell along one
axis, gaps in paper space only (teardown-skill convention: 8–15 mm, no measured assembly path).

## Background job state (`job_control.py`)

A job's `progress.json` is written atomically under a per-job lock (`progress.lock`). Each start or resume opens a new
`generation` (with a `token`) that is on disk before the worker is spawned; the worker adopts it. A status poll
re-judges under the lock and writes `failed` only for the same still-running generation, so a worker that just wrote
`done` is never overwritten; a worker whose generation was replaced, or whose job a poller already ended, is refused
(`Superseded`) and stops. Resume (`start` on an existing job dir) first kills and verifies dead the previous worker
(by recorded pid); if it will not die, no second worker is spawned. Residual risk: a recorded pid that the OS has
reused for an unrelated process would be killed on resume. Tests: `tests/test_drawing_job.py` (fake clock and processes).
