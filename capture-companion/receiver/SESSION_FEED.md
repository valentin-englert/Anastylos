# Session feed: Core ingestion contract (Slice A)

Read-only poll projection over validated receiver staging, so a Core
ingestion worker can consume in-session frame evidence while the
recording session is still active. Transport contract only — it says
where validated bytes land and how to discover them, never what they
mean. Evidence semantics stay in `../SCHEMA.md`; transport security in
`../SYNC_PROTOCOL.md`.

## Discover: `GET /v1/packages?session_id=<uuid>`

Bearer-authenticated like every other `/v1/packages` route. One query
parameter, validated strictly:

- exactly one `session_id`, and it must be a UUID — otherwise `400
  bad_metadata` (`receiver/anastylos_capture_receiver.py`, `GET
  /v1/packages` handler);
- missing auth answers `401` like the rest of the API;
- an unknown-but-well-formed session id answers `200` with an empty
  list, not `404`: "nothing staged yet" is a normal in-session state.

Response (keys exactly as served):

```json
{
  "packages": [
    {
      "package_id": "44444444-4444-4444-8444-444444444444",
      "staged_at": "2026-10-27T16:11:25.013Z",
      "zip_sha256": "bcbb844a…",
      "status": "valid",
      "session_ids": ["11111111-1111-4111-8111-111111111111"],
      "observation_ids": ["22222222-2222-4222-8222-222222222222"],
      "accepted_for_review": ["22222222-2222-4222-8222-222222222222"],
      "duplicate_deliveries": 0
    }
  ]
}
```

`session_ids` is a defensive re-read of the staged manifest *after*
validation (`manifest_session_ids`): only well-formed UUID session ids
are reported, anything surprising yields `[]` rather than failing the
staging. Omit the parameter for the unfiltered operator view.

## Fetch: per-package artifacts

- `GET /v1/packages/<package_id>` (Bearer) returns the stored
  acceptance report (`report.json`) or `404`.
- On the workstation disk, `packages/<package_id>/` holds `package.zip`
  (byte-identical to the uploaded bytes), `report.json`, and the staged
  members (`manifest.json`, `assets/…`, `previews/…`,
  `annotations.json`). The HTTP feed is the versioned interface; direct
  directory reads see the same bytes but bypass auth and versioning, so
  a Core worker should prefer the feed and treat disk layout as
  informational.

## Consumer rules (normative for Slice A)

1. **Poll on `package_id`.** There are no etags or cursors: poll every
   few seconds during an active session and treat unseen `package_id`
   values as new. Polling is cheap (one small JSON read over
   `index.json`).
2. **Order by `captured_at`, never by arrival.** Frames can arrive
   delayed, retried, or out of order; the manifest lists observations
   in `captured_at` order and that is the only ordering claim. The
   filename convention `frame-<stamp>-<seq>-<id8>.jpg` is an advisory
   aid, not a contract.
3. **Deduplicate twice.** Same `package_id` redelivered → the receiver
   counts `duplicate_deliveries` and stages nothing new, but a
   consumer must still dedupe by `package_id`. The same
   `observation_id` may additionally appear in more than one package
   (re-export); dedupe observations by `observation_id`.
4. **Re-hash on consume.** Verify `package.zip` against `zip_sha256`
   and each asset against the manifest's per-file `sha256` before
   analysis. The receiver verified at staging time; the consumer
   verifies its own read path.
5. **Gaps are unknown, not empty.** There are no gap markers: a missing
   sequence number, a late frame, or a dropped upload all look like
   absence. Never interpret absence as "nothing happened"; the sampler
   logs gaps locally (`FrameMissed`) but that log is not yet
   transported (see below).
6. **Staged is not confirmed.** `accepted_for_review` means the
   validator accepted the bytes — it says nothing about component
   identity, quantity, or correctness. Operator review states
   (`unreviewed`/`reviewed`/`confirmed`) stay operator-owned; a Core
   worker must never promote staged evidence into confirmed findings
   on its own.

## Replay recipe (committed code only)

The Slice A demo rebuilds this end to end without sockets:

```powershell
# 1) JVM: the emitter test builds two deterministic Kotlin frame
#    packages (2-frame session 11111111-… + 1-frame session 99999999-…)
#    under android/app/build/outputs/demo/.
cd capture-companion/android
.\gradlew.bat :app:testDebugUnitTest `
  --tests 'org.anastylos.capture.sync.DemoHarnessTest'

# 2) Validate with the UNCHANGED validator (exit 0, status valid).
cd ..
py python/anastylos_capture_validate.py validate `
  android/app/build/outputs/demo/frame-session.zip --json

# 3) Stage exactly like the receiver (validate -> stage into an empty
#    per-package dir -> report.json + package.zip beside it).
py python/anastylos_capture_validate.py validate `
  android/app/build/outputs/demo/frame-session.zip `
  --extract .\demo-stage\44444444-4444-4444-8444-444444444444

# 4) Session projection: the feed predicate over staged manifests.
py -c "import json,sys,zipfile
for p in ['44444444-4444-4444-8444-444444444444']:
    m = json.load(open(f'demo-stage/{p}/manifest.json'))
    print(p, sorted({o['session_id'] for o in m['observations']}))"
```

(A throwaway script automating validate → stage → feed-filter →
duplicate → truncation checks, 15/15 PASS on 2026-10-27, is kept
outside the repo for re-runs; it drives the production
`ReceiverState`/`record_package`/`manifest_session_ids` code paths.)

## What remains between staging and live Core analysis

Slice A proves transport. It deliberately does not provide:

- **A Core ingestion worker.** Nothing polls this feed yet; no
  recognizer consumes staged frames during a session.
- **Push delivery.** Poll-only; no websocket, SSE, or filesystem
  watcher contract. Poll intervals and catch-up behavior are the
  worker's design to own.
- **Per-observation fetch.** The feed lists packages; observations and
  assets come from the whole report/package. Fine at frame rates, but
  a high-rate worker may want a narrower endpoint (new contract —
  needs its own review + negative-path tests).
- **Gap transport.** The sampler records missed frames locally, but
  gaps are not yet packaged or fed; consumers see absence only.
- **A source registry.** Session ids isolate sessions, but there is no
  `source_id` registry yet distinguishing the Android phone from the
  MUSTOOL microscope on the same session — both would stage frame
  packages with only filenames/capture metadata to tell them apart.
- **Batching.** Slice A stages one package per frame (or per small
  manual selection): simple and idempotent, but one HTTP round trip
  plus validate + stage per frame. Throughput framing for sustained
  rates is untested.
- **Live-socket proof beyond loopback.** The feed handler is covered
  by 6 `SessionFeedTests`, and the full receiver suite (40/40) passed
  over live loopback HTTP in this session — but real-LAN binds and a
  physical phone on the feed have not been exercised. Run the suite on
  the workstation (`py -m unittest discover -s tests` from
  `receiver/`) and the hardware checklist before relying on the feed.
