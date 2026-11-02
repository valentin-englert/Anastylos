# Dialogue batch — pre-registration

Written and committed before any live call. Cases: `cases.json` (same commit). Runner: `run_batch.py`.

- **Question:** can Muse at reasoning effort `none` or `low` (a) choose the next question type from a closed list and
  (b) extract part name / origin / storage using only the operator's words, fast enough for a spoken reply?
- **Calls:** 4 choice + 10 extraction cases per effort, one call each = 28. Hard cap 30 calls including retries
  (`MuseClient(max_calls=30, retries=1)`; retry only on HTTP 408/429/5xx, 1 s pause). Timeout 6 s per call. Usage and
  latency of every call go to `usage.jsonl` in the output folder.
- **Scoring:** choice = exact match with `expect`. Extraction = `intent` exact and each field equal to `expect` after
  lowercasing and dropping a leading article, **after** the code-side grounding filter (words not said by the
  operator are dropped). Also reported: ungrounded outputs before the filter (invented words), schema failures,
  timeouts, median and maximum latency.
- **Decision rule, fixed now:** the lowest effort with choice >= 3/4, extraction >= 8/10 and median latency <= 2.0 s
  becomes the default; if neither qualifies the deterministic rules stay the default and Muse is advisory only.
  No case, prompt or schema is changed after the first live call; a failure is reported, not tuned away.
- **Data:** controller-free made-up texts only; nothing from the fidget-toy run is used or looked at.
