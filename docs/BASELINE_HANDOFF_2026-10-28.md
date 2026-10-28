# Baseline handoff 2026-10-28 (audit + consolidation)

Author: audit lane. HEAD at handoff time: `bb718c6f` (this file lands in
the documentation commit on top). Read-only audit of sibling worktrees;
no worktree other than `Anastylos/main` was modified.

## 1. Commit sequence (main)

| SHA | Subject | Files |
|---|---|---|
| `e8df96a6` | prior base (pre-existing) | — |
| `41a1f642` | matcher commit (pre-existing, Muse B lane) | `tools/retrospective-custody/matcher/` |
| `4d1d5b3a` | `research(snes-02): document dual-camera observability results` (pre-existing, Muse A lane) | `snes-02/` (46 files, no JPG), `.gitignore` JPG rule |
| `3e8cb59c` | `research(sam): preserve tracking experiment tooling` (this audit) | `sam-tracking/` (37) + `THIRD_PARTY_NOTICES.md` + `tools/compliance/spdx.json` |
| `bb718c6f` | `research(retrospective-custody): preserve diagnostic evidence extractor` (this audit) | 3 files under `tools/retrospective-custody/` |

Verification at `bb718c6f`: sam-tracking 55/55 unit tests OK (offline),
retrospective-custody 8/8 OK (offline), `tools/compliance/check.py`
0 errors / 0 warnings / 11 open reviews, `git diff --check` clean.
No paid inference, no installs, no network calls in this audit.

## 2. Classified worktree changes

**COMMITTED (this audit):**
- `sam-tracking/` — implemented SAM-3.1 adapter + vendored parser.
  Evidence: 55/55 tests green offline; 13/13 `VENDOR.md` hashes match;
  `client.py` stdlib-urllib-only with lazy import; API key via
  env/stdin only (no embedded secrets); CLI fail-closed without
  `--live --upload-ok`. Limits: no live-run results in tree (SAM-01
  report "forthcoming" per README); live paths need key + permission.
- `THIRD_PARTY_NOTICES.md` + `tools/compliance/spdx.json` — bundled
  with the sam commit (notices reference `sam-tracking/vendor/`
  paths; splitting would dangle). Verified: `LicenseRef-SAM-License`
  (no invented SPDX id), review item left open, checker prints the
  documented 11 reviews.
- `tools/retrospective-custody/{README.md, retrospective_custody.py,
  tests/test_evidence.py}` — offline origin-trace diagnostic.
  Evidence: 8/8 tests green; stdlib + pinned OpenCV/NumPy; no
  network; no imports shared with committed `matcher/` (separate
  implementations, separate schemas); byte-identical to the
  2026-10-28 handoff integrity anchors (see §6). README matches the
  implemented `--clip/--evidence/--out` CLI (verified against
  `--help`; no doc fix was needed).

**DEFERRED (preserved byte-identical, uncommitted):**
- `M ARCHITECTURE.md` — stale relative to HEAD on two counts:
  (1) claims retrospective correspondence `NOT_IMPLEMENTED` while
  committed `matcher/` implements it; (2) claims the sam client is
  the only network code ("verified by search") while committed
  `capture-companion/receiver/` runs a stdlib HTTP server (tracked
  since `c1d0ede0`). Also predates SNES-02 and the contract fork, and
  mixes two provenances (sam prior work + custody-lane status line
  per the 2026-10-28 handoff). Needs Claude's contract-decision
  rewrite, not a blind commit. Hash still matches the handoff anchor
  `4e5b995d…`, i.e. untouched since that session.

**KEPT UNTRACKED (intentional, not committed):**
- `snes-02/work/*.wav` (35 MB, regenerable via committed scripts),
  `manifest.bak.json`, `cases/SY1/dbg.txt` (own-lane intermediates).
- 1,008 JPGs under `snes-02/` (gitignored per 2026-10-28 decision;
  exact frame/PTS references preserved in committed `*-pairs.json`).
- `random/` lane outputs incl. `retrospective-custody-20261028/`
  (real A3 run: `final/report.json` hash-verified, 12 crops) and the
  2026-10-28 handoff + its `.sha256` sidecar. Never force-add.
- `random/research/*.html` (16:26, quiescent, ignored, unknown
  author) — left alone.

## 3. Reproducible evidence and tests

- sam-tracking: `cd sam-tracking && python -m unittest discover -s
  tests` (55 green, <1 s, offline). CLI smoke: `sam.py --help`
  renders fail-closed usage. Vendor pins re-checkable via
  `vendor/VENDOR.md` hashes (EOL note in §7).
- retrospective-custody: `cd tools/retrospective-custody &&
  python -m unittest discover -s tests` (8 green, offline). Note:
  tests stage temp files under ignored
  `random/retrospective-custody-20261028/test-temp` and remove them.
- SNES-02: `snes-02/sync-manifest.json` (offset +0.76896 s C2-after-C1,
  ±40/±70 ms) + `snes-02/work/pair_case.py` regenerate every cited
  still from the immutable originals (`C:\ANASTYLOS\incoming\
  20261028_144225CAM1.mp4` sha256 `dd985c9d…`, `VID_20261028_144224CAM2.mp4`
  sha256 `f179d349…`; re-hash verified 2026-10-28).
- Compliance: `py -3.12 tools/compliance/check.py` → 0/0/11.

