# SNES-01 benchmark tooling

Local consolidation (2026-10-29): preserved standalone research tooling,
including the original untracked source, prompts and tests. Nothing here
is connected to the product runtime. The full 84-test suite passed offline;
historical benchmark/model outcomes below were not rerun or independently
revalidated. No paid calls or retention-setting changes were made.

Minimal, standard-library-only helpers to prepare the SNES-01
(fake-SNES-controller teardown) offline benchmark draft.

What this is: reproducible manifest generation, contact-sheet
rendering (via a system `ffmpeg`), and schema/consistency validation
for human-authored benchmark JSON.

What this is NOT: a detection, tracking, or recognition engine. All
event proposals are human observations recorded in JSON; these scripts
only package, render, and check them. See [`ARCHITECTURE.md`](../../ARCHITECTURE.md)
for the project-wide evidence rules.

## Layout

- `make_manifest.py` — hash inputs, probe video metadata, record the
  frame↔timestamp mapping. Writes `source_manifest.json`.
- `make_contact_sheet.py` — render the labeled before/after contact
  sheet from `event_candidates.json` via `ffmpeg`.
- `validate.py` — check the four benchmark JSON files for schema and
  consistency errors (event count, pending statuses, frame
  references, timestamp mapping, crop bounds, manifest linkage).
- `tests/` — unittest suite (no fixtures, no network).

Benchmark data lives OUTSIDE this repo (never committed):

```text
C:\ANASTYLOS\data\snes-01\benchmark-v1\
  source_manifest.json
  stable_states.json
  event_candidates.json
  ground_truth.pending.json
  review.md
  contact_sheet.jpg
```

## Reproducible commands

From the repo root (Windows PowerShell; any Python 3.11+ works,
stdlib only — no venv needed):

```powershell
$bench = 'C:\ANASTYLOS\data\snes-01\benchmark-v1'
$frames = 'C:\ANASTYLOS\data\snes-01\frames-1fps'
$video = 'C:\ANASTYLOS\incoming\20261027_172426.mp4'

# 1. Manifest (SHA-256 + ffprobe metadata + per-frame inventory)
python tools/snes01-benchmark/make_manifest.py `
  --video $video --frames-dir $frames `
  --sibling 'C:\ANASTYLOS\data\snes-01\overview.jpg' `
  --mapping-checks 't=0:frame_0001,t=84:frame_0085,t=99:frame_0100,t=212:frame_0213' `
  --out "$bench\source_manifest.json"

# 2. Contact sheet (reads crops from event_candidates.json)
python tools/snes01-benchmark/make_contact_sheet.py `
  --events "$bench\event_candidates.json" --frames-dir $frames `
  --out "$bench\contact_sheet.jpg"

# 3. Validate the bundle
python tools/snes01-benchmark/validate.py `
  --manifest "$bench\source_manifest.json" `
  --stable "$bench\stable_states.json" `
  --events "$bench\event_candidates.json" `
  --ground "$bench\ground_truth.pending.json" `
  --frames-dir $frames --contact-sheet "$bench\contact_sheet.jpg"
```

`stable_states.json`, `event_candidates.json`,
`ground_truth.pending.json`, and `review.md` are authored by the
analyst (see `review.md` for method and uncertainty); the scripts
above never invent their content.

## Tests

From this directory (Gate 1 tests need OpenCV, so use the shared
calibration venv for the full suite):

```powershell
C:\Users\HP\Documents\Anastylos\.venv\Scripts\python -m unittest discover -s tests -v
```

## Gate 1 experiment (offline perception, autonomous)

`gate1_run.py` detects persistent visual changes between stable frames
and ranks sparse source→destination correspondences. It opens ONLY
`--frames-dir` (frame JPEGs) and `--manifest` (technical metadata +
per-frame SHA-256); it never reads `event_candidates.json`,
`stable_states.json`, `review.md`, or any ground-truth file. All
thresholds are fixed a priori in the script header. Scores are
uncalibrated ranking signals. Outputs are candidates, never confirmed.

```powershell
$venv = 'C:\Users\HP\Documents\Anastylos\.venv\Scripts\python'
& $venv tools/snes01-benchmark/gate1_run.py `
  --frames-dir 'C:\ANASTYLOS\data\snes-01\frames-1fps' `
  --manifest 'C:\ANASTYLOS\data\snes-01\benchmark-v1\source_manifest.json' `
  --out 'C:\ANASTYLOS\data\snes-01\gate1-exp1'
```

Writes `candidates.json` (`snes01-gate1/candidates@1`),
`contact_sheet_gate1.jpg`, and `uncertainty_log.md` to `--out`.

`gate1_gt_template.json` (`snes01-gate1/ground-truth@1`, `status:
"template"`) is the independent annotation form for rear housing,
D-pad, face-button group, and one membrane ambiguity case. A sealed
copy (`status: "sealed"`, annotator + date filled) is scored with:

