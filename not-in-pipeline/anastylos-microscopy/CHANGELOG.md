# Changelog

## 0.1.2 — 2026-10-29 — Windows integration and resistor uncertainty

Integrates the verified v0.1.1 payload into the main worktree and preserves
the v0.1.0 installation and QA history. No reader source, dependency, schema,
or hardware integration changed.

### Changed
- Resistor evidence assessor v2 holds a native `ok` as `ambiguous` when any
  one decoded band has at least 10% of its pixels with a channel at or above
  250. The prior policy required clipping across multiple bands, which let
  glare-affected interpretations through when a single band was compromised.
- Native result JSON and unconfirmed candidates remain byte-for-byte as
  produced by the reader; only the Core status and assessment metadata change.
- Threshold review used the 1,200-render development grid. The 512-render
  held-out grid was run only after this policy was frozen. Both remain
  synthetic evidence, not resistor accuracy or hardware validation.

### Compatibility
- `core_version` is `0.1.2`; resistor assessment identifier is
  `anastylos-microscopy-resistor-evidence/2`.
- Some resistor records previously reported as `candidate` may now be
  `ambiguous` when a single band is materially clipped. Existing exit-code
  semantics and inspection schema remain unchanged.

## 0.1.1 — 2026-10-29 — reliability patch (QA-01, QA-02, QA-03)

Corrective maintenance release after independent Codex QA of v0.1.0
(*ACCEPT WITH FIXES*). No new recognizers, no reader changes, no new
dependencies. Details and evidence: QA_FIX_REPORT.md.

### Fixed
- **QA-01** Batch exit status. `inspect --batch-dir/--manifest` returned 0 even
  when every entry crashed or was rejected. Now: 0 all completed, 1 empty / nothing
  usable, 3 `ANASTYLOS_ROOT` missing (fatal pre-flight, nothing written) or every
  entry `unavailable`, 4 partial. Single-image `inspect` uses the same rule.
- **QA-02** Capture-root confinement. Frames reached through symlinks, Windows
  junctions or other reparse points outside the manifest directory were read.
  Frames are now resolved, confined to the real capture root, opened once,
  identity-checked after opening and hashed/decoded/preserved from the same
  bytes. `validate --kind capture_manifest` applies the same checks.
- **QA-03** False-confident resistor read on the synthetic glare fixture.
  A Core evidence assessment holds unsupported native resistor `ok` verdicts as
  `ambiguous` while keeping the native result and candidates.

### Added
- `schemas/microscopy-batch.v1.schema.json`; `validate --kind batch`.
- `batch.json`: `outcome`, `exit_code`, `outcome_counts`, `reader_status_counts`,
  `ocr`, per-entry `processing`, `reader_statuses`, `core_downgraded_readers`.
- Inspection: `recognizers[].core_assessment` (resistor), candidate marker
  `core_assessment`, `uncertainty.native_reader_statuses`,
  `quality.channel_clipped_fraction` and non-blocking flag `channel_clipping`,
  `source.capture_manifest.capture_root` and `.confinement`.
- `assessment.py`, `tools/qa03_resistor_sweep.py`, `tools/smoke_cli.py`,
  `tools/qa_repro/`, `documentation/qa-evidence/`, 46 regression tests.

### Changed (compatibility notes)
- Exit codes: batches no longer always return 0; single image returns 1 for
  overall `error`, 3 for `unavailable`, 4 when a reader crashed beside a working
  one (v0.1.0: 0 in all three cases). New code 4.
- `inspect_batch` / `inspect_directory` / `inspect_manifest` raise
  `ReaderEnvironmentError` before processing when the root is missing (v0.1.0:
  one `crashed` entry per image).
- A missing manifest frame is `manifest_rejected` (no record) instead of an
  `input_error` record; `status_counts` uses `manifest_rejected` for rejected
  frames (v0.1.0 counted them as `crashed`).
- `validate --kind capture_manifest` checks files and hashes on disk, not only
  the JSON schema.
- Frame / dataset paths: `.` components, `:`, reserved device names, trailing
  dot/space and hard-linked files are rejected.
- A resistor `ok` may now appear as `ambiguous` (native verdict still in
  `native_status`). MIC-05 glare: `false_confident` → `correct_abstention`.
- Harness fingerprint changes with the Core code:
  `417c8350135a9acfcbd190babca97e02bfccbf283bef1cac0f662827b523904f` (0.1.0) →
  `d17407cc1dc2162782c9f5583ecd475e6151b8de5f54fb47d1eebc8d4419549e` (0.1.1).
- Inspection ids/digests differ from v0.1.0 (core version, new fields);
  v0.1.0 records replay as non-identical under v0.1.1 by design.

## 0.1.0 — 2026-10-29

Initial release (SHA-256 of the ZIP
`1664cbfc16fad93caa3f9d5764649cbafa2bb32b37926c3bd884b575801ab83b`).
