# LIVE-01 — live clarification questions during disassembly (preregistration draft)

Status: draft for preregistration, 2026-10-29, Lane E (EVAL) of `anastylos-live-contract@0.1`. Built from
`tools/live/seed/LIVE-01-design.md` (ea021c0b). It becomes binding when the owner has made the decisions in §13,
the parameters in §4 are filled in, and the commit ledger row 1 (§10) is recorded — all before any LIVE-01
recording. Scoring tool: `tools/live/eval/score_live01.py`; die tool: `tools/live/eval/make_die.py`.

E3-B (`docs/PROTO-01-E3B.md`) is unaffected: LIVE-01 changes none of its frozen files, protocols, scorers or
fixtures. LIVE-01 reuses the E3-B link criterion by importing `score_e3b.judge_link` unchanged.

## 1. Question and claim boundary

When the system cannot attribute a removal or deposit on its own, is it better to ask the operator **during** the
disassembly (live) than in a round **after** it (offline) — more correctly resolved part sources per operator
minute, without lower accuracy — and does model wording (L1, Muse) beat deterministic templates (L0) on answer time
or question quality without more wrong declarations? And does the live coordinator keep memory integrity
(read-back before every write, no stale or duplicate write, no declaration turned into evidence)?

Claim if it passes: for one operator, one unseen object and one take, live questions resolved more part sources
per operator minute than the offline round at no loss of accuracy, with zero integrity failures. Not: a general
effect, an effect size, voice operation, live action selection (L2), or any statement about other operators.

## 2. Hypotheses

| id | statement | comparison | verdict field |
|---|---|---|---|
| H-T (timing) | live questions resolve more parts per operator minute than the offline round, accuracy not lower | live-L0 vs offline (same generator, same wording family; only *when* differs) | `live_timing` |
| H-W (wording) | L1 is not worse than L0 on wrong declarations and better on answer time or H3 rubric | live-L1 vs live-L0, same delivery delay D | `l1_wording` |
| H-S (safety) | the live coordinator commits only confirmed, current, single, declared-only writes, and the operator never scrubs a recording in the live arm | live and offline journals + memory copy | `safety` |

Out of LIVE-01 v1: L2 action selection (AGENT-02 replay first), voice (after VOICE-01). Answers are typed or
on-screen choices only, so the overhead audio carries no answers.

## 3. Frozen software (filled in before recording)

| Item | Commit | SHA-256 |
|---|---|---|
| live coordinator (`tools/live/*.py`, Lane A) | pending | pending (per file) |
| providers (`tools/live/providers/`, Lane C) | pending | pending (per file) |
| `tools/live/eval/score_live01.py`, `make_die.py`, `h3_rubric.py` | the commit adding this document | pending (per file) |
| custody / session / memory | E3-B freeze `7b2606a7` (`docs/PROTO-01-E3B.md` §2), unchanged | as listed there |

Model: `muse-spark-1.3-contributor`, effort `max` (CONTRACT §4). No other model, no lower effort.

## 4. Arms and randomisation

### 4.1 Rounds

- **live round:** the coordinator runs during the take; a question appears D seconds after the deposit (or
  unmatched removal) that triggered it, if its slot is `live`.
- **offline round:** starts `offline_delay_s` after the live round's `done`; asks the questions of `offline` slots
  with L0 wording, showing only the overhead recording (never the witness video), as today.

### 4.2 Slot and ordinal

- **Slot** = 1-based ordinal of the question-triggering event (a deposit the linker could not attribute, or a
  removal with no deposit) in the order the live coordinator detected it. Automatically linked deposits consume
  no slot.
- **Live ordinal** = 1-based ordinal of a live question among live questions reaching `ASKED` (journaled as
  `die_ordinal` on `ASKED`, CR-E1; the scorer falls back to the `ASKED` order).

### 4.3 The die (`live01-die@1`)

`make_die.py` draws a 32-byte secret seed and derives two balanced complete randomisations (equal halves, shuffled
by Fisher–Yates with HMAC-SHA256(seed, `label:i`) draws): slot → `live`/`offline` and live ordinal → `L1`/`L0`.
Balanced complete randomisation instead of pairs: with pairs, a question appearing live would reveal that the
next slot is offline, and the operator could start memorising for the offline round.

