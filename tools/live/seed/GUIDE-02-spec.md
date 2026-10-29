# GUIDE-02 — regression test specification for spoken guide answers

## Baseline (measured 2026-10-29, unchanged tools/guide/check.py, v21 package 1b84d4c8…)

Development set `cases.dev.json` (TL-written): 13 adversarial cases in 7 classes, 5 controls.
check.py accepted 13/13 adversarial cases and 5/5 controls (`baseline.json`).

## Failure classes (each needs ≥ 2 cases in the sealed set)

| class | example | rule |
|---|---|---|
| C1 identity by colour or name for an automatic part | "den grünen Knopf aus Zelle 3 [obs-0001]" | GUIDE_RULES 4 |
| C2 certainty or verification claim not on the word list | "sicher erkannt", "bestätigt", "zweifelsfrei" | GUIDE_RULES 4, 5a |
| C3 swapped or invented source relation between cited parts | "Zelle 3 an die Stelle von Zelle 6" | GUIDE_RULES 1, 3 |
| C4 wrong install order between cited parts | "zuerst Zelle 3, dann Zelle 6" (package says 6 before 3) | GUIDE_RULES 5 |
| C5 basis inversion: declared part presented as system-assigned | "vom System zugeordnet [dec-0005]" | GUIDE_RULES 5a |
| C6 invented mechanical dependency | "muss vorher rein, weil sie darunter liegt" | GUIDE_RULES 3, 5 |
| C7 later observation presented as history | "das aktuelle Foto zeigt …, also kam es von dort" | evidence rule |

C1–C3 are the three failures demonstrated on 2026-10-29; C4–C7 extend them.

## Verifier under test (to be built in an authorised lane)

Muse returns `anastylos-guide-claims@1` (claims with `relation`, `subject`, `object`, `basis_word`); a
deterministic verifier checks each claim against the package (stored_in cell, source relation, install step
order, basis, dependency = must be `not_observed` unless `dependencies.confirmed`), and speech is **rendered
from verified claims**. Free text from the model is never spoken unless every entity in it maps to a verified
claim. For free-text inputs (the cases above) the verifier first maps sentences to claims deterministically
(ids, cells, order words, basis words, colour/name lexicon for automatic parts); anything it cannot map is
rejected (fail-closed).

## Acceptance

- Sealed set written by the owner (not the TL), ≥ 20 adversarial + ≥ 10 controls, hash committed before the
  verifier is run on it; the development set may be used for building, never for the claim.
- Adversarial accepted: 0. Controls rejected: ≤ 1 in 10.
- The old E2 package (older schema) must yield REJECT or a clear "unsupported package schema" error, not a
  crash (F2).
- The verifier checks the package hash against the hash recorded when the package was built (F6).
