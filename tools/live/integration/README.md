# tools/live/integration — TL vertical slices and cross-lane adversarial set

Owner: TL (`tools/live/CONTRACT.md` §1, contract 0.2). Wires lanes A (core), B (guide claims), C (providers)
and D (l2sim) together; no lane imports this folder.

- `wiring.py` — dependency injection: `meta_proposer(run_dir, call_cap)` for `l2sim/run.py --proposer
  integration.wiring:meta_proposer` (`PYTHONPATH=..;../providers` from `tools/live/l2sim`), and
  `checked_good_fake()` for a dry run that applies the real provider's payload check.
- `slices.py` — S1 L0 question → read-back → write; S2 Muse L1 wording/interpretation → gates → write;
  S3 (`l2_slice.py`, own process) Muse next_action → validator → executed or P0; S4 Muse guide claims →
  `claims.check` → speech. Each on fresh memory/package copies under `--out` (outside the repo), call cap 4
  per slice. `--offline` gives the all-`disabled` path; `--only` runs a subset. The key comes only from the
  process environment.
- `tests/test_adversarial.py` — crash, retry, corruption, grounding, stale revision and invalid tool output
  across the seams, deterministic (fake transport / `FakeProposer`, no network):
  `python -m unittest discover -s tests` from this folder.

Limits: synthetic and FULL-01 development data only; no real operator, no hardware. l2sim's `session` module
shadows `tools/session/session.py`, so l2sim code runs in its own process next to the coordinator.

## Real L2 arm (L2-AGENT-02, `l2sim/PREREG.md` v2 `74605b32`) — 2026-10-29

Run `C:\ANASTYLOS\data\live-dev\TL\2026-10-29-L2-primary` (`results.json` `fc677d4c…`, `calls.jsonl` `3ad8c579…`),
`integration.wiring:meta_proposer`, seeds 0–2, cap 910, code `ebf7f46a`, `l2sim` tree clean. Compared with
`compare.py` against lane D's P0 (`2935bcbf…`) and PR (`9a946a2f…`) runs under the same PREREG hash.

| Arm | resolved / interruption | integrity | invalid-action rate |
|---|---|---|---|
| P0 | 0.803 (163/203) | 0 | 0 % |
| PR | 0.362 (123/340) | 0 | 0 % |
| L2 | 0.752 (158/210) | 0 | 0 % |

Paired L2 vs P0: 0 wins, 30 ties, 9 losses; L2 vs PR: 38 wins, 1 loss. Complete, provider fallback rate 0 (5 timeouts
retried). 588 proposer calls, 311 answered from the request-hash cache, 277 paid; 0.40 M input / 0.68 M output tokens;
latency median 21.7 s, p95 64 s, max 114 s.

Verdict per PREREG §7: **rejected** (criterion 1 fails: L2 does not beat P0). L2 stays research; the product
orchestrator uses P0 for action selection. Simulator, oracle and cost model are lane D's preregistered assumptions,
not measurements of a real operator.
