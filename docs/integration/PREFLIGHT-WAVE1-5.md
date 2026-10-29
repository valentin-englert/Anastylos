# Wave 1.5 Pre-Flight Report — integration/spatial-capture-v1

Date: 2026-10-29. Author: principal integration engineer (Wave 1.5 lane).
Scope: pre-merge inventory only. No source changes made at this point.

## 1. Worktree and branch state (verified inline, this session)

- Integration worktree: `C:/Users/HP/Documents/Anastylos-integration-spatial`
- Branch: `integration/spatial-capture-v1`, HEAD `9f4a496d74350f19012df577bc5278da66babcf7`
- `git status --porcelain=v1 -uall`: empty (clean, no preexisting user edits)
- `git merge-base --is-ancestor 9f4a496d HEAD`: exit 0 (HEAD IS the frozen base)
- Log: `9f4a496d contracts(agentcam-g0): freeze` on top of `f98badb3` (main HEAD)
- Main checkout `C:/Users/HP/Documents/Anastylos`: branch `main`, HEAD
  `f98badb38515cbc415a8da466189218fc84d8794`, no tracked modifications, only the
  4 known untracked files (surface_a3_v2.pdf, cam1/cam2 mono48k wav, manifest.bak.json)
- 16 worktrees registered; lane worktrees present for all five lanes plus G0 contract.
  No other worktree was touched. Full `git worktree list --porcelain` captured in session log.
- Scoped temp dir for this lane: `%TEMP%\wave15-spatial-integration` (no shared C:/tmp filenames).

## 2. Source SHAs and ancestry (verified inline via show-ref/rev-parse/merge-base)

| Lane | Branch | Full SHA | Ancestor of base? |
|---|---|---|---|
| ANDROID | feat/agentcam-android | 770f166a69ea13881689c283e467e0745765067f | yes (exit 0) |
| CORE | feat/agentcam-core | 76e74e4cd9a49c327fc28429585e30721d9bf883 | yes (exit 0) |
| SPATIAL | feat/agentcam-spatial | 95a87a9e483f6d9a9d0654e6451fb4353de29800 | yes (exit 0) |
| CAD+PARAM | feat/agentcam-cad | 849c9afd436b6ff6cadca677894f275935327a65 | yes (exit 0) |
| QA | feat/agentcam-qa | ad32657cac4cff6f270f593247f54b262367ad2c | yes (exit 0) |
| G0 follow-up | feat/agentcam-g0-contract | 0aa054da4822ba3212d83235be991eaf20b6b761 | n/a (dispatch docs only) |

All five match the task's pinned SHAs (short SHAs resolved to the same commits).
CAD ancestry: `a4c39497e8a002675858c4bbf89bdd7c1e7de5f7` (trusted compiler + worker)
is an ancestor of `849c9afd` (parametric follow-up) and a descendant of the base.
Merging `849c9afd` therefore includes both CAD commits. Lane commit counts vs base:
SPATIAL 1, CORE 2, ANDROID 2, CAD 2, QA 1.

## 3. G0 contract verification (workflow Phase A + inline recompute pending at merge time)

- Manifest: `capture-companion/control/contracts/CONTRACT.json`, contract
  anastylos-agentcam-g0, v=g0, base=f98badb3.
- 13 frozen files rehashed 13/13 OK against manifest bytes+sha256 (git-show bytes at base).
- Aggregate recompute MATCH: `ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663`.
- G0 follow-up `9f4a496d..0aa054da` (d593f6b8 + 89718247 + 0aa054da) touches ONLY
  WAVE0-DISPATCH.md; `git diff 9f4a496d 0aa054da -- contracts/` is empty; all 14
  contract blob IDs identical. Contract bytes UNCHANGED.
- AJV Draft 2020-12 validation (offline, zero repo writes): 46/46 checks PASS.
- Frozen-vs-blueprint file pairs: semantically identical (formatting/line-endings only).

## 4. Blueprint manifest verification (workflow Phase A)

- All 9 required docs + 4 schemas + 7 vectors present under
  `random/research/ANASTYLOS-AgentCam-CAD-Blueprint-01/`.
- 33/33 ARTIFACT-SHA256 entries recomputed OK, 0 mismatches, 0 missing.
- Gitignored research material confirmed (`/random/`, nothing tracked). Not merged, not force-added.
- Known hash-consistent staleness (not fixed — owned outside this scope): SESSION-HANDOFF S1
  byte counts predate takeover edits; S6-S8 placeholders; json_parsed=12 vs 15 parseable.

## 5. Changed-file inventory and overlap detection (verified inline)

Counts vs base `9f4a496d`: android=16, core=28, spatial=45, cad=33, qa=6.
Total 128 files, 128 unique: **ZERO shared paths across all 10 pairwise lane comparisons.**

Owned dirs are disjoint: android `capture-companion/android/app/src` (+LIMITATIONS.md);
core `capture-companion/receiver` + `tools/capture-control`; spatial `calibration/spatial`
+ `tools/session/supplemental` (+1 line in `calibration/README.md` — see note);
cad `assembly-reconstruction`; qa `tools/agentcam-acceptance`.

NOTE: spatial touches `calibration/README.md`, which exists at base. No other lane
touches that file, so no content conflict is possible; the merge takes the spatial
version trivially. Same-content risk: none (no lane edited frozen contracts).

## 6. Intended merge order and conflict expectation

Order per task §4: 1. SPATIAL, 2. CORE, 3. ANDROID, 4. CAD (849c9afd incl. parametric),
5. QA last (evaluator validates the merged tree).
Method: `git merge --no-ff <laneSHA>` per lane, preserving full lane histories with
auditable merge commits on `integration/spatial-capture-v1` only.
Expected conflicts: none (zero path overlap). Any unexpected conflict stops the lane
for inspection; no merge strategy that silently discards a side will be used.
Focused tests run after each merge.

## 7. Known environmental limitations (pre-existing, not blockers for merging)

- Android Gradle suite gated on JDK 21 + SDK; JVM guidance tests only where runnable.
- CadQuery isolated venv owned by CAD lane; reuse read-only, no reinstalls.
- MCP SDK (`mcp==2.3.0`) is pin-record only; live install needs Contract Owner approval.
- All physical/hardware gates PENDING_EVIDENCE across lanes; synthetic-only validation.
- C:/tmp scratch logs cited by lane EVIDENCE files are session scratch, not repo content.
