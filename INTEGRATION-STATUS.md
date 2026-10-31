# ANASTYLOS Integration Status

## Current status (2026-10-31, main @ `80eb2030`)

On 2026-10-31 the two accepted hackathon MVP lines were integrated
into local main via a tested staging branch
(`integration/main-consolidation-20261031`) and fast-forwarded, with no
rebase, no history rewrite, and no push. Source worktrees were left
untouched; all other pre-existing worktrees remain intact.

| Item | Commit |
|---|---|
| Starting main HEAD | `6ac32a74` |
| Merge 1 — Android spatial-capture MVP | `6736f3b3` (`integration/spatial-capture-v1`) |
| Merge 2 — Core ingestion MVP | `80eb2030` (`integration/core-ingest-v1`) |
| Accepted Android checkpoint (in main) | `b65cc0a0` |
| Accepted Core checkpoint (in main) | `66580106` |
| Current main baseline | `80eb2030` |

Both merges were conflict-free except a disjoint-hunk auto-merge of
`.gitignore`. The Core file set was fully disjoint from the Android
file set. Both accepted checkpoints are verified ancestors of main.

Verification observed on the tested integration HEAD (identical to
current main):

| Suite | Result |
|---|---|
| Core ingest (`tools/core-ingest`) | 8/8 PASS |
| Receiver (`capture-companion/receiver`) | 185 pass, 3 skipped (pre-existing env skips) |
| Capture validator | 22/22 PASS |
| Assembly Memory | 24/25; 1 `test_integration` loader **error**: `No module named 'cv2'` on system python. Reproduced identically on the pre-merge main baseline — pre-existing environmental gap, not merge-caused |
| Android unit (`:app:testDebugUnitTest`) | 443/461; **18 failures**, all in `GuidanceViewModelTest` (`SystemClock`/`Log` called without Robolectric shadows). Proven pre-existing at frozen tip `b65cc0a0`: the merged Android tree is byte-identical to the accepted branch and the branch's own 438/438 reports predate the failing tests. Not merge-caused; frozen code was not modified |
| Android lint / assemble | SUCCESS; lint 0 errors (9 warnings) |
| Compliance (`tools/compliance/check.py`) | 0 errors (1 warning, 18 open reviews) |

Known limitations on current main: the suites above do not prove a
complete model-driven physical loop; `integration/agent-v1` (M0–M2
agent orchestrator) is unmerged on its own branch; physical-demo gates
beyond GUIDE-03/HARDWARE-03 remain open; the 18 Android and 1
Assembly-Memory exceptions above are documented and carried, not
hidden.

## Historical snapshot (2026-10-29 — preserved verbatim below)

> Everything below this line is the October 29 branch-integration
> report for `integration/spatial-capture-v1`, kept unchanged as a
> historical record: provenance, commit references, and observed test
> results are intact and have not been re-baselined. Statements about
> branch state (e.g. "nothing here is merged to main") were true when
> written; both MVP lines have since been merged into main as recorded
> above. Where the October 29 matrix and the October 31 table differ
> (e.g. Android 380/380 then vs 443/461 with 18 known failures now),
> the difference reflects later branch development plus the merge —
> not a reinterpretation of the earlier run.

Date: 2026-10-29.
Canonical worktree: `C:\Users\HP\Documents\Anastylos-integration-spatial`
Branch: `integration/spatial-capture-v1`
Code HEAD: `9064743f` (this index committed on top; see `git log -1`).
Baseline base: `5ef14ad8` (Wave-1.5 integration HEAD).
G0 contract: `ec313f7f…d44663`, recomputed MATCH (13/13).

Verdict: **INTEGRATED.** All five Wave-1 lanes, MESH-01, and Android
Wave 2 are present in one clean branch with no duplication, no
contract change, and no regressions. Mesh stays optional and
non-normative. Physical gates stay open (see below).

This worktree is the sole target for new cross-module development.
Source worktrees below remain unchanged as references and rollback
checkpoints. Nothing here is merged to main or pushed.

## 1. Source-to-target provenance matrix

