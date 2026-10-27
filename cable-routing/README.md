# Cable Routing & Assembly Order

ANASTYLOS capability #12. Two cooperating, independently testable subsystems:

- **Cable routing** (`anastylos_cable/cables.py`): OpenCV/NumPy extraction of
  visible cable paths from tightly framed photos. Ordered polylines, widths,
  endpoints, bends, crossings, occlusions, clip/anchor candidates.
- **Assembly order** (`anastylos_cable/graph.py`): read-only dependency graph
  built from assembly-memory events plus the change-detection observations
  they cite. Temporal, spatial, candidate, and confirmed relationships stay
  distinct.

No cloud calls, no downloads, no new dependencies. Synthetic tests prove
pipeline self-consistency only — never real-hardware accuracy.

## What the module never claims

- A 2D crossing is not an electrical connection, a shared cable identity,
  or a depth order.
- Temporal order ("A removed before B") is not a confirmed dependency.
- Cable identities end at the frame: no persistent tracking across images.
- Hidden segments are never invented; occlusions yield disconnected
  segments plus explicitly uncertain continuation hypotheses.
- A reversed disassembly timeline is a reassembly *proposal*, never a
  verified procedure.

## Run

Use the existing calibration virtualenv; install nothing.

```powershell
# from the cable-routing directory
..\.venv\Scripts\python cable.py read photo.png --out cable_out
..\.venv\Scripts\python cable.py compare before.json after.json
..\.venv\Scripts\python cable.py graph ..\assembly-memory\memory.jsonl --out graph_out
..\.venv\Scripts\python cable.py eval --labels labels.json --images-dir photos
..\.venv\Scripts\python cable.py eval --graph-fixtures fixtures
```

`cable.py` adds both `cable-routing/` and `assembly-memory/` to `sys.path`.
`graph` never writes to the memory log.

## CLI

- `read IMAGE [--roi x,y,w,h] [--metadata frame.json] [--out DIR]` —
  writes `cable_result.json` and `cable_overlay.png`.
- `compare BEFORE AFTER [--out DIR]` — same-frame before/after matching;
  `unavailable` when image sizes or conventions differ.
- `graph MEMORY [--results-dir DIR] [--confirmations FILE] [--out DIR]` —
  writes `assembly_graph.json` and Graphviz `assembly_graph.dot`.
- `eval --labels FILE [--images-dir DIR] [--out DIR]` — cable scoring.
- `eval --graph-fixtures DIR [--out DIR]` — graph scoring.

## Cable result schema

`cable_result.json` carries `status` (`ok` / `ambiguous` / `unknown` /
`unavailable`), `coordinate_convention` (source-frame pixels, origin
top-left, +x right, +y down), `cables` (ordered `polyline_xy` per visible
branch, `width_px`, tangents, typed ends: `tip` / `touch` / `junction`),
`endpoints` (`free` / `frame_edge` / `connector_proximity`),
`features` (`branch` / `tap` / `crossing` / `overlap` with arm `degree`),
`hypotheses` (uncertain continuations, confidence 0.35, segments stay
disconnected), `occluded_regions`, `clips`, `anchors`, `channels`,
`excluded`, `quality`, `geometry_mm`, and `calibration_error`.

Millimetres appear only when `--metadata` supplies a finite
`pixels_per_mm` in `[0.05, 500]`; otherwise pixels stand with an explicit
error or null. Branch records carry `interpolated_px`: path pixels the
tracer drew straight across a junction blob instead of observing.

## Assembly graph schema

Nodes are live (non-superseded) observations in log order. Edges point
`before -> after` (removal constraint direction):

| kind      | meaning                                  | source                    |
|-----------|------------------------------------------|---------------------------|
| temporal  | A removed before B (timeline only)       | log order, never an edge  |
| spatial   | A overlaps/covers B in one view          | cited region bboxes       |
| candidate | A might need to precede B                | reasoner adapter          |
| confirmed | A must precede B                         | confirmations file        |

