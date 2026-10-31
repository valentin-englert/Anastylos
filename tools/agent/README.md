# tools/agent — ANASTYLOS agent (product orchestrator)

Owner: TL (`tools/live/CONTRACT.md` §1, contract 0.3). One entry point that carries a disassembly session through
the existing modules; it adds no evidence logic of its own. Architecture A2: the deterministic core decides, Muse
only proposes (and is never on the path between a question and the operator's answer).

    python tools/agent/anastylos.py new    --session C:\ANASTYLOS\data\agent\<id> --object "SNES controller" --operator "<name>"
    python tools/agent/anastylos.py phase  --session <dir> --tag P1 --label "while unscrewing the back cover" --video P1.mp4 [--python <venv python>]
    python tools/agent/anastylos.py status --session <dir>
    cd tools/agent && python -m unittest discover -s tests

## Session folder

- `agent-session.json` (`anastylos-agent-session@1`): object, operator, phases (tag, operator label, video path
  and SHA-256, run folder, status `running`/`done`/`failed`, counts from `parts.json`). Written atomically.
- `memory/` — the session's Assembly Memory (`memory.jsonl`), appended by `tools/session/session.py`.
- `runs/<tag>/` — one session-runner output per phase (`parts.json`, `questions.json`, evidence).
- `phase-words.json` (`anastylos-live-phase-words@1`) — the operator's phase labels, for the live coordinator.
- `live/journal.jsonl` — the coordinator journal (`anastylos-live-journal@1`, CONTRACT §3.4, 0.2 fields).
- `provider/` — after `prepare --muse` or `guide ask --muse`: shared request-hash cache and sanitized traces.
- Optional `agent-session.json.muse_max_calls` stores the absolute session HTTP-attempt limit (not evidence).

## Rules it enforces

- `session.py` (E3-B frozen) runs only as a subprocess with the OpenCV venv; it cannot resume (B10), so a
  phase tag runs once, a failed run keeps its folder, and an unfinished phase blocks the next one.
- The run's video hash must equal the hash recorded before the run.
- Sessions live outside the repository and never inside an active memory named by
  `C:\ANASTYLOS\data\active-memory.json`.

## Question loop (`ask`, M1)

- English is the product language for every operator-facing line (questions, options, read-backs, messages,
  CLI). Operator labels and places are kept verbatim as evidence, in whatever language they were given.
- P0 (`ask_loop.py`) picks the next question: in-flight after a restart first, then open questions in live order,
  then one more round for deferred ones. A deferred question with no candidates that the operator did not know,
  or one asked twice, is left unresolved (journaled reason). Three unusable replies defer a question.
- Replies: free text (`<label> from <place>`, `<label> from B`, `one`, `option b`, `<label> to cell 4`, `not a part`,
  `skip`, `I don't know`) or a screen letter. The read-back takes only `yes` / `no` (and close variants);
  anything else repeats it. `no` asks the question again; nothing is written until `yes`.
- `prepare --muse` (optional, paid) prefetches L1 wording between phases. `ask` uses a prefetched text only for
  the same op_key (revision) and unchanged L0 facts and journals a `stale` fallback otherwise; the loop itself
  never calls a model.
- `status` reports `complete: false` with counts and reasons while a phase failed, questions are open, or
  questions are `LEFT_UNRESOLVED`. Ending the question round does not mean the disassembly is documented.
  `COMMITTED` resolves a question (including a confirmed non-part); `SUPERSEDED` retires an old revision and
  is not itself an unresolved question. Only the latest revision contributes to the question state.

## Declarations, guide and reassembly (M2)

- `declare` is the operator's own statement, not a P0 question: an extra part (`--phase --label --to [--from]
  [--at]`, validated by `answers.plan` like an `EXTRA` answer) or interchangeable parts (`--group <ids>`). English
  read-back, explicit yes, then the live core's write-ahead writer (`MemoryWriter`, used unchanged) under the
  session lock; journal `declare/journal.jsonl`. A newer declaration makes an unconfirmed one stale; an identical
  committed declaration is refused; a crash at any write point recovers to exactly one record.
- `guide build` runs `tools/guide/package.py` and appends the package SHA-256 and the memory SHA-256 to
  `guide/builds.jsonl`. Answers and reassembly progress name that hash; a package whose bytes differ from the last
  build record is refused (“changed after it was built”). The current memory bytes must also match the build
  record before an answer cache or install plan is used. Changed/missing memory makes the guide unusable with
  an English instruction to run `guide build` again. Rebuilding is explicit. Old answers and progress stay in
  their append-only logs; only records with the current package hash are reused. With unchanged memory, cache
  and assembly resume continue to work.
- `guide ask` answers from claims only: L0 maps `where is <cell/id/label>`, `where does <x> go`,
  `what comes before <x>` to claims; with `--muse`, other questions get model claims within 60 s (else the
  English refusal). Every claim is checked by `tools/guide/claims.py`; text is rendered by `guide_render.py`
  (operator labels and places quoted verbatim, automatic parts “assigned by the system ..., not observed”).
- `assemble` shows the install steps of the package one at a time (each from verified claims; a group step says
  that no part is matched to a particular place). `done` advances, `stop` pauses, `skip` is refused, other text is
  a guide question. `assemble --returns <returns.json>` applies a `tools/reassembly/returns.py` check: a mismatch
  reopens its step, an ambiguous or unattributed return changes nothing.

## Muse budget (preparation and guide)

`--max-calls N` sets the **absolute total HTTP-attempt limit for this session**, shared by `prepare` and
`guide ask`, not N additional calls per command. Both commands use the saved limit when omitted, initially
12. An explicit value replaces that total, including when deliberately raising it; repeating the same value
or omitting it never refills the budget. A deliberately exhausted smaller limit stays exhausted after restart.
Both model commands report `muse_budget` (`limit`, `used`, `remaining`).

The existing provider ledger `provider/traces.jsonl` counts all historical attempts, including retries and
failures; provider cache hits consume zero attempts and may still be served at the cap. The guide answer
cache also consumes no attempts. No alternate provider folder or provider rule is introduced.
Migration: older sessions without `muse_max_calls` start with total 12 and retain all attempts in their existing
traces. The old command-local guide default of 2 was never stored, so it cannot be inferred as a deliberate
session limit; pass `--max-calls 2` explicitly to establish it. No historical trace/cache is rewritten.

## Status (M1, 2026-10-30)

Implemented (M2): `new`, `phase`, `prepare`, `ask`, `declare`, `guide build|ask`, `assemble`, `status`.
Next per roadmap: M3 session page and report, real recording.

M1 validation (development data, no hardware claim): 30 tests (`tests/test_agent.py`, `tests/test_questions.py`):
the twelve regular FULL-01 questions answered in English reproduce `answers.apply` event for event; a crash at
each of 8 points (twice per run) recovers to the same memory with no orphan or duplicate; journals pass
`tools/live/integration/journal_check.py` and the LIVE-01 scorer's integrity counters are all 0. CLI run on a
FULL-01 copy (`C:\ANASTYLOS\data\agent\m1-full01-en`): 12/12 committed, complete; `prepare --muse` used 3 calls,
1 wording accepted and used, 2 rejected by the gate (no screen pointer).

M0 validation: 12 unit tests with a fake runner, plus one development dry run on the FULL-01 P3 video (93 s):
`C:\ANASTYLOS\data\agent\dryrun-m0-P3`, 4 min 07 s (2.65× video length), counts 7 parts / 2 linked / 2 written /
6 questions; `parts` and `questions` identical to the active v21 P3 run. Development data; no hardware claim.

## Stabilization validation (2026-10-31)

Base: `9b4786e12eda97bb31eb4a3460d4f1c302695903`. Before fixes the added regressions reproduced all three
bugs (3 failures, 2 errors from the missing budget report; 1 passing control). Seven new tests cover the actual
`summary()` value, terminal-state distinctions, stale cache/plan rejection, explicit rebuild/history retention,
preparation then guide with fake HTTP, retries, restart, cache hits, legacy traces and absolute limit changes.
The existing package-tampering test remains unchanged. No provider implementation or research fixture changed.

Verification: `python -m unittest discover -s tests` in `tools/agent` (57), `tools/guide` (51), `tools/live` (57),
`tools/live/providers` (27), `tools/live/integration` (12), `tools/reassembly` (11): all passed, no failures,
errors or skips. Baseline agent suite: 50 passed. Windows sandbox tests use a writable external `TEMP`/`TMP`;
the default sandbox temp directory denied writes before the baseline could run.

A separate fresh FULL-01 fixture copy completed 15 CLI commands: 12 confirmed question answers, an extra
confirmed declaration, guide build, two supported questions, one unsupported question refused, assembly pause
and resume to completion, another confirmed declaration, stale status/cache/assembly refusal, explicit rebuild,
and the new part answered. New progress starts at step 1; old package progress remains historical.
This is **offline replay on development data**, not hardware acceptance or a real Muse call. Replay outputs
and SHA-256 manifest are retained outside the repository in this task's visualization artifacts.

E3-B §2 frozen files in the task worktree: 20/20 byte hashes before and after. The untouched original
checkout has CRLF in `tools/proto-01/e3b_grid_frame.py` (19/20 raw byte matches, content equals the frozen LF
file after line-ending comparison only); it was not rewritten. FULL-01 fixture `audit/apply_input.jsonl` has CRLF
in the Windows checkout, so its byte hash differs from its historical manifest; its Git blob matches the
manifest. Neither bytes nor manifest were normalized. The other seven fixture byte hashes match.
Compliance: `python tools/compliance/check.py` reports 0 errors, 1 warning (no worktree `.venv`, installed
environment check skipped), 18 existing open reviews. `python -m unittest test_check` in `tools/compliance`
passes 5 tests; its selftests are not in a `tests/` directory. No dependency or license inventory changed.

Remaining integration limits for a real Muse run: `guide.py::_propose_with_timeout` stops waiting after 60 s
but does not cancel the provider thread; `providers/meta.py::MetaProposer` persists attempt counts only when
the call finishes and does not serialize concurrent CLI processes. Run one model command at a time and let
in-flight work finish before retrying; concurrent/crash-in-flight budget hardening is outside this stabilization.
Provider retention is unchanged. The camera-to-memory-to-guide-to-Muse hardware workflow remains
PENDING_EVIDENCE; no new model capabilities or real API validation are claimed.