| Source | Source commit | Integrated as | Mechanism |
|---|---|---|---|
| Wave-1.5 base (5 lanes) | `5ef14ad8` | `5ef14ad8` (branch base) | pre-existing merges |
| MESH-01 `experiment/mesh-01` | `2b0d6631` | `a2117a48` | `cherry-pick -x`, 0 conflicts |
| Wave 2 `feat/wave2-guided-capture` | `3dbde8f5` | `cc954f50` | `cherry-pick -x`, 0 conflicts |
| Wave 2 | `e669bb96` | `19ed843d` | `cherry-pick -x`, 0 conflicts |
| Wave 2 | `cd95c01b` | `95299e20` | `cherry-pick -x`, 0 conflicts |
| Wave 2 | `deb6c5c0` | `5f66fd5d` | `cherry-pick -x`, 0 conflicts |
| Wave 2 | `dc2c1c6c` | `9064743f` | `cherry-pick -x`, 0 conflicts |

Fidelity proofs (all run in the canonical worktree):

- Mesh tree byte-identical to source:
  `git diff 2b0d6631 HEAD -- assembly-reconstruction/mesh/` → 0 lines.
- Integrated tree equals Wave 2 tip plus mesh:
  `git diff dc2c1c6c HEAD` outside `assembly-reconstruction/mesh/` → 0 lines.
- Change scope bounded: outside `assembly-reconstruction/mesh/`,
  `capture-companion/android/`, and the one receiver tripwire test,
  diff vs base → 0 lines.

Each integrated commit carries a `(cherry picked from commit …)`
trailer with the exact source SHA.

## 2. Modules consolidated

Present and verified: SPATIAL, CORE, ANDROID (Wave 1),
CAD+PARAMETRIC (incl. `849c9afd`), QA acceptance, MESH-01,
Android Wave 2. Historical lane reports preserved untouched
(`docs/integration/`, `capture-companion/android/WAVE2-GUIDED-CAPTURE.md`,
`assembly-reconstruction/mesh/EVIDENCE.md` + lane docs).

## 3. MESH-01 integration report

- Location: `assembly-reconstruction/mesh/` (134 files, new dir).
- No `.venv`, caches, or large outputs imported; manifests,
  fixtures, benchmark pins, docs, and dependency records kept.
- Suite: 64 tests → 63 PASS, 1 FAIL, in 55 s (mesh venv,
  freeze-verified 11/11). The FAIL is exactly the documented
  Case A: `test_box_8view_nonempty_and_bbox_under_5pct`,
  `0.2375 not less than 0.05`. Criteria untouched.
- CLI smoke: render → reconstruct → evaluate → verify-reopen
  exit 0; PLY `680becbc…`, STL `69fc3812…`, 28068/56132 —
  byte-match of committed `smoke_out/recon/` pins.
- Functional flow (§4): synthetic SpatialDataset-shaped staging
  → `ingest-spatial` → READY exit 0 with `mesh-evidence-v0`
  manifest (`non_normative: true`, reopen-verified); incomplete
  masks → exit 1 `PENDING_MASKS` with status file and NO
  manifest. Adapter is read-only; nothing writes to Assembly
  Memory. No contract changes were needed; none made.
- Preserved limits: synthetic-only, Case A FAIL, COLMAP
  BLOCKED, CAD comparison PARTIAL, no real-image or physical
  accuracy claims. No new reconstruction dependencies.

## 4. Android Wave 2 integration status

Wave 2 was NOT present in the canonical branch (canonical HEAD
was its exact base); all 5 commits integrated, no duplication.
Preserved and re-verified in the canonical tree:

- 380/380 unit tests, lint 0 errors, debug APK assembles.
- Canonical APK: `capture-companion/android/app/build/outputs/apk/debug/app-debug.apk`,
  SHA-256 `5BB18D78B4BB7A7DDA8D1D4CFA8824058516F4939CB75E7343CF2F03BDE3D3BF`,
  193515286 bytes. (Hash differs from the Wave 2 worktree's
  `68298E67…` build only via build paths/timestamps; Android
  sources are byte-identical.)
- Regression guards intact: selective camera unbind,
  OpenCV init-before-Mat + linkage containment (2 tests),
  Room v1→v2 migration test, wire-fixture suites.
- Emulator vs physical distinction preserved: all Wave 2
  device claims stay emulator-qualified per the Wave 2
  report. No emulator re-run was needed (sources identical);
  no new device claims are made here.

## 5. Regression / test matrix (observed in canonical tree)

