# ANASTYLOS SAM tracking adapter (experimental)

Isolated evaluation of Meta SAM 3.1 for segmentation and video
tracking on real disassembly footage. Cloud-isolated by design: this
is the ONLY Python module in the tree allowed to touch the network,
and it never imports — and is never imported by — the offline core.
Architecture source of truth stays in
[`ARCHITECTURE.md`](../ARCHITECTURE.md).

## What this is

- A minimal provider adapter: concept + box/point prompts, image and
  video segmentation, forward/backward propagation, confidence,
  official-parser mask decoding.
- A versioned additive evidence format (`sam-evidence-v1`) with
  SHA-256 provenance over every artifact.
- A read-only temporal fusion layer that proposes
  source→destination correspondence candidates and abstains
  (`UNKNOWN` + reason) when evidence is insufficient.
- Deterministic replay: every live result is cached as a raw SSE
  transcript and re-parses byte-identically without network.

## What this is NOT

- Not a memory writer: no code path appends to `memory.jsonl`.
- Not a confirmer: candidates never self-promote; only an operator
  confirms physical identity.
- Not a pipeline: `SAM Adapter → SAM Evidence → Evaluation /
  Fusion Candidates` only. Integration needs a reviewed interface.

## Layout

- `sam.py` — CLI entry point (see below).
- `anastylos_sam/` — adapter code. `client.py` is the ONLY network
  module (stdlib `urllib`); everything else is offline.
- `vendor/` — official `meta-sam-parser` 0.1.0 sources, verbatim at
  pinned commit `40318970…`, with `VENDOR.md` provenance and the SAM
  License. Do not edit.
- `tests/` — 55 deterministic tests over official conformance
  vectors + recorded SSE transcripts. No network, no fixtures
  outside `tests/fixtures/`.

## Run

From this directory, with the pinned venv for render/Video paths
(parse/evidence/fuse/eval work on any Python 3.10+):

```powershell
# suite (offline, deterministic)
..\.venv\Scripts\python -m unittest discover -s tests

# dry-run first: plans cost, writes plan only, uploads nothing
..\.venv\Scripts\python sam.py plan-image --image <frame.jpg> `
  --concept screw --out <plan.json>
..\.venv\Scripts\python sam.py plan-video --clip <clip.mp4> `
  --concept hand --out <plan.json>

# cut an uploadable sub-clip (faststart, provenance sidecar)
..\.venv\Scripts\python sam.py cut-clip --video <src.mp4> `
  --t-start 19 --seconds 20 --out <clip.mp4>

# live calls need BOTH flags + a budget (default $3)
..\.venv\Scripts\python sam.py --live --upload-ok --ledger ledger.jsonl `
  segment-image --image <frame.jpg> --concept screw --out <run/>
..\.venv\Scripts\python sam.py --live --upload-ok --ledger ledger.jsonl `
  segment-video --clip <clip.mp4> --concept screw --out <run/> `
  --direction forward

# offline replay / render / fuse / eval / report
..\.venv\Scripts\python sam.py replay --request-dir <run/samq-…/> [--render --media <f> --out <o/>]
..\.venv\Scripts\python sam.py render --request-dirs <r1/> [<r2/>] --media <clip.mp4> --out <o/> --mp4
..\.venv\Scripts\python sam.py fuse --config <fuse.json> --out <o/>
..\.venv\Scripts\python sam.py eval-seg --gt <gt.json> --evidence <run/> --out <o/>
..\.venv\Scripts\python sam.py report --run-dir <run/>
```

Box/point prompts: `--objects-json {"width":W,"height":H,
"objects":[{"id":1,"frame":0,"box":[l,t,r,b],"points":[{"x","y","label"}]}]}`
with half-open boxes in display pixels (use the exact `w`/`h` the
media reports after rotation).

## Evidence contract (sam-evidence-v1)

One directory per request: `evidence.json` (this schema), raw
`transcript.jsonl`, `masks/*.png`. Coordinates are display pixels,
origin top-left, +x right, +y down; boxes half-open as normalized by
the official parser. Track ids are scoped to their request
(`scope: "request:<id>"`). Cost cites public SAM rates
($2.50/1k images, $0.20/1k frames, 50-frame minimum) as
`ESTIMATED_FROM_PUBLIC_RATES`. No millimetres anywhere: no
calibration is cited for the SNES footage.

`replay` re-parses the cached transcript and re-verifies hashes; a
run reproduces without repeating paid calls.

## Fusion and eval

`fuse` runs one source-anchored hypothesis through
`STATIC_SOURCE → CONTACT_CANDIDATE → TRANSPORT_CANDIDATE →
RELEASE_CANDIDATE → STATIC_DESTINATION` with frozen gates
(see `anastylos_sam/fuse.py`). Verdicts are `HYPOTHESIS_SUPPORTED`
or `UNKNOWN` + reason codes (`GAP_FRAMES`, `HAND_AMBIGUITY`,
`ID_JUMP_SUSPECT`, …). Proximity is not contact; a persistent id is
not identity; a missing frame is not absence.

`eval-seg` scores boxes at frozen IoU 0.5 against a sealed GT file
(`sam-gt-v1`). GT lives with the experiment outputs (outside this
repo), never here; every eval cites the GT SHA-256.

## Limits

- Paid cloud API; all spend is ledgered and capped by `--budget-usd`.
- Results vary slightly run to run (model-side sampling).
- Video frame indices are clip-relative as the API decodes the
  upload; mapping to source time rides on the `.clip.json` sidecar.
- Masks are coarse rasters upscaled for display; overlays label the
  native size.
- No real-hardware validation is claimed until the SAM-01 report
  says so with sealed GT and ledgered runs.

## Validation scope

- 55/55 unit tests green (offline, deterministic).
- Live runs: see the SAM-01 report (forthcoming) for what actually
  ran, what worked on genuine footage, and the GO/CONDITIONAL/NO-GO.
