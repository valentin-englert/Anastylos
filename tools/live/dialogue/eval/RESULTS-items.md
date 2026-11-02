# Cell-items batch — results (28 of 30 calls, plan: PREREG-items.md at 71f7bd2d)

| effort | passed | timeouts (8 s) | rejected by transcript check | median / p95 latency |
|---|---|---|---|---|
| minimal | 14/14 | 0 | 3 (T3, T4, T5: "only one part", "from left and right button"; the check rejected the model's proposal, as intended) | 3.55 s / 7.80 s |
| low | 9/14 | 5 (T3, T4, T5, T9, T10) | 0 | 4.74 s / 7.23 s (answered calls only) |

Registered rule applied as written: no effort has p95 <= 6 s, so the flow keeps `low` with the rule fallback on timeout.
The data favour `minimal` clearly (14/14, no timeouts); switching the flow to it is a deviation from the registered rule
and is left to the owner's decision (one constant: `_default_executor` in `tools/session-ui/dialogue_flow.py`).
Raw: `results-items-2026-11-03.json`. Latencies are the model call alone; no call is made for plain yes/no, meta, empty or unfinished answers.