| Suite | Result | Notes |
|---|---|---|
| Mesh 64 | 63 PASS, 1 known FAIL | Case A `0.2375 < 0.05`, unchanged |
| Mesh CLI smoke | PASS | exit 0, pins byte-match |
| Mesh ingest smoke | PASS | READY 0 + PENDING 1 paths |
| Android unit | 380/380 PASS | 0 skipped |
| Android lint/assemble | PASS | 0 errors; APK `5BB18D78…` |
| Receiver | 185 OK | 3 pre-existing env skips |
| Spatial | 88 OK | — |
| Assembly-reconstruction (2D) | 54 OK | — |
| QA acceptance harness | 20 OK | — |
| Capture validator | 22 OK | — |
| Assembly-memory (system python) | 25, 1 env error | missing `cv2`, tree untouched |
| Assembly-memory (cv2 env) | 29/29 OK | no-regression proof |
| Calibration (system python) | 1 env error | missing `cv2`, tree untouched |
| Calibration (cv2 env) | 13: 9 pass, 4 known env | missing `cairosvg`/poppler (documented limit) |
| G0 contract | PASS | 13/13, SHA match |
| Compliance | PASS | 0 errors (1 warn, 10 reviews as before) |

No suite was skipped, weakened, or re-baselined. The two
system-python import errors and the 4 calibration gaps are
pre-existing environment limits on trees with 0 changed lines.

## 6. Remaining blockers and physical gates

- Contract-owner R1–R8 still unapproved (MCP, CAD venv/SPDX,
  OCCT, UNKNOWN ×3, D1–D4); G0 rename request denied (Wave-1.5).
- Real phone run: board detection, guidance, dwell
  auto-shutter, rotations, cold starts, ANR, throughput —
  PENDING_EVIDENCE (emulator evidence stands, physical does not).
- Live Core TLS round-trip, real calibration/print-scale,
  real-image CAD, FreeCAD GUI, live MCP — PENDING_EVIDENCE.
- Mesh: Case A bound, held-out/real-image accuracy,
  COLMAP, CAD-compare completion — experimental, unchanged.
- APK size 184 MB (4-ABI OpenCV); per-ABI splits deferred.

## 7. Next integration checkpoint (proposed)

1. Physical phone session (§16 of the Wave 2 report):
   install, A3 tracking check, one armed dwell capture.
2. Print-scale + ChArUco calibration → measured profile.
3. Workstation receiver live → enroll/attach/attempt/receipt
   round-trip with a real guided still + sidecar.
4. Mesh held-out review on real-image silhouettes only after
   (2); keep `non_normative` until then.
5. Re-run this matrix + `accept.py run` on the resulting
   candidate and bind a fresh scorecard.

## 8. Commands and environment

```powershell
# Mesh suite (pinned env lives in the mesh source worktree; used read-only)
& 'C:\Users\HP\Documents\Anastylos-mesh-experiment-01\assembly-reconstruction\mesh\.venv\Scripts\python.exe' -m unittest discover -s tests
# working dir: assembly-reconstruction/mesh  (~55 s; expect 63/1 known FAIL)

# Android (Temurin 21; per-clone gitignored local.properties already present)
$env:JAVA_HOME='C:\Users\HP\jdk\jdk-21.0.12.1+1'
.\gradlew.bat :app:testDebugUnitTest :app:lintDebug :app:assembleDebug --offline --no-daemon
# working dir: capture-companion/android

# Python suites (module dirs): python -m unittest discover -s tests
# G0: python tools/agentcam-acceptance/accept.py verify-contract
# Compliance: python tools/compliance/check.py   (expect 0 errors)
```

## 9. Rollback

- `git checkout 5ef14ad8` returns to the Wave-1.5 baseline;
  `a2117a48` = mesh-only checkpoint; `9064743f` = full code
  checkpoint. Original branches/commits (`2b0d6631`,
  `dc2c1c6c`, all lane checkpoints) are untouched.
- Source worktrees (`Anastylos-mesh-experiment-01`,
  `Anastylos-wave2-android`) unchanged — verified clean
  before and after. Do not delete them.
- No main merge, no push, no history rewrite performed.
- Main, E3-B, Assembly Memory data, print masters, and the
  untracked `anastylos-microscopy/` directory were not modified.
