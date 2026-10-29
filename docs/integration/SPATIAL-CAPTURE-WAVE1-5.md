# Wave 1.5 Integration Report — ANASTYLOS Guided Capture / Spatial Evidence

Candidate branch: `integration/spatial-capture-v1` (worktree
`C:/Users/HP/Documents/Anastylos-integration-spatial`).
Evaluated candidate: `14741e1df7889fcdb35ec3d6cb3e175d0609feaa`
(scorecard commit: `e661142d120ed4d52700e1780eb99848c7b5ee8a`).
Date: 2026-10-29. Python 3.12.10. No push, no merge into main.

## A. Executive verdict: INTEGRATED (code) / NOT hardware-validated

All five Wave-1 lane histories are merged into one candidate with full
provenance, plus three bounded integration changes (Spatial↔Core
qualification wiring with a fail-open fix, Android spelling drift
guard, CAD spatial attribution). Every executable suite passes
(458 tests), compliance is 0 errors, the frozen G0 contract
re-verifies, and a fresh SHA-bound QA scorecard (14 PASS /
31 PENDING_EVIDENCE) is recorded for the candidate.

The verdict is CODE-INTEGRATED because every software gate passes on
synthetic vectors while every physical gate (real phone, real board,
real FreeCAD, live MCP host) remains honestly PENDING_EVIDENCE or
BLOCKED. No synthetic result is presented as hardware validation.

## B. Source provenance

Frozen base: `9f4a496d74350f19012df577bc5278da66babcf7` (G0 freeze on
top of main `f98badb38515cbc415a8da466189218fc84d8794`).
Frozen contract SHA-256: `ec313f7f...d44663` (full:
`ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663`).

| Lane | Branch | Source SHA | Merge commit | Files |
|---|---|---|---|---|
| SPATIAL | feat/agentcam-spatial | 95a87a9e483f6d9a9d0654e6451fb4353de29800 | e806f621 | 45 |
| CORE (1a+1b) | feat/agentcam-core | 76e74e4cd9a49c327fc28429585e30721d9bf883 | 290af47f | 28 |
| ANDROID | feat/agentcam-android | 770f166a69ea13881689c283e467e0745765067f | 916c9de6 | 16 |
| CAD+PARAM | feat/agentcam-cad | 849c9afd436b6ff6cadca677894f275935327a65 | 187b64b0 | 33 |
| QA | feat/agentcam-qa | ad32657cac4cff6f270f593247f54b262367ad2c | c946c1f2 | 6 |

All five SHAs and their ancestry from the frozen base were verified
with `show-ref`/`rev-parse`/`merge-base --is-ancestor` before merging.
CAD `849c9afd` includes `a4c39497e8a002675858c4bbf89bdd7c1e7de5f7`
(verified ancestor). All 8 lane commits are reachable from HEAD.
G0 follow-up `0aa054da4822ba3212d83235be991eaf20b6b761` touches only
`WAVE0-DISPATCH.md`; `contracts/` diff vs base is empty, so contract
bytes are unchanged (not merged; dispatch docs only).

Integration commits on top: `84880f74` pre-flight, `db309c36`
Spatial↔Core, `cf22b2be` Android guard + CAD attribution,
`14741e1d` naming + owner requests (evaluated candidate), `e661142d`
scorecard. Worktree clean at every checkpoint. Changed paths total:
128 lane files + 10 integration files (4 glue/tests, 5 renames,
6 docs/scorecards — see §C). Protected paths (`assembly-memory/`,
`memory.jsonl`, `calibration/print/`, E3-B) have zero diffs in
`9f4a496d..HEAD` (verified by filtered `diff --name-only`).

## C. Integration

Merge order (task §4): SPATIAL → CORE → ANDROID → CAD → QA, each via
`git merge --no-ff <laneSHA>` on `integration/spatial-capture-v1`
only. Conflicts: none — changed-file audit showed 128/128 unique
paths across all 10 pairwise lane comparisons. No merge strategy
beyond default `ort`; nothing discarded.

Cross-module adapters added (all stdlib-only, additive except where
noted):

1. `capture-companion/receiver/anastylos_validator_seam.py`
   (rewritten, same public function): runs the real spatial validator
   in-process over re-hashed staged bytes (`packages/<id>/manifest.json`
   resolution with traversal guard), builds `ctx` (`image_shas` always;
   hash-pinned `layouts/<sha>.json`, `profiles/<sha>.json`, `epochs.json`
   from `data_dir/spatial/` when staged), and maps outcomes:
   accept+metric_ready → valid; reject → invalid with reason codes;
   needs_review / accept-without-metric → unavailable (receipt stays
   `package_only`). Every exception fails closed. The planned
   subprocess contract (`<tools>/spatial-validator/validate.py`) was
   never published by SPATIAL, so the docstring now records the
   as-built in-process contract instead.
