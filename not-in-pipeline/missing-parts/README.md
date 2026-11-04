# ANASTYLOS evidence-backed missing parts detection

Detects candidates for missing, displaced, or unrestored physical
components by comparing an explicitly declared original assembly state
against a later reassembly state. A visual difference is never reported
as a definitively missing part: candidates stay unconfirmed until an
external operator confirmation arrives. Deterministic OpenCV plus NumPy
baseline, no learned models, no cloud calls.

```powershell
..\.venv\Scripts\python missing.py compare --original ref.png --current now.png --context ctx.json --out missing_out
..\.venv\Scripts\python missing.py compare --detection ..\change-detection\selftest_output\results\single_01\result.json --context ctx.json --out missing_out
..\.venv\Scripts\python missing.py reconcile --comparison missing_out\result.json --expected expected.json --out missing_reconcile_out
..\.venv\Scripts\python missing.py reconcile --comparison missing_out\result.json --memory-log sessions\day1\memory.jsonl --confirmations conf.json
..\.venv\Scripts\python missing.py eval --labels labels.json --pairs-dir pairs\ --out missing_eval_out
```

The `selftest_output` paths above are generated, not committed: run the
change-detection synthetic selftest first (see
[`change-detection/README.md`](../change-detection/README.md)).

## Workflows

A. Visual state comparison: an original reference image with a current
reassembly image, using validated registration and calibrated geometry
where available. A change-detection `result.json` is the preferred
input: status, view, regions, and warnings are consumed verbatim with
full provenance, and rectified images are reused when present.

B. Evidence-based inventory reconciliation: explicit expected-component
observations (an inventory file, or a read-only Assembly Memory log —
never written to) are matched against the comparison's visual evidence
plus external confirmations. Open items stay listed as unresolved
obligations.

## Comparison context

Classified output needs a declared context naming both roles and
frames; brightness polarity needs the surface tone:

```json
{"original": {"role": "original_assembly", "frame": "fixture_back"},
 "current": {"role": "reassembly_state", "frame": "fixture_back"},
 "surface": "light"}
```

Without `--context` the pipeline reports neutral observed differences
only. Frames reuse the calibration-kit vocabulary. Differing frames
are refused unless the context carries a verified homography with
provenance and reprojection error within 1.0 mm.

## Vocabulary

- `observed_difference` — a visual change, nothing more.
- `candidate_absent_region` / `candidate_newly_occupied` — polarity
  evidence of disappearance or new presence; unconfirmed.
- `candidate_displaced` — a nearby absence/presence pair linked as one
  possible displacement; co-occurrence only, never identity.
- `shifted_present` — the part still matches nearby; still present.
- `hole_unchanged` — a void in both states; never a missing part.
- `expected_unconfirmed` — an expectation with no verdict either way.
- `confirmed_missing` — only via an external confirmation file, never
  from images.

Direct image pairs align with translation-only ECC (gated shift),
flatten illumination by local-mean division, and check every region
for small-shift presence, mounting-hole support, occlusion, glare, and
contradictory texture evidence. Repeated identical parts stay
indistinguishable: several proposed associations are listed, none is
asserted.

## Verdicts

`ok` means the comparison was successfully evaluated — never that the
assembly is complete. `ambiguous` marks weak or contradictory evidence,
`unknown` unusable evidence, `unavailable` a comparison that could not
run (registration failure, frame mismatch). Every result states its
verdict meaning, reasons, evidence references, and what still needs
human confirmation.

## Evaluation

Labels pair original/current images (or detection outputs) with
`truth_absent`, `truth_occupied`, and must-stay-clean `truth_present`
boxes plus optional expectations. The report gives missing-region
recall, false alerts, unknown cases, localization IoU, and obligation
outcomes. Synthetic fixtures prove the harness only.

## Test

```powershell
cd missing-parts
..\.venv\Scripts\python -m unittest discover -s tests -v
```

## Provider seams

`run_comparison(..., aligner, segmenter)` accepts callables so future
segmentation or registration providers plug in without touching the
pipeline; `None` keeps the built-in baseline.

## Limitations

- Validated on deterministic synthetic plates only. No real-hardware
  accuracy is claimed and no real assembly photos exist here yet.
- Small-shift presence checks and occlusion proxies are heuristics;
  heavy occlusion, strong glare, and large perspective read
  `ambiguous` or `unknown` by design.
- Millimetre coordinates on direct pairs need validated `--frame-meta`
  and share its plane; detection inputs reuse rectified millimetres.
