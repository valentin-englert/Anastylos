# tools/live — lane contract `anastylos-live-contract@0.2`

Owner: Technical Lead (integration branch `integration/live-v1`). Lanes branch from the commit that adds this
file and change only the paths they own. A change to this file or to `schemas/` is made by the TL on
`integration/live-v1` first; lanes then merge it in. A lane that needs a change writes it up in its report
instead of editing another lane's files.

Architecture: A2 "deterministic core, agentic edge". The coordinator owns state, validation, authorization and
persistence. A model proposes; deterministic code accepts, falls back or rejects. L0 (no model) must always work.

## 1. Ownership

| Lane | Branch | Owns (create/edit only these) |
|---|---|---|
| TL | `integration/live-v1` | `tools/live/CONTRACT.md`, `tools/live/schemas/`, `tools/live/seed/`, `tools/live/integration/` (vertical slices, adversarial set), `ARCHITECTURE.md`, merges |
| A LIVE-CORE | `feat/live-core` | `tools/live/*.py`, `tools/live/tests/`, `tools/live/README.md` (not the subfolders below) |
| B GUIDE-CLAIMS | `feat/guide-claims` | `tools/guide/claims.py`, `tools/guide/tests/test_claims.py`, `tools/guide/guide02/` |
| C META-PROVIDERS | `feat/meta-providers` | `tools/live/providers/` |
| D L2-SIM | `feat/l2-sim` | `tools/live/l2sim/` |
| E EVAL | `feat/live-eval` | `docs/LIVE-01.md`, `tools/live/eval/` |
| F LAT-1 | `feat/lat1-profiling` | `tools/lat1/` |

Read-only for every lane: everything else, in particular the E3-B frozen files listed in
`docs/PROTO-01-E3B.md` §2 (`tools/session/*.py`, `assembly-memory/anastylos_memory/store.py`,
`custody-ledger/**`, configs, `tools/proto-01/`), `tools/guide/check.py`, `tools/guide/package.py`,
`tools/guide/GUIDE_RULES.md`, all Android and AgentCam worktrees. Reuse them by import or subprocess.

## 2. Data and runtime locations

- Never read or write the active FULL-01 memory (`C:\ANASTYLOS\data\session-runs-v21\FULL-01\memory`).
  Tests copy fixtures into a temp dir. Fixtures committed under a lane's `tests/fixtures/` carry a
  `MANIFEST.json` with source path and SHA-256 of each copied file.
- Development runs write under `C:\ANASTYLOS\data\live-dev\<lane>\<run-id>\`, never into the repo.
- Python stays stdlib-only. No new dependency.

## 3. Interfaces

All JSON objects carry a `schema` field. Schemas live in `schemas/` (JSON Schema draft 2020-12, subset:
`type`, `enum`, `required`, `properties`, `additionalProperties: false`, `items`, `maxLength`, `maxItems`).

### 3.1 Proposer seam (A, D, B consume; C implements)

```python
class Proposer(Protocol):
    def propose(self, task: str, payload: dict, request_id: str) -> dict: ...  # returns provider-result@1
