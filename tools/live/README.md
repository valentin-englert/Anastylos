# tools/live — LIVE-CORE (Lane A)

Deterministic coordinator for live clarification questions (current CONTRACT `anastylos-live-contract@0.2`, A2).
Stdlib only. Files: `states.py` (§3.3 state machine, op_key), `questions.py` (questions.json + revisions),
`templates.py` (L0 wording ported from `seed/h3r0.py`, rubric R1–R7), `interpret.py` (operator input → answers.py
answer, read-back), `gate.py` (L1 gates, schema subset validator), `journal.py` (`anastylos-live-journal@1`,
fsync per line), `lock.py` (OS byte-range lock per memory dir), `memory_writer.py` (intent, steps, recovery, census),
`coordinator.py` (orchestrator + demo CLI).

    python tools/live/coordinator.py demo --out C:\ANASTYLOS\data\live-dev\A\<run>
    cd tools/live && python -m unittest discover -s tests

## Proven by tests (synthetic, FULL-01 v21 fixture copies, see `tests/fixtures/MANIFEST.json`)
EO-1 crash at each step / after each of 24 memory calls of the 13 FULL-01 answers → recovery gives the same events as
`answers.apply`; EO-2 duplicate input ignored (also after restart); EO-3 events written but not journaled are found by
marker, not rewritten; EO-4 second coordinator rejected (same and other process), no stale lock after crash; EO-5 torn
journal/memory line → stopped, no truncation; EO-6 stale revision never committed; EO-7 observation without tray link
detected → stopped. Read-back confirmation is L0 only (no model decides a "yes").

## Limits (fail closed; frozen files not changed)
- The journal still emits Lane A fields such as `at`, rather than all contract 0.2 fields such as
  `t_wall`; integration does not establish journal conformance. The original fixture manifest also
  retains its CRLF-source versus LF-blob mismatch for `audit/memory.before-answers.jsonl`; the local
  integration report records both hashes without changing the forensic fixture or its manifest.
- l2sim's `session.py` shadows `tools/session/session.py`; run the simulator in its own process,
  as the integration tests do. The real L2 evaluation remains independently owned; no benefit is claimed.
- `request_view` remains gated. Contract 0.3 and refreshed provider fixtures remain future work.
- memory.py/store.py do not fsync: a memory line may be lost on power failure. Recovery treats memory as truth and stops
  if the journal acknowledges an event memory lacks.
- No op_key field in memory: the op_key travels in the existing free-text note (`live op_key=...` via `provenance`);
  matching is exact but convention-based.
- The lock excludes only live coordinators; `session.py`, `answers.py apply`, manual `memory.py` ignore it.
- EO-1 bulk test uses `anastylos_memory.cli.main` in-process (same code, no subprocess) for speed.
- Label/place splitting (`<label> aus <place>`) is a heuristic; the read-back is the safeguard.
- `identical_to` groups and EXTRA parts are not handled live. The demo CLI is L0 only (no provider wired).

## Contract requests for the TL
1. `answer_interpretation` intent `not_a_part` (L0 supports it; schema lacks it).
2. Structured `op_key` field on memory events and fsync in `store.append_event` after E3-B release.
3. A memory-wide writer lock honoured by `memory.py` itself.