The default reasoner emits a candidate only for region pairs sharing one
`rectified.view` with cover or >30% overlap, directed by timeline order.
Pure temporal pairs yield nothing. Pass `reasoner=` to `build_graph` for a
custom adapter (it receives nodes plus resolved regions).

Provenance: each cited `result.json` is re-hashed and compared with the
cited `result_sha256`. A mismatch excludes the region (reported under
`missing_regions` with both hashes), so substituted bytes can never
feed a candidate edge. Regions without a usable cited hash still
resolve, but are listed under `unverified_regions` — never trusted.

Operator confirmations are a JSON list (or `{"edges": [...]}`):

```json
[{"before": "obs-0001", "after": "obs-0002",
  "confirmed_by": "operator-initials", "note": "cover screw first"}]
```

Entries with unknown ids or no `confirmed_by` are reported under
`missing_confirmations`, never added. Output adds `cycles` (confirmed
cycles block ordering), `orderings` (canonical disassembly order, exact
valid-order count up to 20 nodes, parallel groups), and `reassembly`
(reversed-order proposal with `unresolved` steps and a NOT-verified
warning).

## Real-photo evaluation workflow

Cables:

1. Photograph tightly framed cable runs; keep one frame per analysis.
2. Hand-label each image: 3+ polyline points per visible cable branch,
   measured width in px, and junction features with kind + position:
   `{"images": [{"file": "….png", "cables": [{"polyline_xy": […],
   "width_px": …}], "features": [{"kind": "crossing", "xy": […]}]}]}`.
3. Run `cable.py eval --labels labels.json --images-dir .`.
4. Read matches/misses/extras, mean polyline drift (px), width error,
   and feature hits. Decide pass/fail from these numbers — not from the
   synthetic suite.

Graphs: build one directory per scenario with `memory.jsonl`,
`results/*.json` (real change-detection outputs), optional
`confirmations.json`, and `expected.json`
(`{"confirmed": [["before", "after"], …]}`); run
`cable.py eval --graph-fixtures DIR` and check confirmed-edge
precision/recall plus order validity.

Real-hardware validation status: **not performed** — no labelled
real-photo set exists yet.

## Tests

```powershell
# from the cable-routing directory
..\.venv\Scripts\python -m unittest discover -s tests -v
```

60 tests: 15 synthetic kinds with exact counts and junction degrees,
rotation/lighting/glare/shadow bounds, calibration valid/absent/invalid,
ROI, segmenter adapters, determinism, JSON serialization, comparison,
annotation, CLI, graph cases (sequence, parallel, temporal-only,
confirmed, candidate, contradiction, cycle, multiple orders, missing refs,
superseded, adapters, read-only), graph provenance verification
(match/mismatch/missing/malformed hashes, tampered-geometry exclusion),
DOT export, and both eval workflows.

## Known limitations

- Rotated (~30°) crossings can split into arm segments with the crossing
  unreported; segments are preserved, nothing is invented.
- Rotated curves may gain a spurious tap at interpolation kinks.
- Very dark frames report `unknown` rather than risk hallucination.
- Glare across a run typically splits it into hypothesized segments.
- Clip vs anchor roles rely on proximity heuristics and can swap.
- Graph candidates need resolvable region evidence; unresolvable logs
  yield `unknown`, and order counts above 20 nodes are not enumerated.
- No persistent cable identity across frames; comparison is same-frame.

## Layout

- `anastylos_cable/cables.py` — segmentation adapter seam, tracing, typing
- `anastylos_cable/graph.py` — read-only dependency graph + DOT export
- `anastylos_cable/compare.py`, `annotate.py`, `eval.py`, `cli.py`
- `anastylos_cable/synthetic.py`, `geometry.py` — fixtures, mm conversion
- `tests/` — `test_cables.py`, `test_graph.py`, `test_eval.py`
