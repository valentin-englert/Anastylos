# Core Capture Evidence Index (`tools/core-ingest`)

Smallest reliable path from a Receiver-staged capture package to durable
Core evidence that can be looked up by session and observation.

**CODE** — implemented. **TEST** — focused unit suite below.
**REAL** — run against the workstation Receiver data directory (see
verification notes). This module never writes `memory.jsonl`.

## What it does

1. Discovers accepted packages from a co-located Receiver `--data-dir`
   (`index.json` + `packages/<id>/`), the same projection as
   `GET /v1/packages?session_id=` (`capture-companion/receiver/SESSION_FEED.md`).
2. Reads staged bytes **read-only** (no new download endpoint).
3. Re-verifies `package.zip` against the Receiver index SHA-256 and each
   original JPEG against manifest `files[]` SHA-256 / byte length.
4. Stores original image bytes content-addressed under an operator-owned
   `--core-dir`, with a SQLite index for idempotent package/observation
   lookup and an append-only import ledger.
5. Supports explicit `--associate-session` for Core session binding.
   Missing remote request IDs are never invented.

## What it deliberately does not do

- Does not append Assembly Memory `observation` / `tray_link` events.
  A JPEG is scene evidence, not proof a part was removed. See
  [`ASSOCIATION.md`](ASSOCIATION.md).
- Does not invent part identities, mm geometry, tray cells, or operator
  confirmations.
- Does not modify Receiver staging.
- Does not add network imports or third-party dependencies.

## CLI

Operational data stays outside the Git repository.

```powershell
$RECV = "$env:LOCALAPPDATA\Anastylos\receiver"
$CORE = "$env:LOCALAPPDATA\Anastylos\core-evidence"

# Ingest one capture session (or omit --session-id for all staged packages)
py -3.12 -m anastylos_core_ingest ingest `
  --receiver-data-dir $RECV --core-dir $CORE `
  --session-id 3638448a-6465-4a70-a7ca-51e5b23adc4b

# Optional: bind evidence to an explicit Core session UUID
py -3.12 -m anastylos_core_ingest ingest `
  --receiver-data-dir $RECV --core-dir $CORE `
  --session-id 3638448a-6465-4a70-a7ca-51e5b23adc4b `
  --associate-session 11111111-1111-4111-8111-111111111111

py -3.12 -m anastylos_core_ingest lookup --core-dir $CORE `
  --session-id 3638448a-6465-4a70-a7ca-51e5b23adc4b

py -3.12 -m anastylos_core_ingest verify-blob --core-dir $CORE `
  --sha256 <image-sha256>

py -3.12 -m anastylos_core_ingest status --core-dir $CORE `
  --receiver-data-dir $RECV
```

Run the module from this directory (or set `PYTHONPATH` to
`tools/core-ingest`):

```powershell
cd tools\core-ingest
py -3.12 -m unittest discover -s tests -v
py -3.12 -m anastylos_core_ingest status --core-dir $CORE
```

## Store layout (`--core-dir`)

```
capture-evidence.sqlite   # packages, observations, images, import_ledger
blobs/sha256/<aa>/<sha>   # original image bytes
imports/<package_id>/receipt.json
```

Interrupted imports leave no completed package row; retry is safe.
Replay of the same `package_id` is a no-op duplicate. The same
`observation_id` appearing in another package links without duplicating
the logical observation; conflicting hashes are rejected without rewrite.

## Epistemics

`accepted_for_review` / staged evidence ≠ confirmed Assembly Memory claim.
Receipts record `epistemic.not_assembly_memory_observation: true`.
