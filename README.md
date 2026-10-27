# ANASTYLOS

Evidence-backed physical disassembly and reassembly assistance.

Taking apart an electronic assembly is easy. Putting it back together —
every screw, washer, cable, and connector in its original place — is the
hard part. ANASTYLOS is being built to assist that round trip: calibrated
photo evidence of each removal step, an append-only memory of what came
from where, specialized visual recognizers for the parts themselves, and
dependency analysis that proposes a safe reassembly order. Every claim
carries its evidence and provenance; anything uncertain stays explicitly
`ambiguous` or `unknown` until a human operator confirms it.

There is **no unified end-to-end hardware workflow yet**. What exists
today is a set of independently working, integration-tested modules on a
shared evidence philosophy — not one production pipeline, and nothing has
been validated on real hardware.

## What currently works

- **Calibration** ([`calibration/`](calibration/README.md)) — printable
  1:1 millimetre sheets (A3/A4 surfaces, fixture front/back, ChArUco
  board) plus the `calibration-config.json` that gives every other
  module its millimetre frames. The foundation everything else cites.
- **Change detection** ([`change-detection/`](change-detection/README.md)) —
  registers a before/after photo pair on the calibrated sheets and
  reports changed regions with millimetre boxes and SHA-256 provenance.
  Its `result.json` is the core evidence artifact other modules consume.
- **Assembly Memory** ([`assembly-memory/`](assembly-memory/README.md)) —
  append-only disassembly log (`memory.jsonl`): observations imported
  from detection results, tray placements, and "where does this go?"
  queries. Events are never edited; corrections append new events.
- **Specialized visual recognition** — independent, CLI-driven
  recognizers, each with synthetic-only validation: resistors
  ([`resistor-reader/`](resistor-reader/README.md),
  [`smd-reader/`](smd-reader/README.md)), PCB text
  ([`pcb-ocr/`](pcb-ocr/README.md),
  [`component-markings/`](component-markings/README.md)), screws
  ([`screw-reader/`](screw-reader/README.md),
  [`screw-measurement/`](screw-measurement/README.md),
  [`washer-spacer/`](washer-spacer/README.md)), polarity/orientation
  ([`polarity-reader/`](polarity-reader/README.md)), part identity
  ([`component-fingerprints/`](component-fingerprints/README.md),
  [`connector-matching/`](connector-matching/README.md)), tray changes
  ([`tray-counter/`](tray-counter/README.md)), and missing parts
  ([`missing-parts/`](missing-parts/README.md)).
- **Cable and assembly dependency analysis**
  ([`cable-routing/`](cable-routing/README.md)) — cable-path extraction
  from photos plus a read-only assembly-order graph built from memory
  events. Distinguishes observed order from confirmed dependencies; the
  reassembly proposal is never presented as verified.
- **Mobile Capture Companion**
  ([`capture-companion/`](capture-companion/README.md)) — offline-first
  PWA for photographing and documenting components on a phone, exporting
  validated evidence ZIP packages. No cloud, no account, no AI model;
  packages are staged for human review, never auto-imported.

## Experimental or unavailable

- All vision accuracy claims rest on **synthetic fixtures and unit
  tests**. No real-hardware validation has been performed.
- Recognizers are **independent experiments**, not a connected pipeline:
  none writes to Assembly Memory yet (read-only consumers of the log:
  `missing-parts` reconcile and the `cable-routing` graph).
- Capture network sync is a **design document only**
  ([`SYNC_PROTOCOL.md`](capture-companion/SYNC_PROTOCOL.md)); evidence
  moves by file export.
- No Meta Muse / SAM integration, no automated BOM sourcing, no CAD
  reconstruction, no live device synchronization. Provider seams exist
  in code as unimplemented interfaces — see
  [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Setup and verification

Python 3.12, one shared virtualenv for all vision modules:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r calibration\requirements.txt
```

Run each module's suite **from its own directory** (test filenames
collide across modules, so there is no single repo-root runner):

```powershell
cd <module>
..\.venv\Scripts\python -m unittest discover -s tests
```

Plus the synthetic change-detection selftest and the compliance check,
from the repo root:

```powershell
.\.venv\Scripts\python change-detection\detect.py selftest --seed 20261026
py -3.12 tools\compliance\check.py
```

The capture PWA (from [`capture-companion/app`](capture-companion/README.md),
after `npm ci`): `npm test`, `npm run typecheck`, `npm run build`.

On Windows, 4 of the 13 calibration tests need native Cairo
(`libcairo-2.dll`) and poppler (`pdftoppm`) on `PATH` — see
[`calibration/README.md`](calibration/README.md). Everything else runs
without them.

## Where to go next

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — technical source of truth:
  module responsibilities, evidence contracts, coordinate systems,
  status index, and validation gaps.
- [`AGENTS.md`](AGENTS.md) — operating contract for AI coding agents
  (required reading before editing).
- Each module's `README.md` — CLI usage, schemas, evaluation workflows,
  and honest limits.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) — third-party
  attribution (machine-readable
  [`tools/compliance/spdx.json`](tools/compliance/spdx.json)).
  Project licensing itself is deliberately deferred: no root `LICENSE`
  file exists.
