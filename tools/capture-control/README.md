# CORE-CONTROL — capture-control adapter/worker (Wave 1)

Owner: CORE-CONTROL. Owned paths: `capture-companion/receiver/` +
new `tools/capture-control/`. No Android, evidence-schema, memory,
CAD, global-deps or provider-credential edits (see blueprint
`IMPLEMENTATION-DEPENDENCIES.md` ownership table + Frozen Resolutions).

Wave-1 scope: durable SQLite control journal + CAS/idempotency green
on frozen vectors; MCP SDK choice recorded with pin; wire fixtures
for ANDROID in receiver tests. Exit: contract-tested endpoint, no
phone required. Physical gates stay `PENDING_EVIDENCE`.

## MCP SDK choice (recorded, not installed)

- Choice: official Python SDK, PyPI package `mcp==2.3.0`
  (repository `modelcontextprotocol/python-sdk`, MIT license,
  `requires_python>=3.10`, verified 2026-10-29 via PyPI JSON:
  `release_url .../mcp/2.3.0/`, `license: MIT`).
- Transport: local stdio for the Muse host (no new listening MCP
  port). Narrow `capture.*` tools only; no arbitrary filesystem/shell
  tool.
- Status: `PROPOSED-RECORDED`. Not added to
  `calibration/requirements.txt` and not installed: shared manifests
  and `tools/compliance/spdx.json` are CONTRACT-OWNER owned. Install
  requires a CONTRACT-OWNER approved exception to the single-manifest
  rule + SPDX/THIRD_PARTY update + `check.py` green, then a pinned
  adapter smoke (`tools/list` + request/status/evidence round trip).
- Rationale: official maintained SDK over a handwritten JSON-RPC
  stack; stdio keeps the receiver as the sole TLS listener.

## Layout

- `README.md` — this file (scope + MCP choice).
- `EVIDENCE.md` — lane evidence report (baseline, gates, hashes).
- Adapter/worker implementation follows in Wave 1b+ after journal
  green; Wave 1a lands the journal in `capture-companion/receiver/`.

Contract: `capture-companion/control/contracts/` read-only, G0
`ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663`.
Never edit in this lane; a defect means stop + G0 revision.
