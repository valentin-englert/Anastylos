# tools/live — LIVE-CORE (Lane A)

Deterministic coordinator for live clarification questions (current CONTRACT `anastylos-live-contract@0.3`, A2).
Stdlib only. Files: `states.py` (§3.3 state machine, op_key), `questions.py` (questions.json + revisions),
`templates.py` (German L0 wording ported from `seed/h3r0.py`, rubric R1–R7), `interpret.py` (German operator input →
answers.py answer, read-back), `english.py` (English L0 wording, interpretation, read-back, rubric R1–R8),
`languages.py` (session language: `en` product, `de` historical regression), `gate.py` (L1 gates, schema subset
validator), `journal.py` (`anastylos-live-journal@1`, fsync per line), `lock.py` (OS byte-range lock per memory dir),
`memory_writer.py` (intent, steps, recovery, census), `coordinator.py` (orchestrator + demo CLI).

Language: `Coordinator(..., language="en")` is the default; the German path is unchanged and used by the historical
FULL-01 tests (`tests/support.py` passes `language="de"`). English R2 checks the operator's own phase words, R8
rejects German function words outside quoted operator labels; over-long spoken options go to the screen.
Journal (CONTRACT §3.4, 0.2 fields): `t_wall` on every record; `question_state` carries `question_id`, `state` and
the static fields, ASKED/ANSWERED/CONFIRMED their fields; `CONFIRMED` is journaled before the intent (a confirmation
without intent is reopened as a read-back after a restart); one `memory_result` with `status: ok` and all
`written_ids` per commit, step records carry `status: step`. Prefetched L1 wording (`prefetch_wording`) is a
`provider_call` with a `prefetch` verdict, used only for the same op_key and unchanged L0 facts.

    python tools/live/coordinator.py demo --out C:\ANASTYLOS\data\live-dev\A\<run>
    cd tools/live && python -m unittest discover -s tests

## Proven by tests (synthetic, FULL-01 v21 fixture copies, see `tests/fixtures/MANIFEST.json`)
EO-1 crash at each step / after each of 24 memory calls of the 13 FULL-01 answers → recovery gives the same events as
`answers.apply`; EO-2 duplicate input ignored (also after restart); EO-3 events written but not journaled are found by
marker, not rewritten; EO-4 second coordinator rejected (same and other process), no stale lock after crash; EO-5 torn
journal/memory line → stopped, no truncation; EO-6 stale revision never committed; EO-7 observation without tray link
detected → stopped. Read-back confirmation is L0 only (no model decides a "yes").

## Limits (fail closed; frozen files not changed)
- Current journal records include `t_wall` and the contract 0.2 question fields (see the contract and
  integration suite). Historical logs and the original fixture manifest retain their original bytes,
  including the CRLF-source versus LF-blob mismatch for `audit/memory.before-answers.jsonl`.
- l2sim's `session.py` shadows `tools/session/session.py`; run the simulator in its own process,
  as the integration tests do. The real L2 evaluation remains independently owned; no benefit is claimed.
- Contract 0.3 is implemented. The session UI's drawing capture bridge uses the existing guided receiver;
  this does not close generic capture gaps or establish physical capture-loop acceptance.
- memory.py/store.py do not fsync: a memory line may be lost on power failure. Recovery treats memory as truth and stops
  if the journal acknowledges an event memory lacks.
- No op_key field in memory: the op_key travels in the existing free-text note (`live op_key=...` via `provenance`);
  matching is exact but convention-based.
- The lock excludes only live coordinators; `session.py`, `answers.py apply`, manual `memory.py` ignore it.
- EO-1 bulk test uses `anastylos_memory.cli.main` in-process (same code, no subprocess) for speed.
- Label/place splitting (`<label> aus <place>`) is a heuristic; the read-back is the safeguard.
- The voice cell flow can record confirmed operator-stated extras through `write_operator_notes`, with
  origin separate from storage. It does not identify parts automatically. The demo CLI is L0 only.

## Contract requests for the TL
1. `answer_interpretation` intent `not_a_part` (L0 supports it; schema lacks it).
2. Structured `op_key` field on memory events and fsync in `store.append_event` after E3-B release.
3. A memory-wide writer lock honoured by `memory.py` itself.
