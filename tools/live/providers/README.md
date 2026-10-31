# tools/live/providers — Proposer implementations (Lane C)

Implements the Proposer seam of [`../CONTRACT.md`](../CONTRACT.md) §3.1 under the Meta Model API rules of §4.
A model proposes; consumers validate again in code and fall back to L0 on every non-`ok` status.

| File | Job |
|---|---|
| `meta.py` | `MetaProposer(run_dir, max_calls)` — the only network module under `tools/live/` (stdlib `urllib`) |
| `fake.py` | `FakeProposer(fixtures_dir, script)` — deterministic replay and scripted failure modes for tests |
| `provider_core.py` | request body + SHA-256, response interpretation, result / trace / fixture records |
| `jsonschema_subset.py` | validator for the schema subset of `../schemas/` (unknown keywords fail closed) |
| `task_prompts.py` | instruction text per task stating the A2 limits (`live-prompts@1`) |
| `smoke.py` | bounded real run: one synthetic request per task, cap 8 calls |

Wiring (in a consumer's `main()`, never imported by the core): put this directory on `sys.path`, then
`MetaProposer(run_dir, max_calls).propose(task, payload, request_id)`.

`MetaProposer` behaviour: model `muse-spark-1.3-contributor`, effort `max` (constants; a response echoing
another model or effort is `provider_error`); timeout 120 s, no retry on timeout; at most 2 retries on
408/429/5xx; every HTTP attempt counts against `max_calls`, also across instances on the same run dir
(counted from durable reservations plus legacy traces); identical request bodies are served from `run_dir/cache/<sha256>.json`
(`cache_hit: true`); missing `MODEL_API_KEY` → `disabled`; an invalid payload raises `ValueError` (caller bug).
Run dir files: `traces.jsonl` (`anastylos-live-provider-trace@1`: no key, no headers, no prompt or output
text) and `cache/*.json` (`anastylos-live-provider-fixture@1`: request body and API response, no key).

`FakeProposer` modes: `replay` (default), `output`, `invalid_json`, `schema_violation`, `wrong_enum`,
`stale_revision`, `timeout`, `provider_error`, `disabled`, `budget_exhausted`; script keys are request ids or
task names. `tests/fixtures/recorded/` holds the fixtures of the real smoke run (synthetic inputs only) with
`MANIFEST.json`.

## Verification

```bash
python -m unittest discover -s tests
```

Real smoke (paid, writes to `C:\ANASTYLOS\data\live-dev\C\<run-id>\`; key from the user scope, not printed):

```powershell
$env:MODEL_API_KEY=[Environment]::GetEnvironmentVariable("MODEL_API_KEY","User"); python -B tools/live/providers/smoke.py
```

## Limits

- Validated on synthetic inputs only (one call per task); no live-session or real-operator data.
- Output validation is schema-level. Whether ids, choices, revisions and claim subjects exist in the current
  state is the consumer's check (CONTRACT §3.1).
- The API stores responses by default (`"store": true` in the response); the request does not override it.

## Durable attempts and lifecycle (local repair, 2026-10-31)

Before each transport send, `attempts.jsonl` appends one reservation and flushes + fsyncs it. Every reservation
counts forever, even if the process dies before sending or recording its result. Completion traces have
`reserved: true` and are not counted again. Older traces without that marker still contribute their attempts;
no migration rewrites them. A missing completion cannot refund a reservation. Cache hits reserve nothing.
Malformed/torn ledger JSON fails closed; do not delete or repair it to reclaim budget.

The existing OS `SessionLock` is reused in the provider directory through the entire proposal (cache lookup,
reservation, HTTP retries, cache write and completion). Competing processes are refused before sending; a new
instance refreshes accounting after acquiring the lock. The agent additionally locks the session root across
model commands and their late work, including saved-limit updates. No limit is automatically increased.

Real HTTP runs in a disposable stdlib subprocess with a 120 s wall deadline per attempt. Credentials and the
unchanged request travel over anonymous stdin, never command-line arguments or files. Child output is bounded
by the existing response-size limit. A deadline kills/reaps the local HTTP worker; this does not cancel a
request already accepted remotely. Timeout has no retry. Retryable HTTP status may lead to at most three
attempts, with 2 s and 5 s backoff: at most 367 s transport/backoff per proposal, plus OS process creation,
cleanup, disk fsync and local processing. OS stalls/power loss cannot have a software wall-time guarantee.
Injected transports must obey the same timeout contract.

Hard parent-process termination may leave remote work (or a local child until its socket timeout) unfinished.
The durable reservation still counts, but there may be no completion trace/cache, no usage report and no way
to infer whether Meta processed the request. Retention and provider request contents are unchanged.

Verification of this repair: 32 provider tests PASS, 0 FAIL / ERROR / SKIP; controlled fake transports only.
Do not run older provider writers against a session concurrently with this version.

Selected guide context v2 adds fixed task-specific system instructions clarifying that stored_in.object is
storage.value, not a provenance ID. Only guide_claims with the exact v2 selected context receives this
refinement; ordinary requests/recorded fixtures, schemas and verifier semantics are unchanged. Missing
storage requires unknown/insufficient_evidence. See tools/agent/README.md for the selected boundary.