2. `capture-companion/receiver/anastylos_control_journal.py`
   (two minimal edits): `maybe_qualify` payload gains `package_id` /
   `image_id` (additive); and a real fail-open is fixed — the latest
   sidecar must now hash-match the receipt's own `sidecar_sha256`
   (with stored-bytes re-hash), otherwise the receipt is returned
   unchanged. Before the fix, qualifying a stale receipt joined the
   LATEST sidecar's validation onto the EARLIER row (null==null hash
   echo) and wrongly qualified it. Regression tests pin the fix.
3. `capture-companion/receiver/tests/test_spatial_qualification.py`
   (new, 20 tests): journal+seam+validator over staged bytes and the
   frozen `spatial-valid.json` vector — positive qualification,
   full-chain (layout/profile/epochs) qualification, wrong-source /
   stale / epoch-break / unknown-marker rejects, tampered-bytes /
   missing-package / no-sidecar / corrupt-profile / import-failure
   fail-closed, history immutability, no-promotion, tamper
   fail-closed, plus `adapt_report`/`build_ctx` unit cases.
4. `capture-companion/receiver/tests/test_guided_routes.py` (one
   test updated — surfaced contract change, see §E): the full HTTP
   loop now pins stub-sidecar→`rejected` (validator-driven) and
   real-sidecar→`qualified`, replacing the validator-absent
   `package_only` pin that task §5 explicitly supersedes.
5. `capture-companion/receiver/tests/test_android_wire_spellings.py`
   (new, 7 tests): the frozen-vector drift check the `Reducers.kt`
   header asks for — Android `SessionState`/`RequestState`/
   `ReceiptKind`/`WireRegistration` spellings tied to the frozen
   `control.schema.json` enums in order, journal delivery mapping ⊆
   `DeliveryState`, fixtures use frozen spellings, plus a
   no-guided-transport tripwire (to retire when Wave-2 lands).
6. `assembly-reconstruction/cad/anastylos_cad/spatial_attribution.py`
   + `tests/test_spatial_attribution.py` (new, 7 tests): validator
   outcome → CAD `basis`/`evidence_ids`/`dataset_sha256` without
   inventing geometry; `uncertainty_mm` stays `None` (no
   owner-approved scalar rule); basis requires citable evidence.

Interfaces verified as connected: Core qualification ↔ spatial
validator (HTTP end-to-end); Core wire spellings ↔ Android reducers
(spelling-compatible); spatial outcomes ↔ CAD attribution helper.
Still disconnected (honest Wave-2): Android transport (no HTTP
client/identity/cursor plumbing — nothing connected at runtime);
CAD geometry derivation (no dimension estimator, no part pose);
validator-notes persistence (no receipt column); epochs integrity
anchoring (custody-owned). No feature flags; safe defaults preserved
(`package_only` unless validated qualification succeeds).

## D. Verification

Toolchain: system `python` 3.12.10 (pinned); CAD venv python from the
preserved lane venv
(`Anastylos-agentcam-cad/.../cad/.venv`, CadQuery 2.8.0 — reused
read-only, zero installs); Temurin JDK 17.0.13 (present, unused —
no SDK); Gradle wrapper 9.8.2 + AGP 9.4.1 (pinned, unrunnable).

Exact commands (from the stated directories) and results on
candidate `14741e1d`:

- `calibration/spatial`: `python -m unittest discover -s tests` —
  88 OK.
- `tools/session`: same — 35 OK.
- `capture-companion/receiver`: same — 185 OK (3 skipped:
  pre-existing OpenCV absence for QR oracle tests).
- `tools/capture-control`: same — 13 OK (1 skipped: pre-existing
  `mcp` SDK pending owner approval).
- `tools/agentcam-acceptance`: same — 20 OK.
- `assembly-reconstruction`: same — 54 OK.
- `assembly-reconstruction/cad` (venv python): same — 63 OK.
- `python tools/compliance/check.py` (repo root) — exit 0:
  0 errors, 1 warning (no root .venv), 10 open reviews (unchanged).
- `python tools/agentcam-acceptance/accept.py run --candidate
  14741e1d... --expect-contract ec313f7f... --out <scoped-tmp>` —
  exit 0: 14 PASS / 31 PENDING_EVIDENCE.

