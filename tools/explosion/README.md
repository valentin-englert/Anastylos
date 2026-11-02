# tools/explosion — schematic explosion view from part drawings

Composes one **schematic** explosion drawing from the part drawings a session
already has (`tools/drawing-proof` jobs) and the operator's confirmed layer
layout. Stdlib only, offline, deterministic (no timestamps in the outputs).
Architecture status and contracts stay in [`ARCHITECTURE.md`](../../ARCHITECTURE.md);
the verdict on the earlier explosion work and the viewer routes are in
[`docs/integration/EXPLOSION-VIEW-01.md`](../../docs/integration/EXPLOSION-VIEW-01.md).

## What it draws — and what it never does

- Only outlines that exist: the `observed_outline` paths of each job's
  `drawing.svg` (1 SVG unit = 1 mm on that drawing's sheet plane), copied
  verbatim (only the `d` data is taken over), one uniform scale.
- Layer order = the operator's confirmed order in `explosion.json`, top layer
  first. The spacing between layers is **layout only** and says so on the
  sheet: no height, mounting point or assembly path is implied. "Assembled"
  only centres the outlines on their bounding boxes; the drawings share no
  transform, so it is not an alignment.
- Every part is `<g class="ax-part" id="part-<job>" data-job data-status ...>`
  (selectable). A part is `confirmed` only when **all** hold: the job's
  `binding.json` says `confirm` for the *current* drawing bytes, the layer is
  `confirmed_by_operator`, and the layer has a view side. Anything else is drawn
  **dashed amber with "UNCONFIRMED" and the reasons**.
- Not drawn (listed on the sheet and in the manifest, never replaced by a
  placeholder shape): missing job, rejected binding, no/invalid outline, drawing
  not in mm, job not `done`, or a result outside `drawn_unconfirmed` / `NEEDS_CONFIRMATION`.
  Technical `NEEDS_CAPTURE` / `NEEDS_REVIEW` gates cannot be bypassed by a saved binding.
- Nothing is written into job folders or memory.

## Layout spec `<session>/drawings/explosion.json` (anastylos-explosion-layout@1)

```json
{"schema": "anastylos-explosion-layout@1",
 "layers": [{"job": "front-shell-20261101-023522", "label": "Front shell", "view_side": "interior",
             "flip_horizontal": false, "confirmed_by_operator": false}],
 "note": "..."}
```

`layers` run top to bottom. `flip_horizontal` mirrors that outline about its
bounding-box centre (a back side photographed from behind). `confirmed_at` is
added by `confirm_layer`. The operator never edits the JSON in the normal flow:

| Function (`explosion_layout.py`) | Effect |
|---|---|
| `propose(session_dir)` | writes the skeleton from existing drawn jobs: one layer per (part, scope), an operator-bound run before the newest run, rejected/failed/no-outline jobs skipped; existing layers and confirmations are kept, new parts appended unconfirmed; initial order = front/back word heuristic (a proposal only); idempotent |
| `confirm_layer(session_dir, job, *, label, view_side, flip_horizontal, confirmed=True)` | the operator's decision on one layer as shown; refuses without a view side; `confirmed=False` withdraws |
| `reorder_layers(session_dir, order)` | new top-to-bottom order; moved layers lose their confirmation |
| `load` / `save` / `validate` | strict schema (unknown keys, duplicate or path-like job names refused); writes are atomic (temp file + `os.replace`) |

## Compose

```powershell
python tools/explosion/compose.py propose --session <session>
python tools/explosion/compose.py confirm --session <session> --job <job> --view-side interior --label "Front shell"
python tools/explosion/compose.py compose --session <session> [--out <dir>] [--mode exploded|assembled]
```

`compose.compose(session_dir, spec=None, mode)` returns `Composition(svg,
manifest)` in memory; `write_outputs` writes `explosion.svg` and `manifest.json`
(`anastylos-explosion-manifest@1`) to `<session>/drawings/explosion/` (the spec
and the manifest are separate files in separate folders). The manifest records
per layer: status and reasons, binding state, view side (layer and drawing job),
flip, outline size in mm, placement in both modes, and `{path, sha256}` for
`drawing.svg`, `overlay.jpg`, the rectified image (`rectified.png`, or the
`chosen_rectified.png` that `drawing_job.py` actually writes), `binding.json`,
`progress.json`; plus the SHA-256 of the layout spec, the SVG, `excluded`,
`warnings` (e.g. layer view side differs from the drawing job's).

## Files

`compose.py` (SVG, manifest, CLI) · `explosion_layout.py` (spec) ·
`explosion_jobs.py` (read-only job/outline reader; does not call
`job_control.status`, which can rewrite `progress.json`).
Viewer: `tools/session-ui/explosion.py` + `web/explosion.*`.

## Tests

```powershell
cd tools/explosion; python -m unittest discover -s tests
```

Synthetic rectangle/triangle/hexagon jobs only (no photos, no network):
three selectable groups, order from the spec, unconfirmed marking (unbound,
unconfirmed layer, no view side, stale binding), excluded layers draw nothing,
verbatim paths and uniform scale, flip, SHA-256 of every input, determinism and
read-only inputs, hostile input (DOCTYPE, markup in path data and labels),
propose/confirm/reorder rules, spec validation.

## Limits

- Synthetic and one real front-shell drawing only; no real multi-part run yet.
- Outline paths must be absolute `M L H V Z` (what `drawing.svg` writes); other
  commands are refused rather than approximated.
- No registration between drawings: the assembled mode is not a fit, and no
  height or thickness exists, so the view has no depth information.
- Operator confirmation is recorded in the spec only; it is not written to
  Assembly Memory.
