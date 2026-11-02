# Dialogue batch — results (2026-11-02, 28 of 30 calls, plan: PREREG.md at 597e7adf)

| effort | choice | extraction | timeouts/errors | median / max latency |
|---|---|---|---|---|
| none | 0/4 | 0/10 | 14 x HTTP 400: `reasoning_effort 'none' is not supported ... Supported: minimal, low, medium, high, xhigh, max` | n/a |
| low | 4/4 | 9/10 | 1 timeout (E4, 6 s limit); no invented words | 3.7 s / 6.0 s |

Decision rule applied as registered: no effort qualifies (`none` unsupported; `low` is accurate but median 3.7 s > 2.0 s),
so the deterministic rules stay the default and Muse is advisory only (`Executor(None)` is the default wiring).
`minimal` is the lowest valid effort and was not tried; it needs a new pre-registration. Raw: `results-2026-11-02.json`.