Total: 458 tests, 0 failures, 4 pre-existing documented skips.
Android build/lint: BLOCKED — no SDK in any standard location, no
`local.properties` (both checkouts), no `.toolchain` in any of 17
`Anastylos*` dirs, SDK env vars unset; JDK 21 absent (JDK 17 only).
Lane's 145-test claim is README prose with no machine-readable
report and was NOT re-run; JVM guidance tests stay NOT_TESTED here.
CAD export/reopen: STEP round-trip rel-err ~1e-16 and +1mm regen
re-verified by the passing kernel suite on the integrated tree;
volume reconciliation independently recomputed: 90·50·6−π·4²·6 =
26698.40710525538 mm³ exactly (no regression). FreeCAD GUI
open/edit: PENDING_EVIDENCE (app not installed). Core/Spatial
negative tests: 20 new cross-module tests (§C.3) + HTTP-level
stub→rejected pin (§C.4), all green.
Candidate-bound QA scorecard:
`tools/agentcam-acceptance/evidence/acceptance-run.wave1-5-14741e1d.json`
(working-tree SHA-256
`108c8096d6eff3e2c5773e895a81604d0f23e1c161b30f5bd340367c3fecc863`,
git blob `feed660f`); lane `acceptance-run.json` (frozen-base Run 1)
preserved untouched. Scorecard fields verified:
candidate==git_head==14741e1d, contract_match True, memory
before==after.

Coverage gaps (explicit): Android JVM/Gradle suites; live MCP host
smoke; FreeCAD GUI; real-image CAD; any on-hardware gate (all E/H).

## E. Security and provenance

- Consent/revocation: unchanged Core behavior; revoke-then-qualify
  yields `late_after_revoke` (lane-pinned, suite-green).
- Session isolation: unchanged; scoped tokens and session checks
  enforced on every guided route (lane-pinned).
- Image/receipt immutability: seam re-hashes staged bytes against
  receipt hashes; receipts are append-only versions; history test
  proves old rows byte-identical after qualification.
- Memory unchanged: QA `regression-memory` gate PASS (every
  `memory.jsonl` rehashed before/after, identical); integration diff
  touches no memory path.
- Invalid spatial evidence: wrong-source/stale/epoch-break/
  unknown-marker sidecars reject or fail closed; tampered bytes,
  missing packages, corrupt staged profiles, and validator import
  failure all fail closed to `package_only` — never qualified.
- The stale-sidecar fail-open found during integration (later
  validation promoted onto an earlier receipt) is FIXED in
  `maybe_qualify` with regression tests; the old-receipt case now
  returns unchanged with no new version.
- Provider image data-use: no image bytes leave the staged store;
  the seam reads bytes locally and hashes them; no network calls
  anywhere in the qualification path (stdlib only, verified by
  construction + offline test runs).
- Surfaced contract change (one): `test_full_loop_to_package_only_receipt`
  → `test_full_loop_sidecar_qualifies_and_rejects`. The old assertion
  pinned validator-absent behavior that task §5 explicitly replaces
  with real qualification; the journal's own valid→qualified /
  invalid→rejected contract (lane-pinned) is unchanged and green.
  Both outcomes remain covered; nothing was weakened to get green.

## F. Open decisions (all routed, none bypassed)

Filed in `docs/integration/CONTRACT-OWNER-REQUESTS.md` (R1–R8):
MCP adapter exception (`mcp==2.3.0`); CAD venv exception + SPDX-edit
authorization (45-pin closure verified exact); OCCT SPDX conclusion
(Apache bindings text verified; bundled-OCCT terms unresolved);
UNKNOWN ×3 (`ezdxf`, `trame-common`, `wslink`); parametric D1–D4
(overlay adoption, operator-wins invariant, FCStd shape + FreeCAD
session, provenance bundle scope); G0 rename default-NO.
No versioned G0 change proposal is filed: nothing in the frozen
contract needs changing (pending items are additive lane contracts;
frozen schemas untouched). Device/toolchain availability: Android
SDK absent (BLOCKED, operator/infra); JDK 21 absent; FreeCAD absent.

## G. Wave-2 readiness

- Android physical build/deploy prerequisites: provision Android SDK
  (cmdline-tools + platform-36/37 + build-tools) on a workstation,
  set `ANDROID_HOME` or repo-local `local.properties` (gitignored),
  confirm JDK 17 vs 21 against AGP 9.4.1/Gradle 9.8.2 release notes,
  then run the documented wrapper `test`/`lint`/`assemble` tasks;
  afterwards implement guided HTTP transport (consent/events/status/
  attempts/finalize/lease/claim, X-Guided-* upload, changes poll),
  identity/cursor plumbing, ViewRequest/Target parsers, Status
  writers, retry-vs-remaining_exposures rule, and the OpenCV PnP
  seam (artifact + ABI demo still open).