```

- `task` ∈ `question_wording`, `answer_interpretation`, `next_action`, `guide_claims`. Each has an input and
  output schema in `schemas/task.<task>.json`.
- The result is `anastylos-live-provider-result@1` (`schemas/provider-result.json`). `status` ∈ `ok`,
  `invalid_output`, `timeout`, `provider_error`, `disabled`, `budget_exhausted`. Only `ok` carries `output`,
  already validated against the task's output schema by the provider. Consumers validate again in code.
- The core never imports `providers/`. Wiring happens in a `main()` (dependency injection). Every consumer
  has a test fake that follows this protocol; `providers/fake.py` (C) replays recorded fixtures.
- Any non-`ok` status → the consumer executes the deterministic L0 / P0 behaviour and journals both.

### 3.2 Operator input (`anastylos-live-operator-input@1`)

`source` ∈ `typed`, `screen_choice`, `speech_transcript`; `text`; `choice` (candidate token or null);
`transcript` (only for speech: `provider`, `model`, `language`). The coordinator treats all three identically
after interpretation. Voice therefore needs no second protocol: STT produces this object.

### 3.3 Question state and op key

- `op_key` = `<live_session_id>:<question_id>:<revision>`. `revision` increments whenever the question's
  candidates or window change after a re-link. An answer or model result for an older revision is stale and is
  never committed.
- States: `OPEN → ASKED → ANSWERED → READ_BACK → CONFIRMED → COMMITTED`, side states `DEFERRED`,
  `LEFT_UNRESOLVED`, `SUPERSEDED` (revision changed), `FAILED`. Only `CONFIRMED` (operator said yes to the
  read-back) can lead to a memory write.
- `slot` (CR-E4): the 1-based ordinal of a question-triggering event in detection order within one round
  (`docs/LIVE-01.md` §4.2). In a LIVE-01 round the coordinator reads the sealed die `live01-die@1` and takes
  arm (live/offline) and wording (L1/L0) per slot from it; it never derives them itself.

### 3.4 Journal (`anastylos-live-journal@1`, JSON lines, append-only, fsync per line)

Kinds: `session_start`, `question_state`, `provider_call` (provider-result without output text beyond the
validated object), `intent` (op_key, planned memory events), `memory_result`, `done`, `recovery`, `fallback`,
`ui_event` (CR-E2). The journal is the coordinator's own file, beside the run, never inside `memory.jsonl`.

Required fields since 0.2 (CR-E1; the evaluation reads them, `docs/LIVE-01.md` §8.1):

| kind | fields |
|---|---|
| every record | `schema`, `seq`, `kind`, `t_wall` (ISO-8601 with UTC offset, millisecond precision) |
| `session_start` | `live_session_id`; in a LIVE-01 round also `round` ∈ `live`/`offline`, `die_sha256`, `delivery_delay_s` |
| `question_state` | `op_key`, `question_id`, `revision`, `state`; static (last non-null wins): `slot`, `question_kind` (never `kind`, which is the record kind), `cell`, `deposit_window_s`, `removal_window_s`, `source_box_mm` `[x, y, w, h]`, `deposit_t_wall`; on `ASKED`: `wording` (`L0`/`L1`), `text`, `anchor_possible`, `anchor_ref`, `die_ordinal`; on `ANSWERED`: `input` (operator-input@1); on `CONFIRMED`: `resolved` = `{outcome: source/destination/no_part, source_box_mm, destination, place_text}` |
| `intent` | `op_key`, `planned` |
| `memory_result` | `op_key`, `status` (`ok` or a failure word), `written_ids` |
| `provider_call` | the provider-result@1 fields except `output` text beyond the validated object, plus `op_key` |
| `fallback` | `op_key`, `task`, `reason` (prefix `grounding` for grounding rejections) |
| `ui_event` | `action` ∈ `scrub`, `replay_open` |

Unknown extra fields are allowed and ignored by readers. Window times (`*_window_s`) are seconds on the
take's overhead-video clock, as in `parts.json`; durations use `t_wall`.

### 3.5 Memory writes

Only the coordinator writes, only through the existing contracts: `tools/session/answers.py` (plan / apply
for one answer at a time) or the `assembly-memory/memory.py` CLI as subprocess. One writer per session dir
(exclusive lock file). No new memory event type, no new field (frozen until E3-B is released).

### 3.6 Guide claims (`anastylos-guide-claims@1`, `schemas/task.guide_claims.json`)

Claims, not prose: `relation` ∈ `stored_in`, `removed_from`, `install_before`, `basis`, `dependency`,
`unknown`; `subject`, `object` (package ids); `basis_word` from GUIDE_RULES. Speech is rendered from verified
claims only.

## 4. Meta Model API (C only; established owner configuration)

Verified 2026-10-29 by one synthetic call (`seed/meta-smoke-trace.json`): HTTP 200, 6.4 s, model returned
`muse-spark-1.3-contributor`, `reasoning.effort` returned `max`, 29 input / 548 output (527 reasoning) tokens,
output valid against a strict schema.

- `POST https://api.meta.ai/v1/responses`, `Authorization: Bearer $MODEL_API_KEY`, body
  `{"model": "muse-spark-1.3-contributor", "reasoning": {"effort": "max"}, "input": ..., "text": {"format":
  {"type": "json_schema", "name": ..., "strict": true, "schema": ...}}}`. Text is in the `message` output item.
