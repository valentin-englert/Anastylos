# ANASTYLOS

> **Moved:** modules that are not part of the working pipeline, including the recognition readers and the microscopy work, now live in [`not-in-pipeline/`](not-in-pipeline/README.md). Older paths in this document refer to that folder.

Evidence-grounded, human-in-the-loop active vision for physical
electronics disassembly and reassembly.

Taking an electronic assembly apart is easy. Putting it back together —
every screw, washer, cable, and connector in its original place — is the
hard part. ANASTYLOS assists that round trip: calibrated photo evidence
of each removal step, an append-only memory of what came from where,
specialized visual recognizers for the parts themselves, and dependency
analysis that proposes a safe reassembly order.

## Approach

Three rules govern every module:

- **Evidence with provenance, never bare claims.** Millimetre
  coordinates cite their calibration; transfers carry SHA-256 hashes.
- **Explicit uncertainty.** Anything weakly supported stays
  `ambiguous` or `unknown`.
- **The human confirms.** No detection, model proposal, or planner
  output promotes itself to a confirmed fact. Operator confirmation is
  the only promotion path.

[`ARCHITECTURE.md`](ARCHITECTURE.md) is the technical source of truth:
contracts, coordinate systems, module status, and validation gaps.

## What exists today

### Capture: phone, guidance, and physical validation

- **Native Android app** ([`capture-companion/android/`](capture-companion/android/README.md))
  — Kotlin/Compose camera companion with QR-bootstrapped pairing and
  SHA-256 evidence provenance. Its **guided auto-shutter** was
  physically validated on a Xiaomi Redmi Note 10 Pro (M2101K6G):
  five separate armings, one automatic JPEG each, plus passing
  off-target, tracking-lost, and invalid-calibration negative tests
  ([`GUIDE-03`](docs/integration/GUIDE-03.md), frozen).
- **Device calibration** — measured profiles for Xiaomi M2101K6G and
  Samsung SM-A536B, validated on hardware
  ([`CAL-01`](docs/integration/CAL-01.md),
  [`CAMERA-01`](docs/integration/CAMERA-01.md),
  [`CAMERA-02`](docs/integration/CAMERA-02.md)).
  Real-camera Receiver transfer was verified on the Samsung
  ([`HARDWARE-03`](docs/integration/HARDWARE-03.md)).
- **Offline PWA + validator** ([`capture-companion/`](capture-companion/README.md))
  — phone capture with a stdlib-only package validator and schema
  contract. No cloud, no account; packages stage for human review.

### Receiver and Core ingestion

- **Authenticated Receiver** ([`capture-companion/receiver/`](capture-companion/receiver/README.md))
  — TLS + QR pairing + token-gated upload; hash-identical transfer
  verified against a real phone. Control journal and guided routes
  included; 185 tests pass.
- **Core Evidence Index** ([`tools/core-ingest/`](tools/core-ingest/README.md))
  — ingests Receiver-staged packages into a content-addressed,
  hash-verified JPEG store with session/observation lookup. Deliberately
  never writes Assembly Memory: a photo is scene evidence, not proof a
  part was removed.

### Memory, recognition, and dependencies

- **Assembly Memory** ([`assembly-memory/`](assembly-memory/README.md)) —
  the append-only disassembly log: observations, tray placements, and
  "where does this go?" queries. Events are never edited; corrections
  append new events.
- **Specialized recognizers** — independent CLI-driven readers, each
  with unit tests: resistors ([`resistor-reader/`](resistor-reader/README.md),
  [`smd-reader/`](smd-reader/README.md)), PCB text ([`pcb-ocr/`](pcb-ocr/README.md),
  [`component-markings/`](component-markings/README.md)), screws
  ([`screw-reader/`](screw-reader/README.md),
  [`screw-measurement/`](screw-measurement/README.md),
  [`washer-spacer/`](washer-spacer/README.md)),
  polarity ([`polarity-reader/`](polarity-reader/README.md)), identity
  ([`component-fingerprints/`](component-fingerprints/README.md),
  [`connector-matching/`](connector-matching/README.md)), trays
  ([`tray-counter/`](tray-counter/README.md)), missing parts
  ([`missing-parts/`](missing-parts/README.md)), and cable/assembly
  dependencies ([`cable-routing/`](cable-routing/README.md)).
  Most vision accuracy claims still rest on synthetic fixtures.
- **Video custody** ([`custody-ledger/`](custody-ledger/README.md)) —
  fixed-camera zone observation with colour-free hand masking and a
  removal-to-deposit linker. Integrated; evaluated on development
  footage only.
