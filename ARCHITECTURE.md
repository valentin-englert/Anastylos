# ANASTYLOS architecture

Evidence-backed visual assistance for electronics disassembly and
reassembly. Every module reports *observations with provenance*, never
bare claims: millimetre coordinates cite their calibration, verdicts
stay `unknown`/`ambiguous` when evidence is weak, and nothing is
confirmed without explicit operator confirmation.

Real-hardware validation is limited to one result: PROTO-01-NOUV E2
(2026-10-28, `docs/PROTO-01-NOUV.md` §10), four correct automatic
source-to-deposit links on a real controller, given an operator-defined
site inventory. All other vision results below come from synthetic
fixtures and unit tests.

## Design rules (observed, keep them)

- Append-only memory: `assembly-memory` events are never edited or
  deleted; corrections append a new event with `supersedes`.
- Read-only analyzers: integrators read detection output and (for
  `missing-parts` and the `cable-routing` graph only) memory logs,
  but never write back to them; they write only to their own
  `--out` dir. `tray-counter` reads detection output only.
- Explicit epistemics: recognizers return `ok` / `ambiguous` /
  `unknown` with reasons; candidates never self-promote.
- Stable tiers: `assembly-memory` and the capture validator are
  standard-library-only; vision modules use the pinned
  OpenCV + NumPy environment (`calibration/requirements.txt`).
- Offline: Python has no network imports; the capture PWA's only
  network use is same-origin service-worker caching of its app shell.
- Licensing is deliberately deferred: no root `LICENSE` file, no
  project license declared anywhere. Agent operating rules live in
  versioned [`AGENTS.md`](AGENTS.md); personal agent notes stay local
  (`AGENTS.local.md`, gitignored).

## System boundaries and subsystems

```text
calibration/  ── sheets + px/mm frames ──> change-detection/
change-detection/ ── result.json + rectified pairs ──> tray-counter/
                                                  ──> missing-parts/
                                                  ──> assembly-memory (import)
readers/ - - planned observations (future; no reader writes to memory yet) - → assembly-memory/
assembly-memory/ + change-detection/ ──> cable-routing/ (read-only graph)
capture-companion/ ── validated .zip packages ──> (human review; never
    auto-imported into Core memory)
```

Five subsystems, one evidence philosophy:

- **Spatial foundation** (`calibration/`, `change-detection/`): printed
  millimetre frames and planar before/after comparison. Produces the
  `result.json` evidence artifact everything downstream cites.
- **Memory core** (`assembly-memory/`): the append-only log of what was
  removed from where. Standard-library-only; the stability anchor.
- **Recognition experiments** (resistor/smd/screw-reader/
  screw-measurement/polarity/washer/pcb-ocr/markings/fingerprints/
  connectors readers): independent single-purpose CLIs. None writes
  to memory yet — see "Actual integration status".
- **Integrators** (`tray-counter/`, `missing-parts/`, `cable-routing/`
  graph): read-only consumers that combine detection output (and, for
  `missing-parts` and the graph, memory logs) into higher-level
  candidates. Never write back.
- **Capture edge** (`capture-companion/`): offline phone PWA plus a
  stdlib-only package validator. Human-reviewed staging only.

```mermaid
flowchart TD
    CAL[calibration/]
    CD[change-detection/]
    MEM[assembly-memory/]
    READERS[recognition readers]
    TRAY[tray-counter/]
    MISS[missing-parts/]
    CAB[cable-routing/]
    CAP[capture-companion/]
    CAL -->|sheets + mm frames| CD
    CD -->|result.json + rectified pairs| TRAY
    CD -->|result.json| MISS
    CD -->|result.json import| MEM
    READERS -.->|planned observations; no writes yet| MEM
    MEM -->|memory.jsonl read-only| CAB
    CD -->|cited regions read-only| CAB
    MEM -->|memory.jsonl read-only| MISS
    CAP -->|validated .zip| REV[human review]
    REV -.->|manual step; never auto-imported| MEM
```

## Module / status index

Status definitions (hygiene gate 01):

- `implemented`: code and relevant isolated tests exist.
- `integrated`: code is part of `main` (or this hygiene branch's
  inherited `main` history) and cross-module verification has been
  performed where applicable.