```powershell
python tools/snes01-benchmark/gate1_score.py `
  --candidates 'C:\ANASTYLOS\data\snes-01\gate1-exp1\candidates.json' `
  --ground <sealed-gt.json> --out <score-dir>
```

PASS (fixed a priori): >= 3/4 targets within +-10 s with source hit
and correct correspondence-or-abstention, AND <= 6 unmatched
predictions (untargeted true removals go to human review, not
automatic FP). Without sealed GT the run is NOT YET EVALUATED.
Human intervention count is NOT YET MEASURED (no operator loop ran).

## Gate 1B experiment (cadence + motion custody, 1/5/10 fps)

Tests whether temporal continuity reveals transport that 1 fps
appearance matching cannot. Same 12 frozen Gate 1 windows at three
cadences with identical criteria; evidence levels
visual_change/interaction/transport/deposit/proposal, never confirmed.

```powershell
$venv = 'C:\Users\HP\Documents\Anastylos\.venv\Scripts\python'
& $venv tools/snes01-benchmark/gate1b_extract.py `
  --video 'C:\ANASTYLOS\incoming\20261027_172426.mp4' `
  --out 'C:\ANASTYLOS\data\snes-01\gate1b\frames10'
& $venv tools/snes01-benchmark/gate1b_run.py `
  --video 'C:\ANASTYLOS\incoming\20261027_172426.mp4' `
  --candidates 'C:\ANASTYLOS\data\snes-01\gate1-exp1\candidates.json' `
  --frames1 'C:\ANASTYLOS\data\snes-01\frames-1fps' `
  --frames10 'C:\ANASTYLOS\data\snes-01\gate1b\frames10' `
  --out 'C:\ANASTYLOS\data\snes-01\gate1b\run1'
```

Outputs (`snes01-gate1b/motion@1`): `motion.json`, `comparison.json/md`,
`sheet_detail_10fps.jpg`, `sheet_compare.jpg`, `strips/` (one frame
sequence per proposal), `review.csv` (operator verdicts unfilled),
`analysis.md` (decision + failure taxonomy).

Video facts (Phase A): stored 3840x2160, displayed 2160x3840
(rotation applied), 6384 frames / 213.18 s (~29.95 fps avg),
SHA-256 matches manifest, t=N-1 mapping confirmed within <1 frame.
Display-full coordinates (x2 from working frames) throughout.

Outcome: decision B — motion is observable but occlusion and
two-hand ambiguity prevent trustworthy correspondence (0/6 inspected
proposals show a carried component). Native fps not pursued:
continuity already 1.0 at 10 fps; failures are association failures.

## Gate 2A experiment (Muse vision correspondence, real API)

Tests whether `muse-spark-1.3` (Meta Model API,
`https://api.meta.ai/v1`) recovers correspondence that classical
methods could not. 4 blind cases (machine-selected from frozen Gate 1
output, no labels) + 3 disclosed diagnostic cases; Task A
interpretation + Task B correspondence with abstention allowed. Key
never logged; request ledger records spend only.

```powershell
$venv = 'C:\Users\HP\Documents\Anastylos\.venv\Scripts\python'
$d = 'C:\ANASTYLOS\data\snes-01'
& $venv tools/snes01-benchmark/gate2a_select.py `
  --candidates "$d\gate1-exp1\candidates.json" --out "$d\gate2a\cases_blind.json"
& $venv tools/snes01-benchmark/gate2a_run.py build `
  --cases "$d\gate2a\cases_blind.json" --cases "$d\gate2a\cases_diag.json" `
  --frames-dir "$d\frames-1fps" --manifest "$d\benchmark-v1\source_manifest.json" `
  --prompt-a tools/snes01-benchmark/gate2a_prompt_A.txt `
  --prompt-b tools/snes01-benchmark/gate2a_prompt_B.txt --out "$d\gate2a\pack"
