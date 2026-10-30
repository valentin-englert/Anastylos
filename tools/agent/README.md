# tools/agent — ANASTYLOS agent (product orchestrator)

Owner: TL (`tools/live/CONTRACT.md` §1, contract 0.3). One entry point that carries a disassembly session through
the existing modules; it adds no evidence logic of its own. Architecture A2: the deterministic core decides, Muse
only proposes (and is never on the path between a question and the operator's answer).

    python tools/agent/anastylos.py new    --session C:\ANASTYLOS\data\agent\<id> --object "SNES-Controller" --operator "<name>"
    python tools/agent/anastylos.py phase  --session <dir> --tag P1 --label "beim Abschrauben der Rückseite" --video P1.mp4 [--python <venv python>]
    python tools/agent/anastylos.py status --session <dir>
    cd tools/agent && python -m unittest discover -s tests

## Session folder

- `agent-session.json` (`anastylos-agent-session@1`): object, operator, phases (tag, operator label, video path
  and SHA-256, run folder, status `running`/`done`/`failed`, counts from `parts.json`). Written atomically.
- `memory/` — the session's Assembly Memory (`memory.jsonl`), appended by `tools/session/session.py`.
- `runs/<tag>/` — one session-runner output per phase (`parts.json`, `questions.json`, evidence).
- `phase-words.json` (`anastylos-live-phase-words@1`) — the operator's phase labels, for the live coordinator.

## Rules it enforces

- `session.py` (E3-B frozen) runs only as a subprocess with the OpenCV venv; it cannot resume (B10), so a
  phase tag runs once, a failed run keeps its folder, and an unfinished phase blocks the next one.
- The run's video hash must equal the hash recorded before the run.
- Sessions live outside the repository and never inside an active memory named by
  `C:\ANASTYLOS\data\active-memory.json`.

## Status (M0, 2026-10-30)

Implemented: `new`, `phase`, `status`. Next per roadmap: M1 question loop through `tools/live/coordinator.py`
(P0 order, read-back, controlled write); M2 guide package with precomputed Muse claims verified by
`tools/guide/claims.py`, reassembly steps with `tools/reassembly/returns.py`; M3 session page and report.
Validation: 12 unit tests with a fake runner, plus one development dry run on the FULL-01 P3 video (93 s):
`C:\ANASTYLOS\datagent\dryrun-m0-P3`, 4 min 07 s (2.65× video length), counts 7 parts / 2 linked / 2 written /
6 questions; `parts` and `questions` identical to the active v21 P3 run. Development data; no hardware claim.
