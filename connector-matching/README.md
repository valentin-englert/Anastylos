# ANASTYLOS connector matching

Analyzes photographs of plugs and sockets, extracts visible mating
geometry, and ranks previously observed candidate counterparts.
Deterministic OpenCV plus NumPy baseline, no learned models, no
downloads, no cloud calls. Self-contained: no imports from other
recognition modules (patterns mirror component-fingerprints).

```powershell
..\.venv\Scripts\python connectors.py observe --image sock.png --id sock-01 --gallery gallery\ --out connectors_out
..\.venv\Scripts\python connectors.py match --image query.png --gallery gallery\ --out connectors_out
..\.venv\Scripts\python connectors.py eval --labels labels.json --images-dir queries\ --gallery gallery\ --out connectors_eval_out
```

Optional flags: `--roi x,y,w,h`, `--frame-meta meta.json`
(`{"px_per_mm": 4.0}` plus optional view/origin), `--marking X1`,
`--top-k 5`, `--weights weights.json`, `--force`. `observe`
without `--gallery` analyzes without saving.

## Observations

Each record carries housing geometry and color, visible contacts
(bright pins vs dark cavities with positions), contact rows and
pitch, keying notches, latch tabs, orientation, quality
indicators, and provenance (source image, ROI, crop SHA-256,
timestamp, observation version).

The role is `plug` only with pins and no cavities, `socket` only
with cavities and no pins, otherwise `unknown`. Side views and
other crops without a usable mating face stay valid observations
with unknown role; matching them reports insufficient visibility
instead of guessing.

Millimetre pitch needs valid `px_per_mm` on a frontal mating
face (rectangular housing outline); otherwise pitch stays
pixel-only and is never compared across images. Contact,
keying, and latch positions live in a housing-fixed canonical
frame, so in-plane rotation does not move them.

## Matching counterparts, not duplicates

Candidates rank by mating-geometry score: housing fit, contact
count, canonical contact layout under mating transforms, keying
alignment, role complement, calibrated pitch, and supplied
marking text. Mating faces meet mirrored, so only mirror-class
transforms are tried — plug-against-plug geometry does not
score as mating.

Three hard gates report physical impossibility: same known role
on both sides, more pins than cavities, and keying that aligns
under no mating transform. Every candidate also reports visual
similarity separately, and `verified_mechanical` plus
`verified_electrical` are always false: similarity alone never
establishes compatibility, and no pinouts, ratings, protocols,
series, or manufacturers are ever invented.

Verdicts: `ok` (clear top mating candidate), `ambiguous` (ties
or rivals within margin — identical-looking parts stay
indistinguishable), `unknown` (nothing clears the floor, poor
query, or no visible mating face), `unavailable` (no comparable
gallery content). Scores are uncalibrated heuristics; the
compatibility note says so on every result.

Future model help stays behind the `AssistantProvider` seam:
advisory notes only, never verdict changes, no integrations.

## Evaluation

Labels: `{"images": [{"file": "query.png", "expect_id": "sock-01"}]}`
(`expect_id` null for queries with no enrolled mate; optional
per-entry `roi`, `frame_meta`, `marking`). The report counts
exact matches, misses, false confident matches, and undecided
cases, plus top-1/top-k retrieval. Synthetic fixtures prove the
harness only.

## Test

```powershell
cd connector-matching
..\.venv\Scripts\python -m unittest discover -s tests -v
```

## Limitations

- Validated on deterministic synthetic renders only (mating-face
  views, rotations, scale, lighting, noise, occlusion, glare,
  missing/bent contacts). No real-hardware matching is claimed
  or measured; real-photo validation has not been performed.
- Heavy glare loses contacts and reads below the floor by
  design; occlusion overlapping the part degrades the housing
  frame first.
- Near-square housings weaken canonical-frame stability (long
  axis ambiguous); sub-degree contact shifts and bent pins
  penalize gradually rather than vetoing.
- Notches hugging a latch tab are attributed to the tab;
  single-view frontality is a rectangularity heuristic, not a
  calibrated plane measurement.
- The gallery is a local directory; no Assembly Memory
  integration exists and its schema is untouched.
