# Explicit Capture-Gap Planner

Offline, stdlib-only, read-only function: `capture_gaps.planner.evaluate(document)`.
No image detection, provider, Memory write, camera control or request dispatch.
The research is design input only, never imported at runtime.

From `tools/agent`:

```powershell
python -m capture_gaps capture_gaps/example.json
python -m unittest discover -s tests -p test_capture_gaps.py
python -m unittest discover -s tests
```

`example.json` is synthetic, not a FULL-01 identity assertion. It prints an
English request for the whole back-shell rim. Output goes only to stdout.
CLI exit 0 means evaluation succeeded (including open/needs_review); 2 means
invalid input. No output is persisted automatically.

## Session-UI handoff

Input schema `anastylos-capture-gap-input@1`: `gap` contains unique caller-owned
`id`, finite `kind` (`outer_boundary`, `missing_side`, `orientation`,
`frame_identity`), specific `detail`, `binding`, and explicit
`source_image_sha256s`. Binding contains session, target `{kind: event or
drawing_element, id}`, revision, hashes `{source-name: lowercase SHA256}`.
No label matching. Include every available source hash; empty hashes are
allowed for un-hashed sources, but revision is mandatory. `current_binding`
must be supplied freshly by the caller, not copied blindly from the gap.
This module compares explicit bindings; it does not independently load files
or authenticate the caller. Stale input from a caller cannot detect itself.

Optional candidate: evidence_id, image_sha256, binding. Bytes must already be
hash-verified by the ingest/caller. This module never reads an image and its
hash field alone is not evidence that bytes exist. Candidate remains
needs_review until technical_review and operator_confirmation both contain
accepted=true, reviewer, recorded_at, gap_sha256 (returned by initial plan),
evidence_id and image_sha256. Operator confirmation additionally echoes the
exact returned content_criterion. It confirms all stated content, not just
image quality. UI must collect explicit assent; never synthesize it.
Reviews are supplied assertions, not authenticated signatures.

Result schema `anastylos-capture-gap-result@1`: gap fingerprint, binding,
missing_information, detail, English instruction, technical_criteria,
content_criterion, candidate, status, reason, basis and dispatch_allowed=false.
Closed results retain both reviews, basis operator_declared. No geometry or
visual identity measurement is asserted. Revised gap criteria/detail invalidate
old reviews through gap_sha256. Current/candidate binding mismatch prevents
closure. Duplicate image bytes cannot supply a new boundary/side/orientation
view. Frame identity intentionally reviews the existing frame itself, without
counting it as a new view. This evaluator keeps no history: UI must supply all
previous relevant image hashes and retain results/reviews in its own workspace.

## Existing capture contracts

This is a local proposal, **not** a ViewRequest and never sendable as one.
The receiver ViewRequest contract additionally requires UUIDs, revision,
expiry, board/calibration/epoch IDs, target pose, quality profile and context
(`capture-companion/receiver/anastylos_control_journal.py`). Android ShutterGate
checks pose freshness, dwell, board/profile and image quality; these necessary
technical gates do not prove rim visibility, side or part identity. A future
adapter must validate the existing contract and gate separately. request_view
remains disabled. Spatial result=complete/geometry_ready is not content closure.

## Offline acceptance

Ten unit tests use explicit review records and synthetic hashes; they do not
mock an image analyser or claim image understanding. They cover the five
required scenarios, wrong target, wrong review binding, all four kinds,
invalid input, immutable arguments and existing-frame confirmation.
For a closed replay, supply a different candidate image hash and both explicit
reviews described above; use the initial output gap_sha256 and exact criterion.
No source file or Memory is modified by evaluation.

## Verification on b522336d (2026-10-31)

Full agent runner from tools/agent: 99 PASS, 0 FAIL/ERROR/SKIP (145.951 s).
Focused capture-gap suite: 10 PASS, 0 FAIL/ERROR/SKIP. CLI example: exit 0, open.
Compliance: 0 errors, 1 warning (no local venv), 18 existing reviews.
Frozen E3-B HEAD blobs: 20/20 against existing freeze inventory; no frozen
tracked diff. No HTTP/model/camera runs. Initial test invocation from repo root
failed to import the module (1 loader ERROR); rerun from the documented
tools/agent directory passed without weakening tests.