- `hardware-validated`: the capability passed documented tests using
  real physical hardware and genuine images. Synthetic tests never
  count. Nothing below has this status.

Integration baseline: five feature branches
(`feat/capture-companion`, `feat/cable-routing`, `feat/missing-parts`,
`feat/polarity`, `feat/screw-measurement`) merged via
`integrate/feature-batch` into `main` at `20be578`
(`fix(integration): stabilize feature-batch verification gates`).
Verification at that commit: 750 Python module/validator tests (746
passed; 1 failure + 3 errors — all 4 are calibration tests needing
native Cairo/poppler on Windows, see limitation 1), plus the
5-test compliance-checker self-suite (755 Python test methods in
total when the checker suite is counted); 66/66 capture frontend unit tests
executed; TypeScript compilation (`tsc`) and the production build
(`vite build`) also clean — those two are static checks, not test
executions. The compliance check reported 0 errors with 8 open
review items (8 SPDX annotations, grouped into 6 narrative items in
`THIRD_PARTY_NOTICES.md`). No real-hardware validation reported.
This hygiene branch is docs-only, so it inherits those results;
test counts below are per-module
(`python -m unittest discover -s tests` from the module directory
unless noted) and match the current tree's test methods exactly.

| Module | Responsibility | Entry point | Status | Tests |
|---|---|---|---|---|
| `calibration/` | printable sheets, ChArUco, px/mm frames | `generate.py` | integrated | 13 run, 9 pass (1 failure + 3 errors need native Cairo/poppler; known Windows gap) |
| `change-detection/` | planar before/after change check | `detect.py` | integrated | 3 pass + synthetic selftest 16/16 |
| `assembly-memory/` | append-only disassembly memory, where-goes queries | `memory.py` | integrated | 28 pass |
| `resistor-reader/` | resistor color-code reader (experimental) | `read.py` | integrated | 30 pass |
| `tray-counter/` | tray addition/removal candidates | `count.py` | integrated | 14 pass |
| `pcb-ocr/` | silkscreen reference-designator extraction (needs OCR engine) | `ocr.py` | integrated | 31 pass |
| `smd-reader/` | SMD marking decoder (reuses pcb-ocr) | `smd.py` | integrated | 31 pass |
| `screw-reader/` | screw-head drive classifier | `screw.py` | integrated | 45 pass |
| `component-markings/` | component marking recognition | `markings.py` | integrated | 54 pass |
| `component-fingerprints/` | visual fingerprint enroll/match gallery | `fingerprints.py` | integrated | 61 pass |
| `connector-matching/` | plug/socket mating-geometry match | `connectors.py` | integrated | 68 pass |
| `screw-measurement/` | calibrated screw geometry (top/side) | `measure.py` | integrated | 81 pass (incl. 3 `_hex_widths` regression tests) |
| `polarity-reader/` | polarity/orientation markers + rules | `polarity.py` | integrated | 60 pass |
| `washer-spacer/` | washer/spacer recognition + classifier | `washer.py` | integrated | 82 pass |
| `missing-parts/` | missing/displaced-part candidates | `missing.py` | integrated | 81 pass |
| `cable-routing/` | cable paths + assembly-order graph | `cable.py` | integrated | 53 pass |
| `capture-companion/` | offline-first capture PWA + validator | app + `python/anastylos_capture_validate.py` | integrated | TS 66 pass, `tsc` clean, `vite build` ok; validator 22 pass |
| `tools/compliance/` | third-party inventory checker (SPDX) | `check.py` | integrated | 5 pass; `check.py`: 0 errors, 11 open reviews |
| `sam-tracking/` | SAM 3.1 segmentation/tracking eval (cloud-isolated experiment) | `sam.py` | implemented | 55 pass; live runs ledgered, none claimed yet |
| `tools/retrospective-custody/` | offline inspection of cached tool-mask endpoints in a video clip (diagnostic) | `retrospective_custody.py` | implemented, experimental geometry gate only | 8 pass; cached real-clip execution; physical contact, removals, deposits and correspondence are not implemented in this tool |
| `tools/retrospective-custody/matcher/` | deposit-triggered source-to-tray correspondence over supplied `anastylos-observation-v1` events (see "Correspondence contracts and owner decisions") | `match.py`, `validate.py` | implemented, synthetic only | 22 pass; no real observation file has been consumed; owner decisions V1, V2, V4, V5 not yet implemented |
| `assembly-reconstruction/` | read-only Assembly Memory → Assembly State v1 → exploded-view SVG | `reconstruct.py` | integrated | 54 pass; real replay on hardware-02 and PROTO-01-NOUV E2 memory |
| `custody-ledger/` | video custody: adaptive source discovery, zone observation with colour-free hand mask, temporal/elimination/hand-order linker | `custody.py` | integrated (`eb9744a9`, observer 1.7.0, `custody-config.v15.json`) | 94 pass; development footage only (E2, FULL-01, SNES-01); frozen v1.1/v1.2 stay on their branches for E2R/E3-A |
| `tools/session/` | one-command phase run (video → custody → numbered parts → evidence → memory → open questions); answer package/apply/remap | `session.py`, `answers.py` | integrated | 23 pass; FULL-01 v20 rebuilt and reproduced |
| `tools/guide/` | guide (Muse) package from memory, rules, grounding check | `package.py`, `check.py` | integrated | 9 pass |
| `tools/reassembly/` | return checks of a reassembly take against declared plans | `returns.py` | integrated | 11 pass |

