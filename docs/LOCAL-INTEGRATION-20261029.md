# Local consolidation evidence — 2026-10-29

This is a local integration checkpoint under feature freeze. The status index
remains `ARCHITECTURE.md`. No push, history rewrite, dependency installation,
paid model call, provider-retention change or hardware validation was performed.

## Sources and preservation

- Main baseline: `f98badb38515cbc415a8da466189218fc84d8794`.
- Completed live implementation: `ebf7f46a8e3597a5025b54750ff789744c347c4d`.
  Its five implementation lanes were already merged; they were not replayed.
- SNES research commits: `9bcd6941` and `ba9959e3`, plus the 22 preserved
  untracked source, prompt and test files. They remain standalone research tools.
- Microscopy v0.1.2: the 76 delivery-manifest payload files, the original manifest
  and 11 selected historical QA records. Original reader implementations remain
  unchanged. Package-scoped `-text` preserves the delivered bytes, including
  pre-existing mixed line endings and whitespace; no global normalization.
- Release ZIP SHA-256:
  `7a4810b09ae318337ae0b6b020d60116075f4565f941eb04de14455df88bd151`.
  The ZIP, staging copies, complete v0.1.0/v0.1.1/v0.1.2 QA histories and generated
  outputs were preserved outside Git; original artifacts remain in main too.

Preservation directory (outside every worktree eligible for removal):
`C:\Users\HP\.codex\visualizations\2026\10\29\01a189c1-e001-7662-bd62-55395c7dd9ba\preservation`.
It contains `inventory.json`, staged/unstaged binary patches, `all-refs-before.bundle`,
`bundle-sha256.json`, `safety-refs.json`, `file-manifest.json`, frozen-file checks,
manifest checks and process-ownership snapshots. The bundle was verified; 32
original branch tips have refs under `refs/safety/consolidation-20261029/`.
The file manifest records 1,431 verified file copies and metadata for 10
reparse points. A final audit found that eight of those copies came from
fixture aliases exposed by Git's untracked-file list: their parent paths
crossed junctions before the link-aware tree walk recorded them. This was a
preservation-procedure error. All targets are synthetic QA fixture directories
inside the retained main worktree; originals were not modified or deleted.
`junction-intake-audit.json` identifies those eight copies separately, and the
backup script now rejects reparse-point ancestors before opening a listed
file (8/8 alias paths blocked by the guard check). The original pre-audit
manifest remains preserved. Volatile spatial-owner work was inventoried and
retained, rather than copied into the candidate.

The final report beside that directory records exact candidate/main SHAs,
every original worktree's disposition, removal outcomes, retained artifacts
and verification commands. Verification logs are in the sibling `verification/`.

## Verification scope

Full `python -m unittest discover -s tests -v` suites ran from each module:
live 51, providers 27, l2sim 33, eval 38, guide 51, integration 12, SNES 84,
custody 95, memory 29, session 25, reassembly 11, reconstruction 54 and
proto-01 scorer 6: all passed, no skips. Microscopy: 83 run, 76 passed,
7 platform skips after Windows junction/hard-link cases ran outside the sandbox.
Unchanged reader regression: 543 passed; CLI smoke: 20/20 steps passed.
Compliance: zero errors, one environment warning, ten unresolved reviews;
`python -m unittest test_check -v` from `tools/compliance`: 5 passed.

Earlier attempts remain in the logs: microscopy had 10 sandbox/platform skips;
CLI smoke ran 18 steps with junction setup skipped; the generic compliance
`-s tests` runner errored because that module has `test_check.py` directly,
and its first direct run had three temp-directory permission errors. The
correct full runner with an external writable temp directory passed.

All 20 E3-B committed-file hashes match the preregistration, before and after
integration. Checkout LF-equivalent hashes also match. No frozen file was edited.
No preregistered evaluation inputs, automatic/operator provenance or criteria
were changed. These are integration/synthetic checks, not scientific L2 or
physical validation. All 76 microscopy payload hashes also match in the Git index.

## Preserved limitations

- Lane A's original manifest records the CRLF source hash
  `6ef053707183e4e71f6820cab839d0370ba8f872b7e40febdac95c0d4aee9e69`
  for `tools/live/tests/fixtures/audit/memory.before-answers.jsonl`; the
  unchanged committed LF blob is
  `b24225a308c8dc01286247015ca8dfe8f394c59d6585913de4cd716fd5644dc8`.
  This additive note does not replace the original manifest or evidence.
  Lane A is 7/8, B 3/3, C 4/4, D 7/7 against committed blobs. Windows checkout
  conversion separately changes the raw hashes of A/D `apply_input.jsonl`;
  their committed LF blobs match the original manifests. Both forms are recorded.
- Journal fields still differ from contract 0.2 (`at` versus `t_wall`, etc.).
  Integration is not a conformance claim. Durable memory `op_key`, fsync and
  store-wide locking remain blocked by the E3-B freeze. The provider's existing
  `store: true` behavior is unchanged.
- l2sim's `session.py` shadows `tools/session/session.py`; subprocess isolation
  remains the workaround. `request_view` stays gated. Contract 0.3 and refreshed
  provider fixtures remain future work. The independently owned real L2
  evaluation establishes no benefit until its completed handoff is reviewed.
- Microscopy's historical extended helper assertion remains FAIL for
  `input_error` versus the expected manifest/hash-rejection classification.
  No assertion was weakened. Historical held-out retained-candidate precision
  is 39.5%; no automatic identification or hardware claim follows.
- Spatial/Wave 2/Mesh and their source lanes remain deferred to Muse's completed
  handoff. The live evaluation worktree remains protected. LAT-1's nine source
  files are backed up and retained; an owning Claude process was observed.
- The assembly `.gitignore` patch contains malformed backslash patterns; the
  custody architecture patch is superseded by current main. Exact patches and
  relevant generated assembly evidence were preserved, not applied.
