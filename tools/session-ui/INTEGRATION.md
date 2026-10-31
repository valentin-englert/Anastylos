# Integration note — session UI (M3)

## No change to shared logic

`tools/agent/anastylos.py` and every other file outside `tools/session-ui/` are
unchanged. The UI reaches the product only through `tools/session-ui/adapter.py`,
which imports and calls, never copies:

| UI need | Product owner (called unchanged) |
|---|---|
| status, `complete` | `anastylos.summary` |
| question order | `ask_loop.next_question` (P0), `open_coordinator`, `operator_input` |
| ask / answer / confirm / defer | `Coordinator.ask/receive/defer/resume`, `MAX_UNCLEAR` |
| guide build / freshness / L0 answer / cache | `guide.build/load/answer`, `claims.verify`, `guide_render` |
| install steps / progress | `assemble.steps/status/mark_done` |
| binding resolution | `selection.resolve` |
| selected origin/storage | `selected_guide.answer` (+ `FakeProposer` for stored claims, as CLI `--claims`) |
| locks | `SessionLock` via `Coordinator.open/close`; per-session mutex in `server.py` |

The one deliberate adaptation is the **stepwise question driver**
(`adapter.question_next/answer/confirm`): the CLI loop (`ask_loop.ask_one`) is a
blocking console loop, so the UI performs one coordinator step per request. The
transitions mirror it exactly — same coordinator calls, same `MAX_UNCLEAR`
policy derived from the journal (reload-safe), same defer reasons
(`no usable answer`, `read-back not confirmed`), same operator messages. An
in-process background loop holding the coordinator open across requests was
considered and rejected: per-request open/close keeps the existing lock
behaviour (held only during the request) and crash recovery untouched.

No second implementation of guide, selection, budget, verifier or progress
logic exists in this module. No `select`/`selected-ask` contract is widened:
bindings are explicit files, labels are never searched, hashes never repaired.

## Seams for the parallel deliveries (not available, not depended upon)

- **Capture-gap planner** (other agent): when it exists, it reads the same
  contracts the UI already surfaces read-only — `runs/*/questions.json` plus
  the coordinator journal states (`GET /api/questions` shape:
  `{questions: [{id, kind, phase, cell, state}]}`). No UI change is needed for a
  planner that only reads; a planner that writes plans would add one new
  read-only endpoint serving its own files, following the `selections/`
  allowlist pattern (`resolve_named`, no browser paths).
- **HTML session report** (other agent): consumes the versioned JSON the UI
  serves — `GET /api/status` (`anastylos-agent-session@1` summary),
  `GET /api/guide` (package hash, parts, verified steps), `GET /api/assembly`
  (progress log view). If the report is written into the session folder by its
  owner, the UI can link it without trusting it (same text-only rendering rule).

Both are named in the page footer as not part of this build. No uncommitted
file of either delivery is imported, and no finished function is assumed.

## Requested connection that was NOT needed

None. Every M3 flow was expressible through the existing public functions, so
no new hook in `anastylos.py` is proposed.