Hardware-validated: none. Every vision claim above rests on synthetic
fixtures; see each module README for its stated validation scope.

## Stable data interfaces

- Detection `result.json`: `status` (`no_change`, `single_change`,
  `multiple_changes`, `registration_invalid`, `view_mismatch`,
  `input_error`), `view`, `rectified.px_per_mm`, `regions[]` with
  `label`, `bbox_mm`, `centroid_mm`. Every file embeds
  `harness_config_sha256` (canonical hash of the parameter set) and
  `kit_config_sha256` (bytes hash of the calibration config); the
  per-file `result_sha256` is NOT embedded — it is computed
  downstream: `assembly-memory import` and `missing-parts` hash the
  file bytes at read time, the `cable-routing` graph reuses the
  hash recorded in memory evidence, and `tray-counter` cites file
  paths without hashing. Consumed verbatim by `tray-counter`,
  `missing-parts`, `cable-routing`, and `assembly-memory import`.
  `registration_invalid` covers unconfirmed views, cross-group
  views, and failed registration; `view_mismatch` only covers a
  before/after view change within one view group (see
  `change-detection/README.md` for the exact conditions).
- Memory log: `<session>/memory.jsonl`, one JSON event per line
  (`observation` / `tray_link`), SHA-256 file provenance, coordinates
  stored verbatim and never transformed.
