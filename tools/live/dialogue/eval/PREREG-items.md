# Cell-items extractor batch — pre-registration

Written and committed before any live call. Cases: `cases-items.json` (same commit). Runner: `run_items_batch.py`.

- **Question:** does Muse (`extract_items`, strict JSON) at effort `minimal` or `low` turn free spoken answers about what is in a
  cell into checked items, and how long does one turn's model call take?
- **Calls:** 14 cases x 2 efforts (`minimal`, `low`) = 28 calls, hard cap 30, no retries, 8 s timeout per call. Usage and latency of
  every call go to `usage.jsonl` (key never printed or logged; injected into the child process only).
- **Scoring (after the deterministic transcript check, `items.check_items`):** a case passes when the surviving items equal `expect`
  (name lowercased, count: stated number, or null for an unnumbered plural, or 1; origin without article); `no_name` passes when no
  item survives; `meta` passes when `kind` is `meta` (or no item survives). Also reported: model output rejected by the check, schema
  failures and timeouts, median and p95 latency per effort.
- **Decision rule, fixed now:** the lowest effort with >= 12/14 passes and p95 <= 6 s becomes the flow's effort; otherwise the
  flow keeps `low` with the rule fallback on timeout and the result is reported as is. Nothing is tuned after the first live call.
- **Data:** controller-style transcripts only; nothing from the fidget-toy run.
