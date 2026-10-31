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

## Integrated deliveries (main consolidation)

- **Session report** (`tools/agent/report.py`, merged): the UI calls
  `report.export(session_dir, <session>/report.html)` unchanged and serves the
  file back for open/download. Freshness is checked inside `export`
  (fail-closed); the target is fixed server-side. No second report
  implementation exists.
- **Capture-gap planner** (`tools/agent/capture_gaps/planner.py`, merged): the
  UI evaluates prepared `capture_gaps/gaps/*.json` files with
  `planner.evaluate`, deriving `current_binding` fresh from the actual memory
  and guide bytes on every call; evidence hashes come from actual file bytes.
  Prepared evidence inherits its gap's prepared binding; staleness is detected
  against the current binding, never repaired.

## Missing interface: technical review producer

A gap closes only with an explicit `technical_review` (accepted, reviewer,
recorded_at, gap/evidence/image binding) plus an explicit operator
confirmation echoing the exact content criterion. No component in this repo
produces the technical review — there is no automated image analysis, and the
Android ShutterGate checks (pose/dwell/quality) prove neither rim visibility
nor part identity and are not wired here. Until a contracted, tested producer
exists, the UI offers no review submission: closure is locked by absence, and
gaps stay `open`/`needs_review`. No sham check was built. `request_view`
remains disabled; no camera call and no automatic memory confirmation exist.

## Requested connection that was NOT needed

None. Every M3 flow was expressible through the existing public functions, so
no new hook in `anastylos.py` is proposed.
