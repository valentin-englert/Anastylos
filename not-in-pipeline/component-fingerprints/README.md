# ANASTYLOS component fingerprints

Evidence-backed visual fingerprinting: enroll component crops into a
local file gallery, then rank later photographs against it.
Deterministic OpenCV plus NumPy baseline, no learned models, no
downloads, no cloud calls.

```powershell
..\.venv\Scripts\python fingerprints.py enroll --image part.png --id ic-01 --gallery gallery\ --out fingerprints_out
..\.venv\Scripts\python fingerprints.py match --image query.png --gallery gallery\ --out fingerprints_out
..\.venv\Scripts\python fingerprints.py eval --labels labels.json --images-dir queries\ --gallery gallery\ --out fingerprints_eval_out
```

Optional flags: `--roi x,y,w,h`, `--frame-meta meta.json`
(`{"px_per_mm": 4.0}` plus optional view/origin), `--marking 358`,
`--top-k 5`, `--weights weights.json`, `--force` (re-enroll one id).

## Descriptors

Each fingerprint stores complementary evidence with provenance
(source image, ROI, crop SHA-256, timestamp, descriptor version):

- Silhouette: log-scaled Hu moments (hu1–hu3 compared; hu4–hu7 are
  recorded but too unstable to compare), contour geometry
  (solidity, vertices, defects, ellipse ratio, extent, aspect).
- Holes and notches: interior hole count and areas, strong
  convexity defects.
- Edges: Canny density and mean gradient strength.
- Size: calibrated millimetres from `px_per_mm` when valid frame
  metadata is supplied, otherwise `uncalibrated` — pixels are
  never compared as millimetres.
- Color: foreground HSV histograms (hue skipped when both sides
  are near-gray, where hue is noise).
- Appearance: silhouette-region thumbnail, normalized cross
  correlation over an 8-angle rotation search.
- Local features: rotation-tolerant ORB descriptors (capped,
  deterministically ordered), cross-checked Hamming matches.
- Marking text: only when explicitly supplied (`--marking` or
  eval labels); nothing here runs OCR.
- Embeddings: optional provider vectors via the
  `EmbeddingProvider` adapter (name plus version, cosine
  similarity). No provider is integrated; the interface is ready
  for future local or vendor vision models.

Fingerprint creation and matching are separate steps: `describe`
builds records, `match` compares them, `store` keeps the gallery
(one atomic JSON file per id; corrupt files and version
mismatches are reported as excluded, never silently compared).

## Similarity and verdicts

The combined score is a weighted mean over available components
(weights configurable, reported per candidate). It is an
uncalibrated heuristic, not a probability, and the best score is
never proof that two images show the same physical object — the
identity note says so on every result.

Verdicts: `ok` (winner clears 0.55 with 0.10 margin), `ambiguous`
(ties, rivals within margin — including visually identical
distinct parts, which must stay indistinguishable), `unknown`
(nothing clears the floor), `rejected` (poor query quality, with
reason). Enrollment refuses poor-quality crops (empty, uniform,
tiny, blurry, glare-drowned, no silhouette) instead of storing
them.

## Evaluation

Labels: `{"images": [{"file": "query.png", "expect_id": "ic-01"}]}`
(`expect_id` null for queries matching nothing enrolled; optional
per-entry `roi`, `frame_meta`, `marking`). The report counts
exact matches, misses, false confident matches, and undecided
cases, plus top-1/top-k retrieval. Synthetic fixtures prove the
harness only.

## Test

```powershell
cd component-fingerprints
..\.venv\Scripts\python -m unittest discover -s tests -v
```

## Limitations

- Validated on deterministic synthetic renders only (four part
  families, rotations, scale, lighting, backgrounds, noise,
  occlusion, glare). No real-hardware retrieval is claimed or
  measured; real-photo validation has not been performed.
- Heavy glare reads `unknown` (correct top, below floor) by
  design; occlusion overlapping the part degrades the silhouette
  components first.
- Rotation tolerance comes from Hu moments, geometry, color, and
  ORB; the thumbnail search covers 45-degree steps and weakens
  between them.
- Thresholds (0.55 accept, 0.10 margin) are heuristics tuned on
  synthetic fixtures, not calibrated decision boundaries.
- The gallery is a local directory, not synchronized; no
  Assembly Memory integration exists yet and its schema is
  untouched.