# $key = <MODEL_API_KEY value>; $key | & $venv tools/snes01-benchmark/gate2a_run.py run `
#   --pack "$d\gate2a\pack\evidence_pack.json" --prompt-a ... --prompt-b ... `
#   --model muse-spark-1.3 --out "$d\gate2a\runN" --api-key-stdin
```

Full commands, frozen prompts (`gate2a_prompt_A/B.txt`), evidence
pack, raw responses, contact sheet, and review template live under
`C:\ANASTYLOS\data\snes-01\gate2a\` (pack, run2, final). Run with
`MODEL_API_KEY` or piped `--api-key-stdin`; default budget $3.

Outcome: PIVOT — 7/7 correspondence judgments grounded (2 decisive
rejections incl. Gate 1's best false pair, 5 honest abstentions, 0
fabrications in checked features), but no same-instance recall
demonstrated (blind set contained no known-true pair by design) and
multi-image interpretation is flaky (false-blindness episodes).
Accuracy NOT YET EVALUATED (no sealed GT). See `final/analysis.md`.

## Gate 2B preparation (positive-correspondence challenge, gated)

Status: PREPARED, STOPPED at the operator-annotation gate (no sealed
answer key exists; recognition accuracy NOT YET EVALUATED).

`gate2b_prepare.py` derives 9 verdict-free candidate pairs + 1
technical sanity pair from frozen machine outputs (same-cell-time,
cross-cell, occluded-clear, opposite-face rotation) and writes the
annotation package: `pairs.json` (no verdicts), crops, context,
`annotate_sheet.jpg`, empty `answer_key_TEMPLATE.json`, and
`WORKFLOW.md` (annotate BEFORE any inference on these pairs).

`gate2b_run.py` (model `muse-spark-1.3-contributor`, vision verified
2026-10-27) runs three frozen modes per pair — separate images,
composite A/B panel, single-image visibility — refuses unsealed keys,
and scores against the sealed key with abstention-explicit confusion
counts (sanity pair never scored). Default budget $2.

```powershell
$venv = 'C:\Users\HP\Documents\Anastylos\.venv\Scripts\python'
$d = 'C:\ANASTYLOS\data\snes-01'
# After the operator saves gate2b_answerkey.sealed.json in gate2b/annotate:
# $key | & $venv tools/snes01-benchmark/gate2b_run.py run --pairs "$d\gate2b\annotate\pairs.json" `
#   --key "$d\gate2b\annotate\gate2b_answerkey.sealed.json" --prompt-pair ... --prompt-panel ... `
#   --prompt-single ... --model muse-spark-1.3-contributor --out "$d\gate2b\run1" --api-key-stdin
```

Contributor terms may permit training on prompts: only SNES bench
imagery is sent, never personal data. See
`C:\ANASTYLOS\data\snes-01\gate2b\annotate\` for the package.

## Gate 2B.1 positive-pair recovery (gated)

Status: PREPARED, STOPPED at the annotation gate. No operator
verdicts exist; accuracy NOT YET EVALUATED.

`gate2b_prepare_v2.py` builds `gate2b/annotate_v2/` from disclosed
human-curated pair definitions: 4 candidates (V2-P01 D-pad, V2-P02
rear shell, V2-P03 PCB cable-connected, V2-P04 face-button group),
each with tight crops, 2.2x context, outlined full-frame refs, and
+-2 s clips for P02/P03. Proposed Type 1 (re-identification;
P01-P03 opposite-face, P04 same-face) — operator confirms
evidence_type + verdict (`same/different_physical_instance`,
`ambiguous`, `unverifiable`).

`gate2b_run_v2.py` is the additive experiment extension (frozen
runner untouched): validates the v2 sealed key, excludes
unverifiable pairs from inference and scoring, never pools v1/v2.
Dry-run verified ($0); seal refusal verified (exit 2, pre-network).

Evidence note (2026-10-27): the source mp4's container bytes no
longer match the 18:10 manifest SHA (size/mtime/metadata identical);
decoded pixels proven identical (seek MADs match Phase A to 0.01;
all 213 frames verify). Builder pins an explicit --video-sha256 and
records the discrepancy; manifest re-issue is the owner's decision.

## Schemas (v1)

- `source_manifest.json`: `schema: "snes01-benchmark/manifest@1"`.
- `stable_states.json`: `schema: "snes01-benchmark/stable-states@1"`.
- `event_candidates.json`: `schema: "snes01-benchmark/event-candidates@1"`,
  at most 8 events, each `status: "candidate"`.
- `ground_truth.pending.json`:
  `schema: "snes01-benchmark/ground-truth@1"`, top-level
  `status: "pending"`, every per-event `verdict: null` until a human
  reviewer confirms.

Frame↔time mapping: `frame_N` is video time `t = (N-1)` seconds
(1 fps extraction from `t=0`), verified by side-by-side comparison
at `t ∈ {0, 84, 99, 212}` — see `review.md`. Timestamps in the JSON
files are whole seconds under this mapping.

Coordinates: source-frame pixels, origin top-left, +x right, +y down
(frames are 2160×3840, rotation already applied). No millimetre
values: no calibration is cited for this benchmark.

## Limits

- 1 fps stills cannot resolve sub-second ordering; per-part removal
  order inside fast handling intervals is reported as intervals,
  not sequences.
- Hands/tools occlude the assembly in most frames; identities of
  small parts are inferred from tray deposits, not from in-hand
  observation, and stay pending until reviewed.
- Nothing here is confirmed ground truth. The `.pending.json`
  suffix is load-bearing: no consumer may treat these events as
  verified.
