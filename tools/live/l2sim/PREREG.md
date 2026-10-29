# L2-SIM preregistration — L2-AGENT-02 replay comparison

Status: preregistered before any real model run (no Muse Spark call has been made by this lane). Written and
committed before the simulator code it describes, so every parameter below is fixed by this file, not tuned to
results. A later change needs a new commit that appends to the changelog at the end, made before the L2 run,
stating what changed and why; changes after an L2 run invalidate the comparison.

Spec: `tools/live/seed/L2-AGENT-02.md`; action schema `tools/live/schemas/task.next_action.json`; seam
`tools/live/CONTRACT.md` §3.1.

## 1. Question

On replayed and synthetic episodes, does the L2 arm (`muse-spark-1.3-contributor`, effort `max`, through an
injected Proposer) choose next actions that resolve more parts per operator interruption than P0 and PR,
with zero integrity violations and an invalid-action rate ≤ 5 %?

## 2. Arms

- **P0** deterministic: before segment end every question that needs a decision is deferred to
  `segment_end`; at segment end, questions are handled one at a time in deposit order (question open time,
  then id): leave unresolved with `no_candidates_and_operator_unsure` if it has zero candidates and the
  operator said "weiß nicht"; leave unresolved with `budget` if its attempts reached the per-question limit
  or the question budget is spent; otherwise ask. P0 never issues `protocol_prompt` or `ask_dependency`.
- **PR** random-valid: uniform over the concrete list of currently valid actions (the same list L2 sees),
  `random.Random(seed)` with the replication seed.
- **L2**: the Proposer gets `task="next_action"`, payload `{"state", "valid_actions"}` (structured state only,
  operator labels as opaque tokens `label#k`, no free text). Any non-`ok` status, any output failing the code
  re-validation of the output schema, or any action failing the validator → the P0 action is executed; model
  action, verdict and executed action are journaled. `timeout`/`provider_error` may be retried (retry limit 3
  per episode); `disabled`/`budget_exhausted` switch to P0 for the rest of the run.
- `request_view` is disabled (AgentCam G1) and is never in the valid list; proposing it is a forbidden action.

## 3. Episodes (n = 13) and replications

- FULL-01 v21 phases P1, P2, P3 (12 questions) from
  `C:\ANASTYLOS\data\session-runs-v21\FULL-01\P1..P3\questions.json` and `parts.json`, ground truth from
  `audit\apply_input.jsonl` (owner answers; development data, not independent validation). Input SHA-256 is
  recorded in every results file. Unprompted owner statements (`question: "EXTRA"`) are not questions and are
  not simulated.
  - A question opens at the end of its deposit window (`source_of_part`) or removal window
    (`destination_of_removal`). Operator busy = union of all deposit, candidate-removal and removal windows,
    each padded by 2 s; everything else is idle; a busy→idle transition is a pause. Segment end = last
    event + 5 s. All revisions start at 1. Parts the linker already wrote (P3-C03, P3-C06) are committed
    parts at start.
  - Truth: `choice` → that candidate; free-text `source` or `destination` → declared; `not_a_part` → not a
    part. No FULL-01 question is resolvable without the operator.
- Synthetic, 5 families × generator seeds {1, 2} = 10 episodes: `stale_revisions` (revision bumps, some
  while the question is asked), `simultaneous` (8 questions open within 2 s), `unanswerable` (one zero- and
  one three-candidate question the oracle can never answer), `busy_bursts` (busy ≈ 70 % of the time, 3 s
  pauses), `resolvable` (two questions become resolvable by the linker mid-episode).
- Replication seeds {0, 1, 2}: oracle seed and PR policy seed. 13 × 3 = **39 episode-runs per arm**, paired
  (identical episodes and oracle random streams across arms).
- Supplementary only (not for acceptance): P0 and PR on replication seeds 0–9.

## 4. Oracle operator and cost model (fixed)

- The oracle sees only the asked question id, its revision, the candidate tokens and whether the operator is
  busy; never model output, policy identity or the state.
- Random stream per (replication seed, episode, question, attempt), so arms face identical draws.
- Abstention ("weiß nicht"): 0.10 when idle, 0.20 when asked while busy; unanswerable questions: 1.0.
- Error (wrong answer): 0.05; a wrong answer is caught at read-back with probability 0.5 (question reopens),
  otherwise committed as a wrong commit.
- Timing: answer arrives 8 s after the ask, read-back ends 3 s later (question in flight 11 s; read-back open
  in the last 3 s). Operator seconds: ask 11 s (+6 s if asked while busy), `ask_dependency` 10 s,
  `protocol_prompt` 4 s, `defer` and `leave_unresolved` 0 s.
