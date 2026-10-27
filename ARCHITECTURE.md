# ANASTYLOS architecture

Evidence-backed visual assistance for electronics disassembly and
reassembly. Every module reports *observations with provenance*, never
bare claims: millimetre coordinates cite their calibration, verdicts
stay `unknown`/`ambiguous` when evidence is weak, and nothing is
confirmed without explicit operator confirmation.

No real-hardware validation has been performed on this branch: all
vision results below come from synthetic fixtures and unit tests.

## Design rules (observed, keep them)

- Append-only memory: `assembly-memory` events are never edited or
  deleted; corrections append a new event with `supersedes`.
- Read-only analyzers: integrators (`missing-parts`, `cable-routing`
  graph, `tray-counter`) read detection output and memory logs but
  never write back to them; they write only to their own `--out` dir.
- Explicit epistemics: recognizers return `ok` / `ambiguous` /
  `unknown` with reasons; candidates never self-promote.
- Stable tiers: `assembly-memory` and the capture validator are
  standard-library-only; vision modules use the pinned
  OpenCV + NumPy environment (`calibration/requirements.txt`).
- Offline: Python has no network imports; the capture PWA's only
  network use is same-origin service-worker caching of its app shell.
- Licensing is deliberately deferred: no root `LICENSE` file, no
  project license declared anywhere. (`AGENTS.md` is intentionally
  gitignored as local-only agent notes, not a repo source of truth.)

## Evidence flow

```text
calibration/  ── sheets + px/mm frames ──> change-detection/
change-detection/ ── result.json + rectified pairs ──> tray-counter/
                                                  ──> missing-parts/
                                                  ──> assembly-memory (import)
readers/ ── observations ──> assembly-memory/ (append-only memory.jsonl)
assembly-memory/ + change-detection/ ──> cable-routing/ (read-only graph)
capture-companion/ ── validated .zip packages ──> (human review; never
    auto-imported into Core memory)
```

## Module / status index

Status `integrated` = merged into `integrate/feature-batch` and
verified there on 2026-10-27. Test counts are that run
(`python -m unittest discover -s tests` per module unless noted).

| Module | Responsibility | Entry point | Status | Tests |
|---|---|---|---|---|
| `calibration/` | printable sheets, ChArUco, px/mm frames | `generate.py` | integrated | 13 run, 9 pass (4 need native Cairo; known Windows gap) |
| `change-detection/` | planar before/after change check | `detect.py` | integrated | 3 pass + synthetic selftest 16/16 |
| `assembly-memory/` | append-only disassembly memory, where-goes queries | `memory.py` | integrated | 21 pass |
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
| `tools/compliance/` | third-party inventory checker (SPDX) | `check.py` | integrated | 5 pass; `check.py`: 0 errors, 8 open reviews |

Hardware-validated: none. Every vision claim above rests on synthetic
fixtures; see each module README for its stated validation scope.

## Stable data interfaces

- Detection `result.json`: `status` (`no_change`, `single_change`,
  `multiple_changes`, `registration_invalid`, `view_mismatch`,
  `input_error`), `view`, `rectified.px_per_mm`, `regions[]` with
  `label`, `bbox_mm`, `centroid_mm`, `result_sha256` provenance.
  Consumed verbatim by `tray-counter`, `missing-parts`, `cable-routing`.
- Memory log: `<session>/memory.jsonl`, one JSON event per line
  (`observation` / `tray_link`), SHA-256 file provenance, coordinates
  stored verbatim and never transformed.
- Calibration JSON: `pixels_per_mm` (+`_std`, `frame`) for
  screw/cable frame metadata; `px_per_mm` inside rectified detection
  output. Same physical meaning, different key spellings per module;
  each module validates its own input. Do not invent a unified schema
  without a migration.
- Capture package (schema v1, `capture-companion/SCHEMA.md`):
  manifest + assets zip; mirrored TypeScript/Python validators must
  keep agreeing (round-trip test enforces this).

## Reproducible test commands

Python (from the repo root; `.venv` per `calibration/README.md`):

```powershell
cd <module>; ..\.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python change-detection/detect.py selftest --seed 20261026
.venv\Scripts\python tools/compliance/check.py
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

## Integration limitations and remaining work

1. Calibration kit tests (4 of 13) need native Windows components
   outside `requirements.txt`: `libcairo-2.dll` on `PATH` (PDF/PNG
   generation) and poppler `pdftoppm` on `PATH` (test-only PDF
   rasterizing). Product code degrades gracefully to SVG with a
   warning. Setup is documented in `calibration/README.md`;
   committed print/preview fixtures are intact and consistent.
2. `pixels_per_mm` vs `px_per_mm` key spellings differ by module (see
   above). No live incompatibility; unify only with a migration.
3. npm transitive closure licenses unverified (compliance review
   item); direct capture devDeps are MIT/Apache-2.0 per installed
   metadata.
4. No end-to-end real-hardware run exists. Synthetic selftests and
   fixtures do not substitute for it.