- Calibration-board measurement plan: with a steel ruler at 100 mm
  reference bars, record `{horizontal_mm, vertical_mm}` per sheet
  into `measured_print_scale` (all 5 NULL today) at 100% scale, then
  controller height (NULL today); only then may mm claims cite the
  prints. Existing assets cover Wave 2: surface_a3/a4,
  fixture_front/back, charuco_a4 (DICT_6X6_250, 5×7, sq30/mk24,
  150×210, legacy_pattern=false); A3-v2 stays discarded.
- First real Guided Capture session: enroll → open → arm → request →
  reserve → bind → upload real phone JPEG → post real sidecar →
  expect `qualified` only when the board is genuinely solved;
  negative controls (covered lens, wrong board side, stale pose)
  must reject or stay `package_only`.
- Actual Muse request/receipt test: needs R1 approval + SDK install,
  then the MCP host smoke (9 tools) against a live journal.
- First real metric CAD test: needs measured prints + a dimension
  estimator (Wave-2 build) feeding `size_mm`/frames with
  `spatial_attribution` citing the qualifying evidence; verify STEP
  dims against calipers, not renders.
- Independent QA acceptance schedule: re-run `accept.py run` on the
  Wave-2 candidate; E/H gates flip only on attached-hardware
  evidence, never on synthetic replays.

Immediate next actions (smallest first): (1) owner rules R1–R8;
(2) provision SDK + run Android suite; (3) ruler-measure the 5
sheets; (4) first real guided session per above; (5) estimator +
metric CAD test. Integration blockers vs Wave-2 features: nothing
in §F blocks keeping this candidate as the base — all are additive
approvals/features, except SDK provisioning which blocks only the
Android suite signal.

## H. Rollback

- Return to frozen G0: `git checkout 9f4a496d` (detached) or stay on
  `main` (`f98badb3`); the integration branch touches neither.
- Lane commits preserved: all 8 lane commits remain on their
  original branches AND reachable from `integration/spatial-capture-v1`
  via the 5 merge commits; original branches/HEADs untouched.
- Disable new features safely: qualification reverts to
  validator-absent by injecting no runner
  (`ControlJournal(..., validator_runner=None)` → default
  unavailable; every receipt stays `package_only`) or by checking
  out `c946c1f2` (pre-glue merges only). No migration needed: the
  journal schema is unchanged (no new columns/tables).
- No historical data loss: append-only stores only gained rows in
  temp test databases; repo memory/evidence/print masters identical
  (see §B/E). No push and no merge into main occurred at any point.

## Pass/fail matrix

| Gate | Verdict | Basis |
|---|---|---|
| Lane merges (5/5 + parametric) | PASS | clean merges, full history, 128 files |
| G0 contract (13/13 + aggregate) | PASS | recompute MATCH, follow-up identical |
| Spatial suite | PASS | 88/88 |
| Session suite | PASS | 35/35 |
| Receiver suite | PASS | 185 OK, 3 pre-existing env skips |
| Capture-control suite | PASS | 13 OK, 1 pre-existing approval skip |
| QA self-tests | PASS | 20/20 |
| Assembly suite | PASS | 54/54 |
| CAD suite | PASS | 63/63 incl. kernel round-trip |
| Compliance | PASS | exit 0, 0 errors |
| QA acceptance (candidate-bound) | PASS | 14/14 executable, 31 PENDING (physical) |
| Spatial↔Core positive/negative | PASS | 20 new tests + HTTP loop, fail-closed proven |
| Stale-sidecar fail-open | PASS | fixed + regression-pinned |
| Android↔Core spelling/contract | PASS | 7-guard drift check; runtime NOT connected (Wave-2) |
| CAD↔spatial attribution | PASS | helper + 7 tests; geometry Wave-2 |
| Volume reconciliation | PASS | 26698.40710525538 exact, no regression |
| Memory/evidence immutability | PASS | rehash-identical, zero protected diffs |
| Dependency freeze/hashes | PASS | 45/45 exact, 1689 hashes match |
| License/SPDX conclusions | PENDING | R2–R6 with owner; never marked PASS |
| MCP live SDK smoke | BLOCKED | R1 approval + install needed |
| Android Gradle suite | BLOCKED | no SDK/toolchain; exact blockers recorded |
| JVM guidance tests | NOT_TESTED | same blocker; no stub claimed |
| FreeCAD GUI open/edit | PENDING_EVIDENCE | app absent |
| Real-image / hardware gates (E/H) | PENDING_EVIDENCE | no hardware attached, by design |
| Calibration print measurement | PENDING | 5 NULL scales + height; plan in §G |
| Parametric D1–D4 | PENDING | R7 with owner; behavior preserved |
