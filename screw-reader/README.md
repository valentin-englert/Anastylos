# ANASTYLOS screw head recognition

Classifies visible screw-head drive geometries from tight crops with
evidence and uncertainty. Deterministic OpenCV plus NumPy baseline:
circular head localization, dark-recess segmentation, and
rotation-invariant geometric features feed a standard-library-only
rule classifier. No pretrained model, cloud API, or downloads.

```powershell
..\.venv\Scripts\python screw.py read --image path\to\screw_crop.png --out screw_out
..\.venv\Scripts\python screw.py read --image path\to\screw_crop.png --roi 20,20,360,360 --out screw_out
..\.venv\Scripts\python screw.py eval --labels labels.json --images-dir crops\ --out screw_eval_out
```

## Classes

- `slotted`, `phillips`, `pozidriv`, `torx`, `hex`, and `unknown`.
- Lookalikes split only on their own evidence: Phillips/Pozidriv on
  diagonal tick energy between the cross arms, Torx/hex on recess
  convexity (solidity) and corner count. Thin evidence stays
  `ambiguous`; there is no most-common-type default.
- A lobe-count gate caps verdicts: counts outside 2/4/6 can only be
  `ambiguous`, never `ok`.

## Pipeline

`read` localizes the head circle (Hough plus contour candidates
scored by Canny rim support), segments the dark recess inside it with
Otsu thresholding, and measures symmetry orders, eccentricity,
solidity, vertices, convexity defects, a detrended radial lobe
count, and cross tick energy. Quality indicators (contrast, glare
and shadow fractions, blur, recess fraction) travel with every
result. Boxes are source-frame pixels; no millimetre, drive-size,
thread, or manufacturer claims are made.

`analyze()` accepts `segmenter` and `ranker` callables so a future
segmentation or classification provider can plug in without touching
the pipeline.

Verdicts: `ok` (winner clears the floor with margin), `ambiguous`
(weak evidence, rival families, lookalikes, or impossible lobe
count), `unknown` (no head, no contrast, or nothing plausible).

## Evaluation

Labels: `{"images": [{"file": "crop.png", "class": "phillips"}]}`.
The report counts exact matches, misses, false confident readings,
and undecided cases per labelled set, and writes one annotated
overlay per image. Synthetic fixtures prove the harness only.

## Test

```powershell
cd screw-reader
..\.venv\Scripts\python -m unittest discover -s tests -v
```

## Limitations

- Validated on deterministic synthetic renders only: nominal
  geometries, rotations, scale, mild tilt, glare, shadow, wear, and
  occlusion. No real-hardware accuracy is claimed.
- Heavy glare, strong perspective, severe wear, and large
  occlusions report `ambiguous` by design.
- Partially occluded slots may read `ambiguous` with the true
  class on top rather than `ok`.
- Real-photo validation has not been performed: no real screw
  images exist in this environment yet.
