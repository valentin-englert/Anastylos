# Lane briefs 2026-10-28 (Technical Lead)

Copy-paste briefs for the execution lanes. Baseline: `main` @ `027ab2b0`
plus the Technical Lead's documentation commit that adds this file.
Integration authority stays with the Technical Lead: no lane redefines a
shared contract, edits `ARCHITECTURE.md`, merges, pushes or touches
another lane's files. A brief is not evidence that the work happened;
each lane reports its own verification.

---

## Brief B1 — Muse B: implement owner decisions V1, V2, V4, V5 in the matcher

```text
Role: Muse B, matcher lane. Exclusive file ownership:
tools/retrospective-custody/matcher/** . Touch nothing else.

Read first: AGENTS.md; ARCHITECTURE.md section "Correspondence contracts
and owner decisions" (V1–V7 are binding); matcher/SCHEMA.md; matcher/README.md.

Order (two commits on main, no push):
  1. SCHEMA.md + README.md only: document the fields below, the new reason
     code, engine version 1.1.0.
  2. validate.py, match.py, new adapter, tests.

V1  Additive optional fields on every event:
    evidence.frames: [{frame: int>=0, pts_seconds: number>=0,
                       role: "primary"|"before"|"after"|"context"}]
    evidence.basis_event_ids: [event_id, ...]  (each id must exist in the
    merged run; check in run_match after merging files)
V2  Additive optional event field:
    producer: {kind: "automatic"|"human", ref: non-empty string,
               config_sha256: null | 64 lowercase hex}
    Output, per deposit decision: autonomy = "autonomous" only if every
    cited event has producer.kind == "automatic"; "operator_assisted" if
    any cited event is human-produced; otherwise "unidentified".
    counts gains proposed_autonomous (PROPOSED and autonomous only).
V4  Additive optional file-level field:
    site_inventory: {complete: bool, basis: non-empty string,
                     pose_epoch: string|null}
    The merged run is complete only if every observation file states
    complete: true. Otherwise a would-be PROPOSED becomes AMBIGUOUS with
    reason INVENTORY_INCOMPLETE. Matcher API: explicit inventory argument;
    absent means incomplete (fail-closed).
V5  New adapter in matcher/ (e.g. sync_from_manifest.py) that reads a
    snes02-sync-manifest-v1 file read-only and writes an anastylos-sync-v1
    file to --out: from_clock "cam1-pts", to_clock "cam2-pts",
    offset_min/max_ms = offset_video_pts_s*1000 -/+
    uncertainty_s_conservative_cross_camera*1000 (SNES-02: 698.96/838.96),
    validated true only if the manifest lists visual_verification entries,
    basis_ref = manifest path + its SHA-256. Never modify the manifest.

Do not change: occlusion behavior (V3), loose-part handling (V6),
removal-evidence semantics (V7).

Acceptance gates (all required):
  - All 22 existing tests pass with unchanged assertions. Only the shared
    test helper may pass an explicit complete inventory; that change must
    be visible in the diff.
  - New tests: adversarial P2 (site-1 present 0-100 ms, unattributed hand
    grasp 200-300, deposit 600-700, site-1 absent 5000-5100) gives AMBIGUOUS
    + INVENTORY_INCOMPLETE without inventory, and PROPOSED with complete:true
    (documents that a false completeness claim reopens P2); autonomy
    autonomous / operator_assisted / unidentified; V1 field validation
    (bad role, unknown basis id); V5 adapter values and basis hash on the
    committed snes-02/sync-manifest.json.
  - py -3.12 tools/compliance/check.py: 0 errors.
  - No observation files produced. No files outside matcher/ changed.
Report: commits, git diff --stat, exact test command and counts, any
contract question you could not settle (raise it, do not decide it).
```

---

## Brief A1 — Muse A: bounded SNES-02 unique-component inspection (132–181 s)

```text
Role: Muse A. Exclusive ownership: snes-02/** and
C:\ANASTYLOS\data\snes-02\custody-u\** . Read-only use of the custody
observer in C:\Users\HP\Documents\Anastylos-custody (do not modify that
worktree). Time box: 4 hours total, then report whatever state you have.

Question: does the unchanged custody observer, run on SNES-02 CAM1, produce
any correct automatic link for the unique parts (buttons, pads, D-pad)
deposited between 132 s and 181 s?

Preconditions (stop and report if any fails, no workaround):
  1. Frozen software hashes match docs/PROTO-01.md §2.
  2. Sheet registration on CAM1 passes with the calibration kit config
     (check marker dictionary and ids against calibration-config.json first).
Before running the observer:
  3. Write gt/snes02u.gt.json: for each unique part moved in 132–181 s, its
     source well label and destination cell, from your existing stills.
     Mark it non-blind (you have seen the video). Hash it.
  4. Write the custody-site-inventory@1 file for the wells, read from a quiet
     frame at or after 132 s, basis disclosed. Hash it.
Run (frozen, from Anastylos-custody\custody-ledger with Anastylos\.venv):
  python custody.py observe --video <CAM1 original> --inventory <inv> --out <run>
  python custody.py link --events <run>\events.json --out <run>\custody.json
Evaluate only links and site events inside 132–181 s.

Not allowed: parameter or code changes, new dependencies, paid inference,
anastylos-observation-v1 exports (V1/V2/V4 not implemented yet), edits to
ARCHITECTURE.md or other lanes' files.
Deliver: appended section "§11 SNES-02-U (exploratory)" in
snes-02/OBSERVABILITY_REPORT.md with correct / incorrect / ambiguous /
unattributed link counts, sources detected, interventions, all hashes,
and the label "exploratory, development data, non-blind ground truth".
A correct link here does not satisfy the MVP gate; PROTO-01 does.
```

---

## Brief A2 — Muse A: PROTO-01 producer (after A1, when the owner's video exists)

```text
Role: Muse A, PROTO-01 producer. You never receive proto01.sealed.json or
proto01.cells.json.
Follow docs/PROTO-01.md §5 (pre-flight on the owner's 15 s setup clip) and
§7 (processing). Verify §2 hashes before each run. Write the site inventory
from the owner's labelled hole photo before running observe. Outputs to
C:\ANASTYLOS\data\proto-01\<run>\ with SHA-256 of every output file.
Hand the frozen outputs and hashes to the Technical Lead; do not score.
Stop and report on any hash mismatch or registration rejection.
```