## 4. Known experimental results and limitations

- Classical tracking: 0 verified correspondences. Gate 1B: 14
  unvalidated proposals. SAM-01: useful masks, no verified
  source→destination correspondence. Custody ledger (sibling
  worktree): 0/5 sources. Retrospective diagnostic: geometry only,
  PENDING_EVIDENCE on the real A3 clip (143 narrow ends are frame
  geometries, not screws/contacts). SNES-02: 5 engagements, 4 sites
  localized (oblique only), 0/5 from overhead, verdict PASS scoped;
  4/6 gate `NOT_EVALUATED (as specified)` (6th engagement absent).
- Limitations preserved, not fixed: dark-screw appearance ambiguity,
  tray occlusion 57–84 s (SNES-02, deductively closed), S2a
  finger-occlusion, no turning-motion observation, vendor EOL pinning
  (see §7), diagnostic tests share a lane scratch dir.

## 5. Contract fork status (unresolved)

Two documents claim `anastylos-observation-v1`: withdrawn
`snes-02/observation-contract-v1.md` (Muse A, marked WITHDRAWN, plus
quarantined `snes02-observations.v1a-WITHDRAWN.json` — do not
consume) vs implemented
`tools/retrospective-custody/matcher/SCHEMA.md` + `validate.py`
(Muse B, proposed canonical). Incompatibility verified by executing
the validator over the A example (rejected: `session_ref`, then
`location.camera_id`). Freeze in force: no observation exports, no
matcher-contract changes until the owner decides. Matcher mechanics
verified in code: hypotheses = reported `site_ref`s only (unreported
places never considered); observed gaps overlapping a transit window
force AMBIGUOUS (`OCCLUDED_TRANSIT_WINDOW`); PROPOSED needs exactly
one supported hypothesis. Open sub-questions from the 2026-10-28
coordination: (a) who sets `site_ref`/`verified`/`target_ref` and on
what evidence (human-set = intervention; no GT in runtime files);
(b) whether grasp-time self-occlusion is a reportable gap (reporting
it reportedly blocks PROPOSED); (c) how site-inventory completeness
is certified (incomplete inventory reportedly caused a false
PROPOSED). Matcher logic frozen until real canonical observations
exist.

## 6. Provenance anchors (2026-10-28 handoff, verified)

`random/retrospective-custody-20261028/HANDOFF_NEXT_CHAT_2026-10-28.md`
(German, Luna-xhigh session + main-agent review) records the diagnostic
lane's scope, its real A3 run (exit 0, 36.7 s, 660 frames, 336 masks,
PENDING_EVIDENCE), clip hashes, and file hashes. Re-verified in this
audit: the 3 diagnostic sources, `ARCHITECTURE.md`, and
`final/report.json` all MATCH; the recorded `final/trace.jsonl` hash
instead matches the parent (non-final) `trace.jsonl` — a path-label
mix-up in the handoff, not post-hoc drift (all mtimes predate this
audit; this audit wrote nothing under `random/`).

## 7. Remaining integration risks

1. `ARCHITECTURE.md` uncommitted and stale (see §2) — Claude must
   rewrite the custody/matcher/sam status honestly; do not commit
   the current diff as fact.
2. V1–V7 owner decisions: NOT FOUND in this repository (full-tree
   search + sibling filename search, both empty). Obtain from Claude;
   nothing here implements them.
3. EOL pinning: `vendor/LICENSE.meta-sam` + `pyproject.toml` are not
   covered by `.gitattributes` LF rules, so a fresh Windows checkout
   converts them to CRLF and breaks 2 of 13 `VENDOR.md` hashes.
   Committed blobs match today; decide LF pinning vs accepted drift.
4. Test scratch: `test_evidence.py` stages temps inside the lane's
   real-outputs dir (`random/retrospective-custody-20261028/`).
   Cleans up after itself, but shares a namespace with irreplaceable
   run artifacts.
5. SNES-02 evidence availability: 1,008 stills + WAVs exist only in
   this worktree (by decision); other checkouts regenerate via
   `snes-02/work/pair_case.py` + local originals (see §3).
6. Sibling worktrees hold independent uncommitted work (assembly:
   `M .gitignore`; custody: `M ARCHITECTURE.md` + `custody-ledger/`;
   snes01-benchmark: `tools/snes01-benchmark/`); each needs its own
   lane decision — untouched by this audit.

## 8. Active worktrees (read-only observation, 2026-10-28)

| Worktree | Branch | HEAD | Status |
|---|---|---|---|
| Anastylos | main | docs commit on top of `bb718c6f` (this file) | `M ARCHITECTURE.md`, 3 kept-untracked snes-02 intermediates |
| Anastylos-assembly | feat/assembly-reconstruction | `1b93b1ac` | `M .gitignore` |
| Anastylos-capture-snapshot | feat/capture-m2-checkpoint | `ea1e40b2` | clean |
| Anastylos-capture-ux | feat/capture-ux | `a49c4e31` | clean |
| Anastylos-custody | feat/custody-ledger | `e8df96a6` | `M ARCHITECTURE.md`, `?? custody-ledger/` |
| Anastylos-integration-mvp-20261027 | integration/hackathon-mvp | `e8df96a6` | clean |
| Anastylos-snes01-benchmark | feat/snes01-benchmark | `ba9959e3` | `?? tools/snes01-benchmark/` |
