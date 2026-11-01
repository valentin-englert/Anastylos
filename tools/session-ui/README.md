# tools/session-ui — local session page (M3)

A slim local page that operates a prepared ANASTYLOS session without terminal
commands: open session → answer open questions → view guide → select one bound
part → ask origin/storage → confirm reassembly steps.

No framework, no dependency: stdlib HTTP server plus a narrow adapter over the
existing product modules. The page and its adapter are the only files this task
owns; `tools/agent/anastylos.py` is unchanged (see `INTEGRATION.md`).

## Start

Prerequisites: the repo Python (3.12) with the pinned stdlib-only product path —
no venv needed for the UI itself. A prepared workspace outside the repository:

```powershell
# 1. Build the offline demo workspace (two fresh FULL-01 copies, ~1 min):
python tools/session-ui/demo/prepare_demo.py --root C:\ANASTYLOS\data\session-ui-demo

# 2. Serve it on localhost only:
python tools/session-ui/server.py --workspace-root C:\ANASTYLOS\data\session-ui-demo --port 8071

# 3. Open http://127.0.0.1:8071/ in a browser (?session=demo-open deep-links).
```

`prepare_demo.py` refuses workspace roots inside the repository or inside an
active memory, and refuses to overwrite existing demo folders without `--force`.
The demo sessions are built from the in-repo FULL-01 v21 fixtures
(`tools/live/tests/fixtures`) with the validated M1 recipe — no original memory,
capture or frozen file is read for writing, only copied from fixtures.

## Offline demo

This build is offline by construction:

- The server wires **no proposer** into guide answers: `source` is `L0`
  (deterministic package answer), `cache` (previously accepted answer) or
  `refusal`. Selected-part answers add `offline_claims` (stored fixtures
  replayed through the same fake path as the CLI `--claims` flag).
- `Muse`, `L1` and `provider_cache` can never appear; the page labels every
  source honestly and the banner states the offline scope.
- Opening the page triggers no model call and no question mutation: status,
  parts and progress are read-only; the next question is asked only on click.

## What the page covers

- Session status: phases, question states, memory events, `complete`/`incomplete`
  (the product's own `summary()` — open or unresolved questions keep
  `complete: false`).
- The existing question round: next question in P0 order, free-text or screen
  letter answer, English read-back with explicit yes/no. Skip / "I don't know"
  defer; exhausted questions stay `LEFT_UNRESOLVED` (journaled reason).
- Guide: explicit build, freshness banner with rebuild when the memory changed,
  L0 questions, parts table, verified install steps.
- Selection: exactly one hash-bound part from an explicit binding file. Origin
  and storage are shown and asked separately. Changed bytes refuse the
  selection — no label search, no hash repair.
- Reassembly: current step from verified claims, confirm-only progression,
  progress across restarts, superseded-package notice.
- Session report: one click exports the standalone HTML (`report.export`,
  reused unchanged) to the server-fixed `report.html`, then opens or downloads
  it. A stale guide refuses the export; session inputs are never modified.
- Capture needs: prepared gaps show missing information, the English
  instruction, technical criteria and the success criterion; prepared evidence
  is hash-verified from its bytes and evaluated with the planner. Closure is
  locked (see Limits).

Mutating actions are serialized per session; while a request is in flight its
button is disabled, and a repeated POST is a no-op on the new state (never a
duplicate write). Every section has loading, empty, error and stale states.

## Capture loop (part drawings ↔ phone)

A drawing job that ends `NEEDS_CAPTURE` shows **Request this view on the phone**. The workstation posts one G0
`ViewRequest` (top-down over the A3 sheet, label "<part>, <view side> up, on the sheet, hands off") into the guided
session it drives; the phone adopts it and shoots through its normal gates. On delivery the still is fetched with its
hash re-checked, stored under `drawings/captures/` with an evidence record, and a follow-up job
`<job>-capture-<time>` draws from that still. Request, evidence and part binding stay separate records
(`captures.py`); the photo never proves which part it shows — the operator confirms the follow-up drawing.

One-time setup (not part of the normal flow): `<workspace>/capture-settings.json` with `receiver_url` (https),
`spki_pin_sha256` or `ca_file`, `receiver_data_dir`, `source_id` (the enrolled phone), and the `board_id`,
`calibration_id`, `board_frame` the phone accepts for the A3 sheet. Then **Connect phone** mints an adapter token
via the receiver CLI, opens a guided session and waits until the operator arms it on the phone.

Known bridge: the G0 contract gives the adapter's bootstrap token no read route for the grant the phone creates, so
`captures._find_grant` reads the receiver's `control.db` read-only on the same workstation. A proper route is
requested from the contract owner.

## API (JSON, localhost only)

Reads: `GET /api/sessions /api/status /api/questions /api/guide
/api/selections /api/assembly /api/gaps` (query: `?session=<id>`); file reads
`GET /api/report` (`&download=1` for attachment) and `GET /api/gaps/evidence`
(`&file=<name>`).
Writes (POST, foreign `Origin` refused with 403): `/api/questions/next`,
`/api/questions/answer`, `/api/questions/confirm`, `/api/guide/build`,
`/api/guide/ask`, `/api/select`, `/api/selected-ask`, `/api/assembly/done`,
`/api/report/export`, `/api/gaps/evaluate`.

Ids are plain names resolved inside the workspace root; paths from the browser
are rejected. The server refuses non-loopback `--host` values.

## Explosion view (adapter ready, routes not wired)

`explosion.py` and `web/explosion.html|js|css` show the schematic explosion drawing composed by
`tools/explosion` (click a part for its evidence, assembled/exploded toggle, operator layer
confirmation). `server.py` does not serve them yet: the routes and exact signatures are in the
`explosion.py` docstring and in `docs/integration/EXPLOSION-VIEW-01.md`.

## Stale demo (optional)

```powershell
Copy-Item -Recurse C:\ANASTYLOS\data\session-ui-demo\demo-complete C:\ANASTYLOS\data\session-ui-demo\demo-stale
python tools/agent/anastylos.py declare --session C:\ANASTYLOS\data\session-ui-demo\demo-stale `
  --phase P1 --label 'stale probe part' --to 'cell 9' --confirm yes
# open ?session=demo-stale: the guide banner asks for an explicit rebuild,
# and the old selection bindings refuse with a hash mismatch (no repair).
```

## Verify

```powershell
cd tools/session-ui; python -m unittest discover -s tests
python tools/compliance/check.py   # from the repo root: 0 errors expected
```

Browser acceptance screenshots live in `demo/screenshots/` (landing, open and
completed session, prompt, read-back, commit, selection, stored claims,
assembly progress, stale guide, refused selection, rebuilt guide with a
still-stale binding).

## Limits

- Sessions must already exist (`new`/`phase` stay CLI-only); declarations
  (`declare`) and return checks (`assemble --returns`) stay CLI-only too.
- Capture-need closure is locked: closing a gap needs an explicit technical
  review (image readability, framing, quality) plus an explicit operator
  confirmation, and no technical image check exists in this repo to produce
  the former — the UI submits no reviews and synthesizes none. Gaps stay
  `open` or `needs_review` with the planner's reason. No image analysis, no
  camera control, no `request_view` is wired; see `INTEGRATION.md`.
- Guide/selection/assembly answers are only as good as the session evidence;
  a selection is not a frame→record confirmation or assembly approval.
