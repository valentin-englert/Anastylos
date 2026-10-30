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
- `provider/` — only after `prepare --muse`: request-hash cache and sanitized traces of the wording calls.

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
- `status` reports `complete: false` with reasons while a phase failed or questions are open.

## Declarations, guide and reassembly (M2)

- `declare` is the operator's own statement, not a P0 question: an extra part (`--phase --label --to [--from]
  [--at]`, validated by `answers.plan` like an `EXTRA` answer) or interchangeable parts (`--group <ids>`). English
  read-back, explicit yes, then the live core's write-ahead writer (`MemoryWriter`, used unchanged) under the
  session lock; journal `declare/journal.jsonl`. A newer declaration makes an unconfirmed one stale; an identical
  committed declaration is refused; a crash at any write point recovers to exactly one record.
- `guide build` runs `tools/guide/package.py` and appends the package SHA-256 and the memory SHA-256 to
  `guide/builds.jsonl`. Answers and reassembly progress name that hash; a package whose bytes differ from the last
  build record is refused (“changed after it was built”), and answers cached for another hash are not reused.
- `guide ask` answers from claims only: L0 maps `where is <cell/id/label>`, `where does <x> go`,
  `what comes before <x>` to claims; with `--muse`, other questions get model claims within 60 s (else the
  English refusal). Every claim is checked by `tools/guide/claims.py`; text is rendered by `guide_render.py`
  (operator labels and places quoted verbatim, automatic parts “assigned by the system ..., not observed”).
- `assemble` shows the install steps of the package one at a time (each from verified claims; a group step says
  that no part is matched to a particular place). `done` advances, `stop` pauses, `skip` is refused, other text is
  a guide question. `assemble --returns <returns.json>` applies a `tools/reassembly/returns.py` check: a mismatch
  reopens its step, an ambiguous or unattributed return changes nothing.

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
