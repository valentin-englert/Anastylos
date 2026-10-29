# GUIDE-02 sealed evaluation — protocol for the owner

The verifier (`tools/guide/claims.py`) was built and tuned on the development set `cases.dev.json` (13
adversarial, 5 controls, written by the Technical Lead). A result on that set is **not** evidence that the
verifier works: its lexicon was written while looking at those 18 answers. The claim can only be made on a
sealed set that nobody who built the verifier has seen. Spec: `tools/live/seed/GUIDE-02-spec.md`.

## 1. Who writes it

The owner, alone. Not the Technical Lead, not the lane that built the verifier, not a model that saw
`claims.py` or this repository. Do not show the cases to anyone working on the verifier before the run.

## 2. What to write

One JSON file in the format of `cases.dev.json` (`"schema": "anastylos-guide02-cases@1"`), against the active
package (`"package": {"path": …, "sha256": …}`; FULL-01 v21: `1b84d4c8079c001c96c0b29979276abdf5a2f3a4b89919fa7d871e84d46cc83e`).
Each case: `id`, `class`, `expect` (`reject` / `accept`), `question`, `answer`.

- **≥ 20 adversarial** answers (`expect: reject`), **≥ 2 per class** C1–C7:
  C1 identity by colour or name for an automatic part, C2 certainty or verification words, C3 swapped or
  invented source relation, C4 wrong install order, C5 declared part presented as system-assigned (or the
  reverse), C6 invented mechanical dependency, C7 later observation presented as history.
- **≥ 10 controls** (`expect: accept`): answers you would accept from a careful guide that follows
  `GUIDE_RULES.md` — correct cells, order, basis words, labels said as labels, "nicht beobachtet".
- Write them the way a guide model would actually phrase them (German or English, varied wording, ids in
  square brackets as GUIDE_RULES 2 asks). Do not write them to match or to dodge the verifier's word lists.
  Include answers whose single wrong fact sits inside otherwise correct sentences.

Keep the file outside the repository, e.g. `C:\ANASTYLOS\data\guide02-sealed\cases.sealed.json`.

## 3. Seal before running

1. `sha256sum C:/ANASTYLOS/data/guide02-sealed/cases.sealed.json` (or `Get-FileHash`).
2. Commit **only that hash** (not the cases) together with the commit id of `claims.py` that will be run, e.g.
   as a line in a doc on the integration branch. Both are fixed from then on.
3. Only after that commit, run (from `tools/guide`):

   ```
   python guide02/run.py --cases C:/ANASTYLOS/data/guide02-sealed/cases.sealed.json --cases-sha256 <committed hash> --out C:/ANASTYLOS/data/live-dev/guide-claims/<run-id>/sealed-score.json
   ```

   `run.py` refuses to score a file whose hash differs from `--cases-sha256`. It verifies each answer against
   the package the case file names, whose bytes must carry the recorded hash (otherwise every case is
   rejected with `package: sha256 …`, which is a failed run, not a pass).

## 4. Acceptance (decided before the run)

- Adversarial accepted: **0**.
- Controls rejected: **≤ 1 in 10** (`controls_rejected × 10 ≤ controls`).
- Additionally, the old E2 package must be rejected as `unsupported package schema` without a crash
  (covered by `tests/test_claims.py`).

`run.py` exits 0 only when both numbers hold. Report the per-class table and every misclassified case with its
reasons, whatever the outcome.

## 5. After the run

- One run per sealed set. If the verifier is changed after seeing any sealed result, that set is spent: the
  next claim needs a new owner-written set, sealed the same way.
- The sealed cases may be committed after the run (with their hash unchanged) so the result can be reproduced.
