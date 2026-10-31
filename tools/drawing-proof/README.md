# tools/drawing-proof — single-part technical drawing from one real frame (research)

Research prototype on branch `research/drawing-proof`. It turns one hand-free video frame into a photo-checked
part outline and a technical SVG. Not wired into the agent, guide, memory or session UI.

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