- A revision change while a question is asked supersedes the pending answer (never committed); the question
  reopens at the new revision.

## 5. Validator, budgets and limits (fixed)

Preconditions per the seed table, fail-closed: ids exist at the current revision; at most one question ASKED;
`ask` on OPEN/DEFERRED, attempts < 2 per question, question budget = 2 × questions per episode; `defer` on
OPEN/ASKED with deposit age < 600 s and not after segment end (an expired deferral returns the question to
OPEN); `protocol_prompt` ≤ 1 per 60 s, not during an open read-back, budget 2 per episode, not after segment
end; `ask_dependency` only for parts committed in this session and not already asked; `leave_unresolved`
only after ≥ 1 attempt, or zero candidates plus "weiß nicht", with the reason matching the state. Turn limit
per episode = 3 × questions + 6; the policy gets a turn only while a question needs a decision. Hard proposer
call cap for the whole L2 run: **910** (v2; was 600 in v1, see changelog).

## 6. Metrics

Per arm, pooled over the 39 episode-runs (sums, then ratios), plus per-episode values:

- **Primary: resolved per interruption** = resolved parts / interruptions. Resolved parts = operator commits
  whose answer matches the truth + questions the linker resolves at segment end (synthetic `resolvable`
  only). Interruptions = executed `ask` + `ask_dependency` + `protocol_prompt`.
- Secondary: resolved per question interruption (asks only), unnecessary questions (ask on a question already
  marked resolvable), wrong-target questions (proposed `ask`/`defer`/`leave_unresolved` on an unknown id, a
  stale revision or a question not in a state that allows it), invalid-action rate, validator rejections by
  reason, forbidden proposals, simulated operator seconds, proposer calls and calls per resolved part, wrong
  commits, left unresolved, open at end, asks while busy.
- Effect unknown: executed `protocol_prompt` (observability effect cannot be simulated from footage) and
  `ask_dependency` (no dependency ground truth in FULL-01) are counted and cost time but contribute nothing
  to resolution.
- Integrity (must be exactly 0): memory writes by the model, commits without a confirmed read-back, stale
  actions executed, stale answers committed.

## 7. Acceptance (L2 → candidate for a live trial)

All must hold on the primary 39 episode-runs:

1. L2 pooled resolved-per-interruption > P0 and > PR (strict).
2. Integrity counters all 0 for L2.
3. Invalid-action rate ≤ 5 %: ok-status outputs rejected by code schema check or validator / ok-status outputs.
4. The run is complete (all 39 episode-runs, call cap not hit) and the provider fallback rate (non-ok statuses
   / L2 decisions) ≤ 20 %; otherwise the result is **inconclusive**, not a failure or success.

Otherwise L2 stays research. With n = 39 paired runs no statistical significance is claimed; per-episode
win/tie/loss counts are reported beside the pooled ratio. The result says nothing about `protocol_prompt` or
`request_view` usefulness, nor about real operators.

## 8. Commands

```
cd tools/live/l2sim
python run.py --arm P0 --seeds 0,1,2 --out C:\ANASTYLOS\data\live-dev\D\<run>
python run.py --arm PR --seeds 0,1,2 --out C:\ANASTYLOS\data\live-dev\D\<run>
python run.py --arm L2 --proposer <module:factory> --seeds 0,1,2 --max-calls 910 --out C:\ANASTYLOS\data\live-dev\D\<run>
python compare.py --p0 <P0 run> --pr <PR run> --l2 <L2 run>
```

## Changelog

- v1 (491183cd): initial preregistration.
- v2 (with the simulator code, still before any model run; no L2 result exists):
  - Hard call cap 600 → **910**. 600 was below what the arm can legitimately need: the turn limits sum to
    3 × (3 × 62 + 6 × 13) = 792 turns over the 39 runs, plus at most 3 retries per episode-run (117), so 909
    calls is the bound. The deterministic good fake already used 573 calls. 910 means the cap can only
    stop a run that violates the turn/retry limits, never truncate a regular one. Supersedes "600" in §5
    and §8.
  - Made explicit (no behaviour change against v1's intent): `defer` and `leave_unresolved` carry
    `question_revision` like `ask`, so every question action is checked for staleness ("ids exist at the
    current revision", §5); after segment end the policy gets no turn while a question is in flight; a
    turn ends with no executed action if neither the proposal nor P0 yields a valid one; the arms are
    compared with `compare.py`, which refuses unpaired runs or runs made under different PREREG hashes.