- **Assembly reconstruction** ([`assembly-reconstruction/`](assembly-reconstruction/README.md)) —
  read-only projection of memory into assembly state + exploded-view
  SVG, replayed on real hardware-02 and PROTO-01 memory. CAD worker is
  isolated with licensing and FreeCAD gates still open; mesh
  reconstruction is synthetic-only with a known retained failure.

### Microscopy

- **Microscopy Core + Pi node** ([`anastylos-microscopy/`](anastylos-microscopy/README.md)) —
  offline file-based orchestration over the existing readers, plus a
  Raspberry Pi capture node whose basic transport and capture behavior
  was physically observed. Recognition accuracy remains unverified;
  see the module README for limits and evidence.

### Meta integration status

- **SAM 3.1 tracking adapter** ([`sam-tracking/`](sam-tracking/README.md),
  experimental) — cloud-isolated by design: the only Python module
  allowed near the network, never imported by the offline core.
  Segmentation/tracking evaluation with recorded transcripts that
  replay byte-identically offline. No production integration; no part
  identity is ever derived from masks alone.
- **Muse proposer seam** ([`tools/live/providers/`](tools/live/providers/README.md)) —
  stdlib-only client for the Meta Model API (`muse-spark-1.3`
  family); the model proposes, code re-validates, the operator
  confirms. Key via environment only, never logged. Recorded fixtures
  from a real smoke run ship with the tests; live model-driven
  behavior beyond that is pending validation.
- **Live coordinator** ([`tools/live/`](tools/live/README.md)) —
  deterministic clarification coordinator with gated advisory hooks
  and a versioned journal contract. L2 benefit unproven (measured
  below the rules baseline, deliberately not deployed).
- The M0–M2 agent orchestrator (`tools/agent/`) lives on the separate
  `integration/agent-v1` branch and is **not** part of main.

## Limitations (honest)

- There is **no complete model-driven Active-Vision loop**. Capture,
  memory, recognition, guidance, and planning exist as verified pieces
  with documented seams — not one autonomous pipeline.
- Recognition accuracy is largely synthetic-only; broad real-hardware
  accuracy is unverified.
- Android unit tests: 443/461 pass; 18 `GuidanceViewModelTest`
  failures are a known pre-existing gap at the frozen MVP checkpoint
  (see [`INTEGRATION-STATUS.md`](INTEGRATION-STATUS.md)). Lint and
  assembly are clean.
- One Assembly Memory test module errors without the pinned `cv2`
  environment; 4 calibration tests need native Cairo/poppler on
  Windows. Details in [`INTEGRATION-STATUS.md`](INTEGRATION-STATUS.md).

## Development provenance

ANASTYLOS foundations — Assembly Memory, the calibration kit, the
recognizer suite, and the capture PWA — predate the hackathon. Work
developed during the event (Android guided capture and its physical
validation, the authenticated Receiver and Core ingestion, live/Meta
provider integration, custody advances, microscopy, CAD/mesh
experiments) is visible as such in the preserved Git history:
timestamps, ancestry, and authorship are intact and no history has been
rewritten. Older components are not presented as new work.

## Setup and verification

Python 3.12, one shared virtualenv for the vision modules:

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

Android (from `capture-companion/android/`, JDK 21, see the module
README for the pinned toolchain):

```powershell
.\gradlew.bat :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
```

The capture PWA (from `capture-companion/app`, after `npm ci`):
`npm test`, `npm run typecheck`, `npm run build`.

On Windows, 4 of the 13 calibration tests need native Cairo
(`libcairo-2.dll`) and poppler (`pdftoppm`) on `PATH` — see
[`calibration/README.md`](calibration/README.md).

## Where to go next

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — technical source of truth.
- [`INTEGRATION-STATUS.md`](INTEGRATION-STATUS.md) — consolidation
  record and known test exceptions.
- [`AGENTS.md`](AGENTS.md) — operating contract for AI coding agents.
- Each module's `README.md` — CLI usage, schemas, evaluation
  workflows, and honest limits.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) — third-party
  attribution (machine-readable
  [`tools/compliance/spdx.json`](tools/compliance/spdx.json)).

## Source availability

The source is intended to be publicly viewable. Reuse rights are
undecided: no root `LICENSE` file exists and no open-source license is
claimed. Do not assume redistribution or reuse permission beyond
viewing unless and until licensing terms are published.
