# l2sim — L2 next-action replay simulator (Lane D)

Offline, stdlib-only harness for the L2-AGENT-02 question: does a model choosing from a closed action list
(`schemas/task.next_action.json`) resolve more parts per operator interruption than a deterministic policy
(P0) and a random-valid policy (PR), without integrity violations? Spec: `../seed/L2-AGENT-02.md`; seam:
`../CONTRACT.md` §3.1; frozen comparison: [`PREREG.md`](PREREG.md). Architecture context: `ARCHITECTURE.md`.

Nothing here writes memory, reads the active FULL-01 memory, or makes network calls. A "commit" is a state
change inside the simulation.

## Modules

| file | job |
|---|---|
| `actions.py` | closed action set, typed constructor, code re-validation of the output shape |
| `validator.py` | fail-closed preconditions (ids at current revision, one question asked, rate limits, budgets, leave rules) and the valid-action list |
| `session.py` | coordinator-side question states (`OPEN → ASKED → READ_BACK → CONFIRMED → COMMITTED`, side states) |
| `episodes.py` | FULL-01 v21 episodes (read-only) and seeded synthetic adversarial families |
| `oracle.py` | oracle operator: preregistered abstention/error/read-back-catch rates, common random numbers |
| `policies.py` | P0, PR, L2 (`ProposerPolicy` over an injected Proposer; structured state, opaque labels) |
| `simulator.py` | event replay, turns, validation with P0 fallback, journal trace, counters |
| `metrics.py` | pooled metrics, paired win/tie/loss, PREREG §7 acceptance |
| `fakes.py` | test proposers: `good`, `random`, `adversarial` |
| `run.py` / `compare.py` | CLI: one arm per run; acceptance over three runs |

## Usage

```
cd tools/live/l2sim
python run.py --arm P0 --out C:\ANASTYLOS\data\live-dev\D\<run>
python run.py --arm PR --out C:\ANASTYLOS\data\live-dev\D\<run>
python run.py --arm L2 --proposer fake:adversarial --out <dir>          # harness check, no network
python run.py --arm L2 --proposer <module:factory> --max-calls 910 --out <dir>
python compare.py --p0 <dir> --pr <dir> --l2 <dir>
```

Defaults: `--seeds 0,1,2`, `--episodes primary` (13 episodes), `--data C:\ANASTYLOS\data\session-runs-v21\FULL-01`.
`--out` must be outside the repository and must not already hold `results.json`. For a real provider the
module must be importable (e.g. `PYTHONPATH=tools/live`); the factory receives `call_cap` and `run_dir` if it
takes them, and `run.py` enforces the hard cap itself (`CappedProposer`). Output: `results.json`
(`anastylos-l2sim-results@1`: provenance incl. git HEAD, PREREG and source hashes, input SHA-256, pooled and
per-source summaries, per-episode counters and traces with `model_action` / `validator_verdict` /
`executed_action`) and, for L2, `calls.jsonl` (request id, request hash, provider result per call).

Tests: `python -m unittest discover -s tests` (fixtures under `tests/fixtures/full01/` with `MANIFEST.json`).

## Results so far (P0 and PR only; no model run)

Run 2026-10-29 at b5558952 (clean tree, PREREG v2), `C:\ANASTYLOS\data\live-dev\D\2026-10-29-{P0,PR}-primary`
(39 paired episode-runs, seeds 0–2) and `…-supp-s0-9` (130 runs, supplementary, not for acceptance):

| arm | resolved | interruptions | resolved / interruption | per question interruption | operator s | integrity |
|---|---|---|---|---|---|---|
| P0 primary | 163 | 203 | **0.803** (FULL-01 0.825, synthetic 0.798) | 0.803 | 2233 | 0 |
| PR primary | 123 | 340 | **0.362** (FULL-01 0.405, synthetic 0.350) | 0.523 | 3929 | 0 |
| P0 seeds 0–9 | 551 | 665 | 0.829 | 0.829 | 7315 | 0 |
| PR seeds 0–9 | 403 | 1124 | 0.359 | 0.505 | 13262 | 0 |

P0 beats PR in all 39 paired runs. PR: 41 prompts + 64 dependency questions (effect unknown), 90 asks while
busy, 4 unnecessary questions, 24 questions open at the turn limit. Invalid-action rate 0 for both (P0 by
construction, PR draws from the valid list). The bar an L2 run must clear is therefore P0's **0.803**; the
ceiling is 1.0 and 12 of the 186 question-runs are unanswerable by construction.

## Limits (what this cannot show)

- The oracle is a model of the operator with preregistered rates, not the owner. FULL-01 answers are
  development data (12 questions), not independent validation; the synthetic families are constructed.
- `protocol_prompt` and `request_view` effects on observability cannot be simulated from footage (scored
  "effect unknown"); `request_view` is disabled. `ask_dependency` has no ground truth in FULL-01.
- Busy/idle is derived from deposit and removal windows, not observed hands; operator time is a fixed cost model.
- Read-back is a single confirm step; real read-back wording and misunderstanding are out of scope (L0/L1).
- No latency: a model's proposal is applied at the state it was computed on; stale protection is exercised by
  revision changes and adversarial proposals, not by real response delays.