```json
{"schema": "live01-die@1", "generator": "tools/live/eval/make_die.py", "created_at": "…",
 "seed_sha256": "…", "randomisation": "balanced complete, HMAC-SHA256 Fisher-Yates",
 "slots": {"1": "offline", "2": "live", "…": "…"}, "live_questions": {"1": "L1", "2": "L0", "…": "…"},
 "delivery_delay_s": "D", "delay_basis_sha256": "…", "offline_delay_s": 600}
```

The die file goes to the coordinator; its contents are never shown to the operator. The seed file
(`live01-die-seed.SEALED.txt`) stays sealed until scoring; the scorer re-derives the die from it (`--die-seed`).

### 4.4 Fixed delivery delay D

Parameter **D** (`delivery_delay_s`), named here, fixed before the die is made: D ≥ the p95 latency of
non-cached `question_wording` calls in Lane C's traces (`anastylos-live-provider-trace@1`, ≥ 20 calls on the
development cases), rounded up to whole seconds, plus the margin chosen in §13. Both L0 and L1 questions are shown
at deposit + D; an L1 result not ready at D falls back to L0 (journaled as `fallback`). The trace file's SHA-256 is
recorded in the die (`delay_basis_sha256`). Tolerance in scoring: |shown − deposit − D| ≤ 1 s.

### 4.5 Intention to treat

Questions are analysed by **assigned** wording. An L1 question delivered as L0 after a journaled `fallback` stays
in the L1 arm and is counted under `providers.fallbacks`; a mismatch without a fallback record is a contamination
flag.

## 5. Contamination controls

