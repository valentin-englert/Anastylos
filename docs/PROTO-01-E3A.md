# PROTO-01-E3A — independent test of adaptive discovery and linking (preregistered)

Status: preregistered 2026-10-29, before any E3-A footage exists.
Owner decision 2026-10-29: freeze implementation, configuration and
thresholds after the E2 development runs; E3-A uses new footage with
sealed ground truth and fixed criteria. Nothing is tuned on E3-A.

## 1. Question

Without an inventory, names, coordinates or part counts, does the frozen
adaptive pipeline discover the sources of an unfamiliar arrangement and
propose correct source-to-deposit links, while keeping uncertain cases
`ambiguous` / `unattributed`?

## 2. Frozen software

Branch `feat/custody-v1.2`, commit `e057cf4f`, worktree
`Anastylos-custody-v12`; observer 1.2.0, linker 1.1, proposals 0.2.0;
42/42 tests. Run with the default `custody-config.json` (no grid config).

| File | SHA-256 |
|---|---|
| `anastylos_custody/proposals.py` | `c318df966dd3d96e63816c897b88898f6e6afa5073f3b9bdb96fbb9869ad2cd2` |
| `anastylos_custody/linker.py` | `998485806069b78eebf90609efcab3929b7fd252e7333162b2005d00300b339a` |
| `anastylos_custody/observer.py` | `61ee588519ad75db52ddde7d79de3106e6352fc3b5b2d472d16952457178bfdf` |
| `anastylos_custody/session.py` | `c87c4fb8e954e8b54fb3b204deb1d6bb70395597882c119bb463344e04dd0b60` |
| `anastylos_custody/cli.py` | `d85bb82353b33219fab07f4d6c7efbe824952f91ac5a19de8052b7d163c88a52` |
| `custody-config.json` (file in this worktree, LF) | `87f748f93efe7a3d62faac0c65a6267b919df5f3e89d2d3dbb5ae60ea1e76680` |

Other module files as committed at `e057cf4f` (three of them differ
byte-wise from the `Anastylos-custody` worktree only by line endings).
Proposal parameters (`DEFAULTS`): paper V ≥ 150 and S ≤ 40, mask closing
3 mm, quiet hold 0.6 s, confirm hold 1.0 s, minimum area 8 mm², occluder
≥ 4 mm² outside the device mask, pose break at 30 % of the device, merge
gap 1 mm, same interval 0.25 s, proximity ratio 0.5.

Development history that this test must be independent of: E2 (four
face buttons, SNES front shell). Results on E2 were retrospective:
E2-D run 1 FAIL, run 2 PASS; adaptive pass with proposals 0.1.0 0 links
(fragmented source, ambiguity cascade); with linker 1.1 and proposals
0.2.0 4 correct, 0 incorrect links (`E2-adaptive-r2`). None of these
count as validation.

## 3. Arrangement (owner, sealed)

Not the SNES front shell and not the four face buttons. An object or
board the pipeline has never seen, at rest on the work area, with 5–8
removable parts of clearly different size and shape, all of which fit a
tray cell of the mat in use. It must include:

- **split case:** two neighbouring parts (≤ 5 mm apart) removed in
  separate steps;
- **merge case:** one part whose removal exposes a ribbed or multi-part
  floor (likely to fragment);
- **non-removal control:** one part shifted within the object and left
  there (no removal may be claimed for a deposit);
- **skin-coloured part:** one part close to skin colour;
- **arm-rest control:** the hand rests motionless over the object for
  ≥ 5 s once (must never become a source);
- **pose control:** after the last deposit, the object is pushed
  deliberately by ≥ 10 mm and left (later changes must be unknown, and no
  link may cite a source after the push).

The owner photographs the arrangement with labels (sealed, not shown to
the Technical Lead), rolls order and target cells with a sealed script,
records with the witness phone, and writes the sealed actual file, as in
`PROTO-01-NOUV.md` §4. Capture standard as in `PROTO-01-NOUV.md` §8b
(or the new mat's validated settings). Hands may be bare; no tweezers
required; no nails required if the object rests stably.

## 4. Processing (Technical Lead, blind)

1. Verify §2 hashes.
2. `custody.py discover --video E3A.mp4 --out run`, then
   `custody.py observe --video E3A.mp4 --inventory run\inventory.json --out run\pass2`,
   then `custody.py link`.
3. Hash and commit all outputs; only then request the sealed files.

## 5. Criteria (fixed now)

A discovered source matches a true source location when its centroid
lies within the location's labelled outline grown by 3 mm (or, if only a
point is labelled, within 0.6 × the part's largest dimension).

| Outcome | Criterion |
|---|---|
| PASS | source recall ≥ 60 % of removed parts, ≤ 1 false-positive source, ≥ 50 % of deposits correctly linked, 0 incorrect links, all three controls clean |
| STRONG PASS | recall ≥ 80 %, 0 false positives, ≥ 80 % correct links, 0 incorrect links, controls clean, split and merge cases both resolved correctly |
| FAIL | any incorrect link, a control producing a removal or a link, the resting hand becoming a source, or recall < 60 % |
| INCONCLUSIVE | hash mismatch, registration rejected, or a deviation that breaks the script |

Reported regardless: per-case results (split, merge, skin-coloured part,
controls), every `ambiguous` / `unattributed` deposit with its reasons,
interventions, and the assumptions of `P2-ADAPTIVE-DISCOVERY.md` §4.
A failure is reported as such; any later fix is a new version and needs a
new independent test.