- Memory event contracts: `observation` carries `source_view`,
  `bbox_mm`, `centroid_mm`, and an evidence reference (`result_json`,
  `pair_id`, `region`, plus the file's SHA-256 when on disk) or a
  manual basis; `tray_link` binds an observation to a tray cell with
  the tray snapshot (`sheet_id`, `number`, `rect_frame_mm`) resolved
  from `calibration/calibration-config.json` at record time, plus either an
  evidence basis or a human confirmation — anything else is rejected
  and nothing is written. An evidence basis cites what was observed (the
  cell change); a link that was *concluded* (automatic custody links)
  also carries `inference` {`relation`, `inferred_by`, `producer`,
  `assumptions`} (PROV-01, 2026-10-29, additive; older links lack it and
  readers must treat their correspondence as concluded, not observed).
  Queries answer `located` / `ambiguous` /
  `unknown` / `unassigned`, never a bare claim.
- Calibration JSON: two key spellings share one physical meaning
  (image pixels per millimetre) and are NOT interchangeable across
  modules — `pixels_per_mm` (`screw-measurement`, which adds
  `pixels_per_mm_std` and `frame`, plus `cable-routing`,
  `polarity-reader`, `washer-spacer`)
  versus `px_per_mm` (`pcb-ocr`, `component-fingerprints`,
  `connector-matching`, `missing-parts`, plus the rectified
  detection output itself). Each module validates its own input.
  Do not invent a unified schema without a migration.
- Capture package (schema v1, `capture-companion/SCHEMA.md`):
  manifest + assets zip; mirrored TypeScript/Python validators must
  keep agreeing (round-trip test enforces this). Every interpreted
  field carries an epistemic state (`observed` / `interpreted` /
  `suggested` / `confirmed` / `unknown`); only the operator can set
  `confirmed`, and quantities are never inferred from photo counts.

## Correspondence contracts and owner decisions

Canonical contract files (owner decision V1, 2026-10-28):

- `tools/retrospective-custody/matcher/SCHEMA.md` defines
  `anastylos-observation-v1`, `anastylos-sync-v1` and
  `anastylos-correspondence-v1`, enforced by `validate.py` and
  `match.py`. It is the only valid definition of these schema ids.
- `snes-02/observation-contract-v1.md` is WITHDRAWN. The quarantined
  `snes-02/observations/snes02-observations.v1a-WITHDRAWN.json` must
  not be consumed (it is also not valid JSON).
- `snes-02/sync-manifest.json` (`snes02-sync-manifest-v1`) is a dataset
  provenance record, not a matcher input; see V5.

Owner decisions of 2026-10-28. These are binding decisions; the
status column states what the code does today.

| ID | Decision | Implementation status |
|---|---|---|
| V1 | `matcher/SCHEMA.md` is canonical. The withdrawn SNES-02 contract's provenance fields (`evidence.frames[{frame, pts_seconds, role}]`, `evidence.basis_event_ids`) become additive optional fields. | not implemented |
| V2 | Human and automatic producers are both permitted and must be identified per event (additive optional `producer: {kind: automatic \| human, ref, config_sha256}`). A result that cites human-produced `site_ref`, `verified` or `target_ref` is operator-assisted and never counts as an autonomous success. `verified` means observed and confirmed by the producer's documented criterion. | not implemented |
| V3 | Existing occlusion behavior stays: any observed material gap in the transit window yields `AMBIGUOUS`. A narrower gap rule may be evaluated only on physical evidence (PROTO-01 / adversarial captures) with preregistered criteria. | current code is the decision |
| V4 | Fail-closed inventory guard: additive optional `site_inventory: {complete, basis, pose_epoch}`; if absent or not complete, a would-be `PROPOSED` becomes `AMBIGUOUS` with reason `INVENTORY_INCOMPLETE`. A regression test reproduces adversarial case P2 (uninventoried true source plus a later absence of an inventoried site). | not implemented |
| V5 | Canonical sync adapter: dataset manifests are translated into `anastylos-sync-v1`; the original manifest stays and is cited with its hash in `basis_ref`; uncertainty is carried as offset bounds (SNES-02: `cam1-pts` → `cam2-pts`, 698.96–838.96 ms). | not implemented |
| V6 | Loose-part and multi-agent removal cases (e.g. SNES-02 S5: loosened by tool, dropped, deposited by fingers) remain `UNKNOWN` for the MVP; no new event kind. | current code is the decision |
| V7 | Transport or count evidence as an alternative proof of removal is deferred until after PROTO-01. | current code is the decision |

Owner requirements of 2026-10-29 (Northstar P2 adaptive discovery, P3 hand presence and
occlusion). Binding for every custody observer version after v1.2; v1.1
(E2R, preregistered) stays frozen.

| ID | Requirement | Implementation status |
|---|---|---|
| H1 | Hand presence, visual occlusion and component state are separate signals. A static object is never classified as a hand by colour alone; detection uses a hand-free reference, temporal entry and tracking, geometry and optionally a hand-landmark model. Different skin tones, changing light and gloves must be supported; an optional per-user hand-colour calibration may supplement but never be required. Insufficient evidence stays occluded/unknown, and a hand that stops moving never becomes a component. No device- or colour-specific exceptions. Demographic robustness is claimed only after testing on varied hands. Test plan and model feasibility: [`docs/P3-HAND-OCCLUSION.md`](docs/P3-HAND-OCCLUSION.md). | implemented in `main` (merged `eb9744a9` from `feat/custody-v1.3` @ `45d7440d`), observer 1.7.0 / hands 0.2.0: no skin-colour test remains; one colour-free hand mask (busy components against a hand-free reference in raw colour channels, reaching the sheet border or the edge of the camera's view) drives hand order, the zone-intrusion gate for clean samples and the hand exclusion of device alignment; skin-colour configs are refused. Frozen observers v1.0–v1.2 (E2R, E3-A pin) still use the skin-colour test. Limits: a resting hand over a background that looks the same in every channel is only partly found; raw channels assume fixed exposure. Synthetic tests cover light skin, dark skin and a blue glove; no test on varied real hands yet |

| H2 | Spatial discovery is adaptive: grids or other sampling only propose candidate changes; discovered sources carry stable ids, mask geometry in device-relative coordinates, confidence and provenance; neighbouring proposals merge when they describe one persistent change and split only when independent changes are shown; geometry refinements are appended, never rewriting earlier evidence; uncertain segmentation or correspondence stays `ambiguous`/`unknown`. Assessment and next experiment: [`docs/P2-ADAPTIVE-DISCOVERY.md`](docs/P2-ADAPTIVE-DISCOVERY.md). | partial, in `main` since `eb9744a9`: proposals 0.4.0 (commit `27e7f4ed`) discover sources from busy components, cluster them into `SRC-n` with device pose epochs and candidate features; the grid only proposes. Development data only (E2, FULL-01). Frozen v1.2 (E3-A) uses the fixed grid |

| H3 | Clarification is asked in the operator's terms and at the moment it is needed. A question names the phase in words and anchors the unknown part on known neighbouring removals ("Was hast du nach dem Steuerkreuz herausgenommen und in Zelle 3 gelegt?"), describes places relative to the device or known parts rather than in mm, and offers time windows only as supporting detail. In a live session the system asks as soon as it cannot assign a removal, while the part is still at hand; the operator must never have to scrub a recording to answer. A very short change (for example under 0.5 s) is first treated as a likely hand or tool, not as a part. Answers stay `operator_declared` and never count as automatic evidence. | not implemented: `tools/session/answers.py` asks offline after the recording, per question id with mm positions and time windows; owner feedback and the FULL-01 answer round: [`docs/FULL-01.md`](docs/FULL-01.md) §21 |

Owner decision 2026-10-29, **implemented in `assembly-memory`** (readers opt in separately): additive Assembly
Memory events for a complete disassembly (needed by FULL-01). Existing
`observation` and `tray_link` stay unchanged; readers ignore unknown types
until they opt in.

| Proposed event | Fields | Why |
|---|---|---|
| `declared_removal` | `part_label` (operator's words), `source` (free text or `site_ref`), `phase`, `t_s` (optional), `declared_by`, `evidence_note`; no mm geometry required | parts without image evidence (back shell, PCB, membranes) |
| `storage_link` | target `observation_id` or `declared_removal_id`, `location` (`cell-N` or free text such as `OUT-A`), `basis` (`evidence` / `human`) | storage outside tray cells |
| `compatible_group` | `members` (ids), `basis` (`operator_statement` / `measured`), `measurement` (optional) | identical screws / nubs: interchangeable, no per-part identity |
| `phase` | `label`, `pose` note, `starts_after` (event id or null) | ordering across pose changes (P1 → P2 → P3) |

Every new event names its basis; operator-declared content is never
counted as automatic evidence.

Implementation order: this section, then `SCHEMA.md`, then
`validate.py` / `match.py` with tests, then producers. No observation
file in the canonical format may be produced before V1, V2 and V4 are
implemented. A contract and its consumers are never changed in one
edit.

## Coordinate systems and calibration semantics

The experimental `tools/retrospective-custody/` output
(`retrospective-custody-origin-trace-v1`) is a diagnostic artifact, not a
new Assembly Memory event. It verifies the supplied clip, cached transcript
and consumed mask hashes, then records mask-endpoint geometry, decoded
clip-local frame indices/PTS and a bounded set of hashed image crops.
An optional fixed work-area ROI is disclosed operator setup; it is not
a supplied removal event or confirmed origin. A mask endpoint does not
establish a tool tip, contact, removal or physical identity.

This tool uses rotated display pixels (origin top-left, +x right,
+y down), never millimetres. It records the decoder's rotation metadata
and leaves the original video's timeline mapping unverified rather than
adding the nominal clip-cut offset. Source presence/absence, deposit
detection and retrospective correspondence are explicitly
`NOT_IMPLEMENTED` in this tool; their counts are null. (The separate
`matcher/` submodule decides correspondence from supplied observations;
it does not detect them.) It has no network calls, no
Assembly Memory writes and no downstream Core consumer.

Several coordinate kinds exist; every measurement states which one
it uses. There is no single universal convention — the statements
below are per kind:

- Source image pixels: origin top-left, +x right, +y down. The
  modules that name a pixel convention (`cable-routing`
  `coordinate_convention`, `polarity-reader`, `washer-spacer`) all
  use this one; the remaining crop readers report source-frame
  pixels or boxes without restating the origin. Angles, where
  reported (`polarity-reader`, `washer-spacer`), run clockwise
  from +x in degrees over [0, 360).
- Rectified detection millimetres: `change-detection` warps each
  pair onto a millimetre grid for one named view (`rectified.view`,
  `rectified.px_per_mm`, `rectified.origin_frame_mm`). Frame axes
  are x right, y down per
  `calibration/calibration-config.json`; the named frame origins
  are the top-left of marker 0 (`surface_a4`), 10 (`surface_a3`),
  20 (`fixture_front`), and 30 (`fixture_back`).
- Operator-supplied calibration millimetres: readers convert
  pixels to mm only from a validated `--frame-meta` /
  `--calibration` file with provenance, using their own spelling
  (`pixels_per_mm` vs `px_per_mm` — see "Stable data interfaces"
  for the per-module inventory).
- Connector-local housing coordinates are the exception: contact,
  keying, and latch positions live in a housing-fixed canonical
  frame (rotation-invariant), not in image pixels; millimetre
  pitch additionally needs a frontal mating face and valid
  `px_per_mm`.
- View identifiers double as frame identifiers for the four
  detection sheets (`surface_a3`, `surface_a4`, `fixture_front`,
  `fixture_back`). `charuco_board` is calibration-only: its sheet
  carries no ArUco registration markers, the detector reads only
  `DICT_5X5_100`, and the harness `view_groups` list just the
  four sheets — so detection never reports it as a view.
  `screw-measurement` additionally accepts `custom:<name>` frames.
- Front and back share no transform.
- Homographies are exact on the paper plane only; controller/part
  height is unmeasured, so off-plane content suffers uncorrected
  parallax. The camera must stay fixed between before/after shots.
- Millimetres appear in a result only with cited, validated
  calibration (frozen `change-detection/harness-config.json` + kit
  SHA-256 for detection; `--frame-meta` / `--calibration` files
  with provenance elsewhere). Without it: pixels plus an explicit
  error or null — never silently converted pixels. Never change
  these semantics without a migration and a version bump of the
  affected contract.

## Current dependency relationships

Code-level (Python imports across module boundaries) — only these:

- `cable-routing` → `assembly-memory` (`graph.py` lazily imports
  `anastylos_memory.store` for read-only log loading).
- `smd-reader` → `pcb-ocr` (`anastylos_pcb.backends`,
  `anastylos_pcb.pipeline` — public reuse, no second OCR engine).
- `component-markings` → `pcb-ocr` (same seam).
- `assembly-memory/tests` → `change-detection` (integration test
  drives the synthetic harness; test-only).

File-contract coupling (no imports; consumers read produced files):

- `change-detection` reads `calibration/calibration-config.json` via
  the pinned path + SHA-256 in `change-detection/harness-config.json`.
- `assembly-memory` reads the kit config for tray-cell resolution
  and imports detection `result.json` files verbatim.
- `tray-counter` reads detection `result.json` files (+ rectified
  images) only; `missing-parts` reads detection output plus
  `memory.jsonl` (reconcile); the `cable-routing` graph reads
  `memory.jsonl` plus the cited detection regions. All three read
  verbatim; none imports producer code.

No other cross-module imports exist (verified by search). In
particular `missing-parts` and `tray-counter` consume files only,
`connector-matching` and `component-fingerprints` share patterns but
no code, and the capture validator is dependency-free. Keep it that
way: new coupling goes through versioned file contracts, not imports.

## Detection and interpretation flow

1. `calibration/` prints the physical reference; its config pins
   marker geometry and tray zones with a SHA-256 identity.
2. `change-detection/` registers before/after pairs onto that frame,
   writes rectified images and `result.json` (regions in mm,
   embedding the harness canonical hash and the kit bytes hash).
   The parameter set is frozen by canonical hash: `canonical_sha256`
   over the parsed harness JSON (`json.dumps` with sorted keys,
   compact separators, UTF-8 — see
   `change-detection/anastylos_diff/config.py`).
3. Recognition readers classify tight crops independently
   (`ok`/`ambiguous`/`unknown` + reasons); all stop at their own
   `--out` dir today and none writes to memory.
4. `assembly-memory import` turns cited `result.json` regions into
   append-only observations (hashing each file's bytes into
   `result_sha256` evidence at import time); operators add tray
   links and manual observations with confirmation.
5. Read-only integrators derive candidates (tray add/remove, missing
   parts, assembly-order graph) without mutating their inputs.
6. Anything promoted toward "confirmed" requires an operator
   confirmation artifact — see below. Capture packages wait in human
   review and are never auto-imported.

## Operator confirmation requirements

These fields/transitions require explicit human confirmation and must
never be set by analysis code:

- `assembly-memory` `tray_link` without an evidence basis needs
  `--confirmed-by`; `where` answers never invent a location.
- `missing-parts` `confirmed_missing` arises only from an external
  confirmation file, never from images.
- `cable-routing` graph `confirmed` edges need a confirmations file
  with `confirmed_by` per edge; unknown ids are reported, not added.
- `tray-counter` `proposed_links` always carry
  `requires_confirmation: true`.
- Capture `confirmed` category/quantity/review states are set only by
  operator action in the inspector; identification candidates stay
  `suggested`.
- `measured_print_scale`, `--mapping` files, and frame-metadata
  scales are operator-owned evidence: wrong input, wrong output, and
  the module must not second-guess them.

## Offline and future AI-provider boundaries

- Python is offline except two explicit, isolated carve-outs:
  `sam-tracking/anastylos_sam/client.py` (outbound, stdlib `urllib`
  only, imported lazily by `sam.py` live paths alone) and
  `capture-companion/receiver/anastylos_capture_receiver.py` (inbound
  stdlib `http.server` + `ssl` workstation receiver for capture
  packages). Verified by import search 2026-10-28: no other Python
  module outside `.venv/` imports HTTP, socket or TLS modules. The
  PWA caches its app shell and, since the M2 sync prototype, transfers
  packages to a paired receiver (`capture-companion/SYNC_PROTOCOL.md`).
- Approved, not yet implemented (owner, 2026-10-28): a third outbound
  carve-out for an advisory Muse narration of custody runs
  (`muse-spark-1.3-contributor`, effort max; budget at most 30 calls,
  about 0.25 M input / 0.6 M output tokens, text only, no images). It
  will live only in `tools/narration/`, be imported by no module, and
  write only beside its run. The offline parts exist: `prepare.py`
  builds a hashed input bundle and prompt, `check.py` rejects any
  narration citing ids, cells, sites or times absent from the bundle,
  claiming confirmation or identity, or stating facts without
  citation. A narration never changes a verdict and is never written
  to memory. This contributor tier lets the provider use inputs and
  outputs for training; only the derived event bundle is sent.
- Future models plug in behind narrow, documented seams — all
  currently unimplemented, advisory-only by contract:
  - `missing-parts`: `run_comparison(..., aligner, segmenter)`
    callables; `screw-measurement`: `analyze(..., segmenter)`;
    `screw-reader`, `polarity-reader`, `washer-spacer`:
    `segmenter`/`ranker`/`detector` callables.
  - `component-fingerprints`: `EmbeddingProvider` vectors (cosine
    similarity, named + versioned); `connector-matching`:
    `AssistantProvider` advisory notes; `component-markings`:
    `assistant` callable whose notes are recorded verbatim.
  - Capture: provider-neutral `IdentificationProvider` interface
    (`app/src/lib/identify.ts`); the registry is empty and the UI
    labels identification as future functionality.
- Seam contract: providers may add evidence or notes, never change a
  verdict, never confirm an identity, never call the network from
  Python. SAM exists only as the isolated `sam-tracking/` experiment
  with no pipeline consumer. No Meta Muse integration (the Gate 2A
  client lives outside `main`), automated BOM sourcing, CAD
  reconstruction, or live sync exists — do not document any as
  present.

## Actual integration status

What "integrated" concretely means today — and does not:

- Does mean: all modules merged to `main`, shared `.venv`
  environment, per-module suites green (except the 4 known
  Cairo/poppler calibration cases), cross-module file contracts
  exercised (`assembly-memory` ⇄ `change-detection` integration
  test; capture TS↔Python round-trip test), compliance inventory
  consistent.
- Does not mean: a single runnable pipeline. There is no top-level
  orchestrator, no shared runner, and no end-to-end real-photo run.
  All recognizers write only to their own `--out` dir and none
  writes to memory; wiring them into memory import is future work
  requiring per-module contract decisions — not a bulk edit. The
  only producer paths into `memory.jsonl` today are
  `assembly-memory import` (change-detection `result.json`) and the
  manual `record observation` / `record tray-link` commands; the
  only read-only consumers of the log are `missing-parts`
  reconcile, the `cable-routing` graph and `assembly-reconstruction`.
- `custody-ledger/` (real-video observer, adaptive discovery, linker)
  is in `main` since `eb9744a9` (2026-10-29), byte-identical to
  `feat/custody-v1.3` @ `45d7440d`. Frozen versions stay on their
  branches and worktrees: v1.1 `feat/custody-ledger` (E2R), v1.2
  `feat/custody-v1.2` @ `e057cf4f` (E3-A pin). Its module-local schemas
  are not `anastylos-observation-v1`. Active FULL-01 development memory:
  `C:\ANASTYLOS\data\session-runs-v20\FULL-01` (pointer
  `C:\ANASTYLOS\data\active-memory.json`; v18 kept unchanged).
- `snes-02/` (commit `4d1d5b3a`) is a research checkpoint, not a
  module: a dual-camera observability study in which the overhead
  view localized 0/5 screw sites and the oblique view 4/5, all five
  screws went into one shared tray cell, and no camera observed a
  complete custody event. See `snes-02/OBSERVABILITY_REPORT.md`.

## Reproducible test commands

Python (from the repo root; `.venv` per `calibration/README.md`):

```powershell
cd <module>; ..\.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python change-detection/detect.py selftest --seed 20261026
py -3.12 tools/compliance/check.py   # stdlib-only; no venv needed
```

Capture PWA (from `capture-companion/app`, after `npm ci`):

```powershell
npm test        # vitest
npm run typecheck
npm run build   # vite production bundle -> app/dist/
```

Run each Python suite from its own module directory: several suites
share test file names (`test_eval.py`, `test_vision.py`), so a single
repo-root pytest collection collides. Per-module unittest discovery is
the documented runner.

Test methodology: deterministic synthetic fixtures (fixed seeds,
hand-built detection pairs, stub OCR backends) prove pipeline
self-consistency and contract conformance only — never real-hardware
accuracy. Real-photo evaluation harnesses (`eval` subcommands with
hand labels) exist per module but no labelled real-photo set exists
yet. Known validation gaps: zero real images scored anywhere; capture
camera/install/offline behavior verified by inspection + build checks,
not on devices; calibration print masters unmeasured
(`measured_print_scale` unset).

## Integration limitations and remaining work

1. Calibration kit tests (4 of 13: 1 failure + 3 errors) need native
   Windows components outside `requirements.txt`: `libcairo-2.dll` on
   `PATH` (PDF/PNG generation) and poppler `pdftoppm` on `PATH`
   (test-only PDF rasterizing). Product code degrades gracefully to
   SVG with a warning. Setup is documented in
   `calibration/README.md`; committed print/preview fixtures are
   intact and consistent.
2. `pixels_per_mm` vs `px_per_mm` key spellings differ by module
   (per-module inventory under "Stable data interfaces"). No live
   incompatibility; unify only with a migration.
3. npm transitive closure licenses unverified (compliance review
   item); direct capture devDeps are MIT/Apache-2.0 per installed
   metadata.
4. Real-hardware runs are development data only: PROTO-01-NOUV E2
   (frozen custody v1.1, one take, operator-defined inventory) and
   FULL-01 (custody 1.7.0, owner answers). No independent validation;
   E3-A is preregistered but not recorded. No other module has a
   real-hardware result.
5. Current priority: real-hardware validation and end-to-end demo
   readiness — print and measure the calibration sheets, photograph a
   real disassembly, score the `eval` harnesses on labelled real
   photos, and run the capture manual checklist on a physical phone
   (see `capture-companion/README.md`). The first physically verified
   source-to-deposit correspondence is the critical path: a protocol
   capture (device at rest, one part per cell, hands-off pauses)
   evaluated by the frozen custody observer against sealed physical
   ground truth (`docs/PROTO-01.md`).
