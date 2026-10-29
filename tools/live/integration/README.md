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