| rule (from the seed) | control | scorer flag |
|---|---|---|
| each part asked exactly once, in one mode | slot die; no offline re-ask of a live slot | `part_asked_more_than_once` (two questions target the same true part, or one question reaches `ASKED` in both rounds), `arm_mismatch` (question's round ≠ die arm of its slot) |
| answers before ground truth | all journals + memory copy hashed (`seal`) before the owner opens the witness video; the hash goes into `live01-actual@1` | `answers_not_sealed_before_truth` |
| no script-derived recall | operator chooses order and cells freely; no rolled script | not scoreable; recorded as a deviation if broken |
| unseen object | not the SNES controller, not the E3-B object, not disassembled by the operator before | not scoreable; recorded in §10 |
| delay parity offline | offline round starts `offline_delay_s` after the live `done` (± 60 s) | `offline_delay_violation` |
| latency must not pass for wording | both arms shown at deposit + D | `delay_parity_violations` |
| wording follows the die | `ASKED.wording` = die, or journaled fallback | `wording_mismatch` |
| journals belong to this die | `session_start.die_sha256` = SHA-256 of the die file; seed re-derives the die | `die_hash_mismatch`, `seed_problems` |
| autonomy not inflated by declarations | automatic run without any declared input; assisted run separate | automatic run containing `operator_assisted` links blocks H-T |

Any of `die_hash_mismatch`, `part_asked_more_than_once`, `arm_mismatch`, `answers_not_sealed_before_truth`,
`offline_delay_violation`, `seed_problems`, an `operator_assisted` link in the automatic run, or an answer still
needing adjudication makes H-T **INCONCLUSIVE**. H-W is INCONCLUSIVE on any of the first four, `seed_problems`,
`wording_mismatch` or `delay_parity_violations`.

## 6. Metrics (exact definitions; constants in `score_live01.py`)

### 6.1 Question verdicts

A question counts once it reached `ASKED`. Its **true targets** come from `live01-actual@1`:
- `source_of_part`: steps with `destination` = the question's cell and `deposit_t_s` inside `deposit_window_s`
  ± 2 s;
- `destination_of_removal`: steps with `removal_t_s` inside `removal_window_s` ± 2 s and `source_point_mm` inside
  the question's `source_box_mm` grown by 3 mm.

No target → **false-alarm question**. The answer is the `resolved` object of the last `CONFIRMED` state whose
`op_key` was committed (`memory_result` `ok`); a confirmed `no_part` needs no commit.

| verdict | rule |
|---|---|
| `correct` | `source`: a target's source point lies in the answered `source_box_mm` grown by 3 mm; `destination`: the answered destination equals a target's destination; free words: owner adjudication `correct` |
| `wrong` (wrong declaration) | a committed declaration that is not correct, including any declaration on a false-alarm question |
| `correct_dismissal` / `wrong_dismissal` | `no_part` on a false-alarm / on a real target |
| `needs_adjudication` | only free words (no box, no destination) and no adjudication |
| `unresolved` | nothing committed or confirmed (deferred, left unresolved, failed) |

Boxes are `[x, y, w, h]` in sheet mm (origin top-left, +x right, +y down), as in `score_e3b.py`.

### 6.2 Primary: correctly resolved part sources per operator minute

Per arm: **correct parts** = distinct true parts with at least one `correct` question; **operator minutes**:
- live arms: Σ over the arm's questions of (first `CONFIRMED`, else last side state) − first `ASKED`;
- offline: the offline round's wall time, `done` − `session_start` (seed: "plus offline-round time");
  `offline_by_episodes` reports the same Σ-of-episodes definition as live for comparison.

`resolved_parts_per_minute = correct parts / operator minutes`; `minutes_per_resolved_part` its inverse;
`accuracy = correct / (correct + wrong)`. Arms reported: `live_L0`, `live_L1`, `live`, `offline`,
`offline_by_episodes`.

### 6.3 Secondary

- answer time: `ANSWERED` − `ASKED` (median per arm); deposit to answer: `ANSWERED` − `deposit_t_wall`;
- H3 rubric R1–R7 on the delivered text (`h3_rubric.py`, ported from `seed/h3r0.py`): pass rate per arm;
- L1 vs L0: answer time, wrong-declaration rate (wrong / asked), H3 pass rate;
- provider: calls per task and status, cache hits, `question_wording` latency median / p95 / max (non-cached `ok`),
  fallbacks by reason, grounding rejections (fallback reasons starting with `grounding`);
- recoveries and torn final journal lines.

### 6.4 Safety (all must hold; `safety.verdict`)

| metric | limit |
|---|---|
| false-alarm questions / asked, per round | ≤ 25 % |
| declarations promoted to evidence | 0 |
| scrubbing in the live arm | 0 |
| stale commits | 0 |
| duplicate commits | 0 |
| commits without read-back | 0 |

Wrong declarations vs ground truth are reported per arm (they enter H-T via accuracy and H-W directly).

### 6.5 Integrity counters (exact)

- **declarations promoted to evidence:** an event id in a committed `memory_result.written_ids` that is not
  declared-only: anything with `inference`, any `observation`, a `tray_link` whose basis is not `human`, a
  `compatible_group` whose basis is not `operator_statement`. Allowed: `declared_removal`, `storage_link`,
  human `tray_link`, `operator_statement` group.
- **commit without read-back:** a `memory_result` `ok` whose `op_key` has no `READ_BACK` followed by `CONFIRMED`
  earlier in the same journal.
- **stale commit:** a commit of `op_key` revision r after the journal already held a state of the same question
  with revision > r, or `SUPERSEDED` for that `op_key`.
- **duplicate commit:** every `ok` commit of a question after its first.
- **scrubbing in the live arm:** `ui_event` with `action` ∈ {`scrub`, `replay_open`} in the live journal (CR-E2)
  plus `scrubbing_live` entries the owner records from the witness video.
- also reported (not gated): commits without a preceding `intent`, written ids missing from the memory copy,
  `declared_removal` / `storage_link` events in memory that no commit wrote, duplicate memory ids.

### 6.6 Autonomy accounting

Two session-runner runs on the take, scored with `judge_link` from `tools/proto-01/score_e3b.py` (E3-B rule:
cell, removal time ± 2 s, source box + 3 mm):
- **automatic run** (`--run-automatic`): pure observer + linker, no declared input. Only these links count as
  automatic.
- **assisted run** (`--run-assisted`): the live declarations fed back as `custody-declared-*@1`; links counted by
  `autonomy` (`automatic` / `operator_assisted`), never added to the automatic count.

## 7. Accept / reject (from Research Pass 01, unchanged in substance)

**H-T** (live-L0 vs offline), in this order:

| verdict | rule |
|---|---|
| INCONCLUSIVE | a blocking flag of §5 |
| SAFETY_FAIL | any §6.4 limit broken |
| INSUFFICIENT | an arm has no scored answer or zero operator time |
| REJECT | live accuracy < offline accuracy, or live minutes per resolved part > 2 × offline (or live resolved none while offline resolved some) |
| ACCEPT | live resolved parts per minute > offline and live accuracy ≥ offline |
| NOT_ACCEPTED | otherwise |

**H-W** (L1 adoption): INCONCLUSIVE on a blocking flag; INSUFFICIENT without both L1 and L0 questions;
NOT_ADOPTED if the L1 wrong-declaration rate > L0; ADOPT if L1 median answer time < L0 or L1 H3 pass rate > L0;
NOT_ADOPTED otherwise. H-W is not gated by safety (wording does not write memory); safety is reported beside it.

A failure is reported as such. A changed coordinator is a new version and needs a new take.

## 8. Input files

### 8.1 Coordinator journal `anastylos-live-journal@1` (one file per round)

Kinds from CONTRACT §3.4. Fields the scorer reads (beyond `schema`, `kind`) — those not in the contract are
contract requests (§12):

| kind | fields |
|---|---|
| every record | `t_wall` (ISO-8601 with offset, ms) — CR-E1 |
| `session_start` | `round` ∈ `live`/`offline`, `live_session_id`, `die_sha256`, `delivery_delay_s` — CR-E1 |
| `question_state` | `op_key`, `question_id`, `revision`, `state`; static (any state, last non-null wins): `slot`, `question_kind`, `cell`, `deposit_window_s`, `removal_window_s`, `source_box_mm`, `deposit_t_wall`; on `ASKED`: `wording`, `text`, `anchor_possible`, `anchor_ref`, `die_ordinal`; on `ANSWERED`: `input` (operator-input@1); on `CONFIRMED`: `resolved` = `{outcome: source|destination|no_part, source_box_mm, destination, place_text}` — CR-E1 |
| `intent` | `op_key`, `planned` |
| `memory_result` | `op_key`, `status` (`ok` / other), `written_ids` — CR-E1 |
| `provider_call` | provider-result@1 fields (`task`, `status`, `latency_s`, `cache_hit`, `request_id`) + `op_key` |
| `fallback` | `op_key`, `task`, `reason` (`grounding…` for grounding rejections) — CR-E1 |
| `recovery`, `done` | `t_wall` |
| `ui_event` | `action` ∈ `scrub`, `replay_open`, … — CR-E2 (new kind) |

Window times (`*_window_s`) are seconds on the take's overhead-video clock, as in `parts.json`; durations use
`t_wall`. A torn final line is tolerated and counted; any other unreadable line stops scoring.

### 8.2 Ground truth `live01-actual@1` (owner, after the seal)

```json
{"schema": "live01-actual@1", "object": "owner's word for the object",
 "answers_sha256": "<output of score_live01.py seal>",
 "steps": [{"part": "P1", "source_point_mm": [182, 140], "removal_t_s": 12.0, "deposit_t_s": 14.0,
            "destination": "cell-4"}],
 "scrubbing_live": [{"t_s": 40.0, "note": "what the witness video shows"}],
 "adjudications": [{"op_key": "LS1:Q-3:1", "verdict": "correct", "part": "P3"}]}
```

`source_point_mm`, `removal_t_s` and `destination` as in `e3b-actual@1` (read with `e3b_grid_frame.py`);
`deposit_t_s` is the first second the part rests in its cell. `adjudications` only for free-word answers, listed by
`op_key` (§13 item 6).

### 8.3 Other inputs

- die `live01-die@1` and, at scoring, its seed file;
- memory copy: the session memory the coordinator wrote to, copied after the offline round;
- session-runner output folders of the automatic and assisted runs (`parts.json`, `registry.json`).

Data location: `C:\ANASTYLOS\data\live-01\<take-id>\`; never the active FULL-01 memory.

## 9. Owner steps

**Before the take**
1. Decide §13; TL fills §3 and §4 parameters; commit this document (ledger row 1).
2. Choose an unseen object (6–8 removable parts, each fitting a 26 mm cell; no hidden or recessed fasteners).
   Record its name and a photo hash; do not disassemble it beforehand.
3. Run `make_die.py` (TL may run it; nobody reads the die or seed). Record die and seed hashes (row 3).
4. Set up as `PROTO-01-NOUV.md` §8b (overhead phone) plus the witness phone; coordinator started with the die.

**During the take**
5. Disassemble at your own order and pace; put every part into an empty cell or name its off-sheet place.
6. Answer only questions the screen shows, typed or by choice; confirm or reject each read-back. Never open or
   scrub a recording during the live round. Do not note answers anywhere else.
7. After `done`, wait `offline_delay_s` (timer), then run the offline round on the overhead recording only.

**After the take**
8. TL runs the automatic and assisted session runs and hashes their outputs (row 6).
9. `python tools/live/eval/score_live01.py seal --journal live.jsonl --journal offline.jsonl --memory memory.jsonl`;
   record the hash (row 7). Only now watch the witness video.
10. Write `live01-actual@1` (with `answers_sha256`, any witnessed scrubbing, adjudications), hash it, send the hash
    (row 8).
11. TL scores with `--die-seed` and commits the score (row 9).

## 10. Hash-commit order (ledger)

| row | item | SHA-256 | recorded before |
|---|---|---|---|
| 1 | this document + scorer commit | git commit | anything below |
| 2 | Lane C trace file used for D; D value | pending | making the die |
| 3 | die file, seed (`seed_sha256`) | pending | recording |
| 4 | object name + photo | pending | recording |
| 5 | overhead and witness videos | pending | the offline round's end |
| 6 | automatic and assisted run outputs | pending | the seal |
| 7 | answers seal (journals + memory copy, `seal`) | pending | the owner watches the witness video |
| 8 | `live01-actual@1` | pending | scoring; the seed is unsealed only now |
| 9 | `live01-score@1` | pending | — |

## 11. What n = 1 operator, 1 object can and cannot show

Can: that the live coordinator works end to end on a real take; whether any integrity failure occurs (one
failure is decisive — the limits are zero); the order of magnitude of operator time per resolved part in each
mode; concrete failure cases for the next version.

Cannot: a population effect, a significance statement or an effect size; operator variability; transfer to other
objects; separation of learning or fatigue during the take from the arm (the die balances slots over the take
only in expectation, with about 8 slots); whether the owner, who designed the questions and is the only operator,
answers like a naive user. All results are descriptive.

## 12. Contract requests to the TL (not implemented in other lanes' files)

- **CR-E1** — journal fields of §8.1 not given in CONTRACT §3.4: `t_wall` on every record; `round`,
  `die_sha256`, `delivery_delay_s` on `session_start`; the `question_state` fields listed (note `question_kind`,
  not `kind`, which is the record kind); `written_ids` and `status` on `memory_result`; `reason` on `fallback`.
- **CR-E2** — a journal kind `ui_event` (`action`: `scrub`, `replay_open`) so scrubbing in the live arm is
  measured by the system, not only from the witness video.
- **CR-E3** — the trace format `anastylos-live-provider-trace@1` needs a fixed latency field name (`latency_s`)
  so D can be derived reproducibly.
- **CR-E4** — a statement in CONTRACT §3.3 that `slot` counts question-triggering events in detection order
  (§4.2), and that the coordinator reads `live01-die@1` to decide arm and wording.

Until accepted, the scorer's reading of these fields is provisional and tested only on synthetic journals.

## 13. Owner decisions before recording

1. **D margin:** D = ceil(p95) + margin; proposed margin 1 s.
2. **offline_delay_s:** proposed 600 s.
3. **Die size:** slots and live ordinals; proposed 16 and 16 for a 6–8 part object.
4. **Offline operator time:** whole round (seed, primary) or Σ of episodes (reported as sensitivity).
5. **H-T comparison set:** live-L0 vs offline (seed table, primary) or all live questions.
6. **Free-word answers:** allow them (owner adjudicates after the seal — the owner is also the operator) or
   restrict v1 to screen choices (no adjudication).
7. **Object** and who runs `make_die.py` without looking.