- Model id and effort are constants. No other model, no lower effort, no tier comparison.
- Key only from the process environment; never printed, logged, cached or written. If the process lacks it
  (the variable is set at Windows user scope, older processes do not inherit it), status `disabled`.
- Safeguards: timeout 120 s; at most 2 retries on 408/429/5xx before any body; deduplication by SHA-256 of the
  request body (cache under the run dir; a cache hit is journaled as such); a per-run call cap passed in by the
  caller (no default unbounded loop); no recursive model calls.
- Inputs: synthetic or minimized structured state (ids, counts, phase words, operator labels already typed by
  the owner). No images, video, audio, raw files or secrets. Labels in `next_action` are opaque tokens.
- Trace per call (`anastylos-live-provider-trace@1`): request_id, task, model, effort requested and returned,
  `latency_s` (wall seconds of the HTTP call, float; CR-E3, same name as in provider-result@1), usage, status,
  sanitized error (HTTP status, error type, ≤ 300 chars, no headers), request hash.
- The request body does not set `store`; the provider default (`store: true`) applies. Together with the
  contributor tier this means inputs and outputs may be retained and used for training (owner decision D1
  scope); therefore inputs stay synthetic or minimized as above.

## 5. Verification

Each lane runs its suite from its own directory: `python -m unittest discover -s tests` (for C:
`tools/live/providers`, for D: `tools/live/l2sim`, for E: `tools/live/eval`). Real-API tests are not part of
the unit suites; they are separate scripts that write traces under `C:\ANASTYLOS\data\live-dev\`.
Commit on the lane branch only after the suite passes. No push, no merge to `main`.

## 6. Research seeds (`seed/`, read-only)

From the TL scratchpad on 2026-10-29, copied verbatim: `h3r0.py` (L0 templates and rubric R1–R7),
`cases.dev.json`, `baseline.json`, `GUIDE-02-spec.md`, `L2-AGENT-02.md`, `LIVE-01-design.md`, `probe.py`
(exactly-once probes X1–X6), `lat0_harness.py`, `meta-smoke-trace.json`. Lanes port what they need into their
own files; the seeds stay unchanged as provenance.

## 7. Change log

- 0.2 (2026-10-29, TL, on `integration/live-v1` after merging lanes A–E):
  - CR-E1 accepted: journal field table in §3.4; `t_wall` replaces lane A's `at` as the record time field.
  - CR-E2 accepted: journal kind `ui_event`.
  - CR-E3 accepted: `latency_s` is the latency field in traces and provider-result@1 (already used by C).
  - CR-E4 accepted: `slot` definition and die reading in §3.3.
  - Lane A request `not_a_part` intent: deferred to 0.3. Any change to a task schema changes the request
    SHA-256 and invalidates C's recorded fixtures, so it lands together with a re-recording. Until then L0
    carries `not_a_part` beside the schema output (as A implemented).
  - Lane A requests structured `op_key` in memory events, `fsync` in `store.append_event`, and a lock that
    `memory.py` honours: blocked until E3-B is released (§3.5 freeze).
  - Task schemas and `provider-result.json` are unchanged in 0.2.
  - New TL-owned folder `tools/live/integration/` (vertical slices and the cross-lane adversarial set).
  - Lane A owes the 0.2 journal adaptation (`t_wall`, `question_kind`, `ui_event`, `written_ids`,
    `fallback.reason`); until then `docs/LIVE-01.md` scoring of A's journals stays provisional.
