# ANASTYLOS — 3-minute demo script (English)

Total: ≤180 s. Language: English. No offline answer is presented as live model
performance. Drawing and new UI appear only as explicitly open scenes unless
their acceptance is cited.

Base: main after the 2026-10-31 consolidation (`b522336d` + report, capture-gap
planner, session UI, submission docs). Evidence: E2 sealed 4/4, GUIDE-03/
HARDWARE-03 bounded capture, hash-bound selection, STORED-IN-V2-01 bounded Muse
demo, offline UI/report/gap acceptance on FULL-01 copies. All else is labeled
development/synthetic/open.

## Scene 1 — Problem (0:00–0:25, 25 s)

**Line:** “Disassembly is easy. Reassembly fails on one question: which part
came from where, and where is it now?”

Show a controller with tray cells. State the three rules: evidence with
provenance, explicit `unknown`, human confirms. No autonomy claim.

**Artifacts:** tray sheet `surface_a3`; one E2 cell photo reference
(`docs/PROTO-01-NOUV.md` §10).
**Fallback:** still photo of tray + device if live camera fails. No claim lost.

## Scene 2 — Real disassembly evidence (0:25–1:10, 45 s)

**Line:** “On a sealed protocol take, the frozen observer linked four deposits
to their sources, zero wrong. On natural footage it asks instead of guessing.”

Show: E2 run (4 correct / 0 incorrect / 1 unattributed, STRONG PASS, score
`2691d41d…`); memory `memory.jsonl` `867fe05d…` with four observations and four
owner-confirmed tray links; `where --tray` returns `located` for all four.
Then one FULL-01 natural question (“not observed”) to show honest uncertainty.

Capture honesty: “Guided auto-shutter fired once per arming on Xiaomi
(GUIDE-03, 5/5 + negatives); Receiver transfer was hash-identical on Samsung
(HARDWARE-03). Metric reconstruction and the full camera→memory→guide→Muse loop
are still open.”

**Artifacts:** `docs/PROTO-01-NOUV.md` §9–§10 + §12 hashes; `GUIDE-03.md`;
`HARDWARE-03.md`.
**Fallback:** pre-rendered score table + memory excerpt (hashes visible). Do not
re-run video live.

## Scene 3 — Part selection (1:10–1:45, 35 s)

**Line:** “Pick one declared part. The selection binds exact bytes, not a name.”

Run read-only `select` on byte copies of S memory + guide with an explicit
`anastylos-drawing-selection@1` binding (event `dec-0006` back shell / `dec-0011`
PCB in the offline validation). Show success JSON: separate `original_location`
and `stored_location`, bases, memory/guide SHA, limits. Then show a refusal:
same binding against relocated W / replay R → STOPPED, exit 1, empty stdout.

State: “No composite, no alias, no frame→record confirmation. A missing origin
stays null.”

**Artifacts:** `tools/agent/selection.py`; offline validation (agent 76 PASS,
16 selection tests); S/R/W byte-copy refusal demo.
**Fallback:** static JSON excerpts with hashes. Label as replay, not live.

## Scene 4 — Traceable answer with Meta Muse (1:45–2:25, 40 s)

**Line:** “Muse proposes claims. Code verifies. A fixed local renderer speaks.”

Ask only recorded origin/storage for the selected part. Show the minimized
context (`anastylos-selected-part-context@2`: ID, label, memory hash, typed
origin/storage, allowlisted IDs, limits; no images/paths/names). Show the gate:
only `removed_from`/`stored_in` or `unknown` with the exact ID; origin object
must be null so the full local description is quoted.

Concrete Meta contribution (labeled replay, not live):
STORED-IN-V2-01 — one authorized real call, retries off, exact stored location +
operator basis returned, both verifiers accepted, no cache/fallback; model
8.672 s, CLI incl. drain 8.734 s; budget 4, used 3→4, remaining 0.
Model `muse-spark-1.3-contributor`, effort `max`, strict `json_schema`.

Then show the honesty path: same question on a replay PCB with missing origin →
`unknown` + `insufficient_evidence`, spoken as “not observed.” Offline `--claims`
is labeled `source: offline_claims`, never Muse.

**Artifacts:** `tools/agent/selected_guide.py`; `tools/guide/claims.py`;
provider fixtures (`tools/live/providers/tests/fixtures/recorded/MANIFEST.json`,
4 synthetic-input files); `storage-contract-proof/verification.json` summary.
**Fallback:** if no network/key, run L0 + cached proof only, on-screen label
“OFFLINE / CACHE — NOT A LIVE MODEL CALL.” Never relabel.

Latency honesty (one line): “L2 median 21.7 s / p95 64 s / max 114 s on the
2026-10-29 arm; provider bound 367 s per proposal; answer deadline 60 s with a
fixed fallback. Latency is not process time.”

## Scene 5 — Reassembly help + limits (2:25–3:00, 35 s)

**Line:** “The guide narrates only memory, step by step, and reopens a step on
mismatch.”

Show `guide build` (package + memory SHA in `builds.jsonl`), then `assemble`:
one install step at a time from verified claims; group step says no part is
matched to a place; `done` advances, mismatch reopens, ambiguous return changes
nothing. State P0 stays active (L2 0.752 < P0 0.803, rejected per PREREG).

Open scenes (explicit): “Technical drawing: open. A2 front shows a partial
contour gain, not full accuracy; back shell/PCB need hands-free re-capture;
separate viewports + photo overlay pending. E3-B is preregistered, not
recorded. Gap closure needs a technical image check that does not exist.”

Optional M3 beat (offline, ≤30 s, replaces nothing above): open the localhost
session page on a FULL-01 copy, answer one question, build the guide, resolve
one bound part, confirm one assembly step, export the report, show one
synthetic capture gap staying `needs_review`. Label every screen “OFFLINE —
NOT A LIVE MODEL CALL.”

**Artifacts:** `tools/guide/GUIDE_RULES.md`; `tools/reassembly/returns.py`;
`tools/live/integration/README.md` (L2 table).
**Fallback:** static install-order list (FULL-01 P3/P2/P1) labeled development
data, plus the open-scene cards above.

## Production notes

- No take names a part identity; say “the part from cell 4” unless the package
  label says otherwise, then “labeled …”.
- Every positive storage/origin line carries its basis (“assigned by the system”
  vs. “according to the operator”) and IDs in brackets.
- If any artifact hash mismatches on stage, stop that scene and show its fallback.
  A hash failure is part of the demo, not a glitch to hide.
