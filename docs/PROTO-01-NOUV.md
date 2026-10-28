# PROTO-01-NOUV: protocol variant without UV instance coding

Status: preregistered variant, not executed. Prepared 2026-10-28 by the
Technical Lead on owner directive (UV markers ordered, delivery
unconfirmed). It does not replace `docs/PROTO-01.md`: the UV protocol,
its scoring definitions and its acceptance table stay unchanged and can
run later as an additional, stronger evaluation. Results of this variant
are never re-scored under PROTO-01 and vice versa.

Frozen software and pre-flight checks are those of PROTO-01 §2 and §5
(custody-ledger `496e323f` on `feat/custody-ledger`, canonical config
`10b58c2d…`). No code, configuration or threshold changes.

## 1. Questions and claim boundary

| Question | In scope |
|---|---|
| Source-state recovery: which mounting location changed from occupied to empty? | yes |
| Source-to-deposit correspondence: which source location (or compatible source group) corresponds to an observed deposit? | yes |
| Physical instance identity: is this exact screw the object that occupied that location? | **no** |

Every result is labelled:

- "automatic link given an operator-defined source-site inventory"
  (sites are initialized manually; no autonomous site discovery);
- "protocol-based ground truth, without UV instance verification".

Instance-level re-identification is never reported from this variant.
For screws, functional interchangeability may be stated only if the
calipers show identical screws (§6); that is mechanical compatibility,
not historical identity. For unique components (E2), the part's
identity is verified physically by inspection before and after the
take; the system itself does not re-identify anything.

## 2. Experiments

| ID | Take | Parts | Controls (E3) |
|---|---|---|---|
| E1 | controller back up, screws in place | back-shell screws, one at a time, each into its own empty cell | C1: tool engages a screw that stays in; C2: one deposited screw moved to an empty cell |
| E2 | front shell interior up, PCB and membranes removed off camera | face buttons, Start/Select, D-pad if it fits a cell (§7) | C3: one button lifted and put back into its own well; C4: one deposited part moved to an empty cell |

Each take is a separate recording with an empty tray at the start, its
own static start and its own site inventory. At most two takes per
experiment; a third needs owner approval.

## 3. Roles and blindness

- **Owner (ground-truth holder):** rolls and seals the intended script,
  performs the take, keeps the witness recording and all ground-truth
  files off the workstation until the producer's outputs are hashed.
- **Technical Lead (producer and scorer):** writes the site inventory,
  runs the frozen observer, hashes the outputs, and only then receives
  the ground truth. Does not view take frames beyond the static start
  used for the inventory.
- Muse A is not needed for this variant; it continues brief A1.
- The overhead video carries an audio track: **no spoken labels during
  a take**.

## 4. Ground truth without UV

Per take, in this order:

1. **Before the take:** the owner photographs the device and labels
   every source location (`H1…Hn` for holes, `W-red`, `W-dpad` … for
   wells; positions and part names only). This photo goes to the
   producer. The owner rolls the die for the order of locations, the
   target cell of each removal and the control positions, writes
   `nouv-<E>.script.json` with a `nonce` (≥32 random characters),
   computes the SHA-256 (`Get-FileHash … -Algorithm SHA256`), sends only
   the hash, and moves the file off the PC. The Technical Lead commits
   the hash (§9) before the take.
2. **During the take:** a second phone records as witness, close to
   device and tray, continuously, and stays off the PC. The owner notes
   any deviation from the script on paper.
3. **After the take:** the owner reviews the witness video and writes
   `nouv-<E>.actual.json`: the actually performed sequence (location →
   cell), the controls as performed, and the deviations, with its own
   nonce; hash sent and committed. For E2 the owner also photographs
   each cell (part identity) and the emptied wells; those photos stay
   off the PC until scoring.

The script alone is not proof that an action happened. Scoring uses
`nouv-<E>.actual.json`, checked against the witness video; for E2 also
the physical cell contents. Without a witness phone, E1 falls back to
script plus paper log and is labelled "operator-testimony ground
truth"; E2 still has its physical before/after check.

## 5. Processing (Technical Lead, before seeing any ground truth)

1. Verify the PROTO-01 §2 hashes.
2. Write the `custody-site-inventory@1` file from the labelled photo and
   a static, registered frame of the same setup (pre-flight A clip or
   the take's static start), basis `operator-defined`. Hash and commit
   it before running `observe`.
3. Run, from `Anastylos-custody\custody-ledger` with `Anastylos\.venv`:
   `custody.py observe --video <take> --inventory <inv> --out <run>`,
   then `custody.py link --events <run>\events.json --out <run>\custody.json`.
4. Hash all outputs and commit the hashes (§9). Only then request the
   ground-truth files.

## 6. Scoring

- Unit: each `candidate_temporal_exclusive` link (site → cell).
- **Correct:** `nouv-<E>.actual.json` records that a part was moved
  from that location to that cell, and (E2) the cell photo shows the
  part belonging to that well.
- **Incorrect:** any link that contradicts the actual record.
- Controls: C1/C3 must not produce a removal or a link for their
  location at that time; C2/C4 must not produce a link that gives the
  receiving cell a new source.
- Report correct, incorrect, ambiguous and unattributed links; sources
  detected per location; control outcomes; interventions (inventory,
  any manual step); deviations; the ground-truth label.
- Screws: measure all removed screws with calipers after E1 (length,
  head Ø, thread Ø). Only if they agree within 0.2 mm may an answer
  state that these screws are mechanically interchangeable.

| Outcome (per experiment) | Criterion (same thresholds as PROTO-01 §9) |
|---|---|
| PASS | ≥1 correct link, 0 incorrect links, all controls clean |
| STRONG PASS | PASS and ≥3 correct links |
| FAIL | 0 `site_changed` events, any incorrect link, or a control producing a removal or a new-source link |
| INCONCLUSIVE | hash mismatch, registration rejected, or a deviation that breaks the script |

These criteria are fixed before any take and are not changed after
results are seen.

## 7. Which SNES parts the frozen observer can realistically handle

Basis: observer rules in `custody-ledger/README.md` (sites are
operator-given rectangles on a resting rigid device; a content change of
≥8 mm² must be confirmed for 1 s while the zone is clean; tray cells are
26 × 26 mm), SNES-01/02 experience, and no new measurements of these
parts yet.

| Part | Expectation | Reason |
|---|---|---|
| Coloured face buttons (4) | good candidates | large contrast against the grey shell, fit a cell easily, one well each |
| Start / Select | possible | small and low-contrast rubber; the two may be identical (then a compatible pair, not two identities) |
| D-pad | conditional | must lie fully inside a 26 mm cell; SNES-01 showed overflow into neighbouring cells as false deposits. Measure first; if it cannot sit inside a cell with margin, exclude it and report "unsupported by tray geometry" |
| Back-shell screws | marginal | dark, ~5 mm heads; pre-flight B decides |
| Shoulder buttons L/R | unlikely | longer than a cell; sit on springs |
| Rubber membranes | not supported | deformable, cover several sites, dark on dark |
| PCB | not supported | tethered by the cable, larger than a cell |
| Shell halves | not supported | they are the device itself; removing them breaks the pose |
| Loose or multi-agent paths (e.g. a dropped screw) | `UNKNOWN` by decision V6 | |

Unsupported parts are reported as unsupported, never as successes.

## 8. Time box

- Day 1: set-up, pre-flight A and B, take E1 (with witness), screw
  measurement.
- Day 2 morning: E2 set-up (off camera), 15 s static clip, take E2.
  Afternoon: processing and scoring of E1 and E2.
- If E1 pre-flight B fails twice, skip E1 and go straight to E2: do not
  spend a further day on screws.
- From the video freeze on, no new takes.

## 8a. Fixture and stability gate

The sheet is printed paper permanently mounted on 5 mm MDF. Nothing is
taped or stuck to the paper; small holes in paper and MDF are accepted.

**Preferred fixture: nail pegs.** Four to six thin nails stand
vertically in the MDF around the controller outline, 10–15 mm proud,
each touching the controller's side wall lightly (snug, not
squeezing): one at each end lobe, and one or two along the top and the
bottom edge, offset so that the controller cannot rotate. Drive them
only about 4 mm deep so they do not go through the board.

**Fallback: pinned stop blocks.** If pegs do not hold, place a small
wooden or MDF block against each end lobe and fix each block with one
nail through the block into the MDF. Larger contact, still no adhesive.
The machine vise is not used: it occludes the view, clamps the plastic
shell and lifts the part off the sheet plane.

**Placement.** Everything stays inside the printed work area (frame mm
x 60–310, y 52–224), which keeps the marker quiet zones free. Keep the
controller and pegs left of x ≈ 290 mm, so they stay out of the 20 mm
context windows of the tray cells (x ≥ 318 mm). Long axis parallel to
the sheet's long side, back up. Route the cable towards the top edge
between markers 10 and 14, slack, and off the board; never across a
marker or a tray cell. Pegs belong to the scene: set them before
pre-flight A.

**E1 vs E2.** E1 needs the pegs: unscrewing applies torque about the
screw axis and downward pressure. E2 (front shell interior up, parts
lifted with tweezers) needs little force; the front shell has the same
outline, so it goes into the same pegs. There is no cable in E2.

**Two separate stability questions.**

| Relation | Checked by | Go criterion |
|---|---|---|
| Sheet to camera (board, stand) | pre-flight A, and the observer's drift fits during every take | registration valid on a static frame with all 7 markers; 0 events; all camera-drift fits accepted |
| Controller to sheet (fixture) | pre-flight B with an inventory of **all** holes | `site_changed` only for the removed screw's hole and `appeared` only for its cell; no `site_changed` for any other hole; no `pose_epoch_break`; `device.realignments` empty, or all accepted with `shift_px` ≤ 3 (≈1 mm at 3 px/mm) and `rotation_deg` within ±0.5 |

**Go / no-go for the E1 take:** go only if both rows pass. If the
controller-to-sheet row fails, add or move pegs (or switch to the
fallback) and repeat pre-flight B, at most twice; then skip E1 and go to
E2 (§8). For E2, its 15 s static clip must pass the sheet-to-camera row;
the controller-to-sheet values of the E2 take are reported with its
result. These are setup gates; they do not change the scoring in §6.

## 8b. Pre-flight results and capture standard (2026-10-28)

Run by the Technical Lead with the frozen software (§2 of `PROTO-01.md`,
all file hashes matched). Pre-flight clips are never scored.

| Clip (SHA-256) | Result |
|---|---|
| `preflight-A.mp4` first take (`eb0fe53d…`) | no-go: valid with 6 markers only; the cable covered marker 15 |
| `preflight-A.mp4` retake (`bf66eff8…`) | pass: 7/7 markers, RMS 0.281 mm, 0 events, 14/14 drift fits |
| `preflight-B.mp4` (`ced2883b…`), 5-hole inventory, H2 removed to cell 1 | fail: no `site_changed` for H2 (recessed screw well looks the same with and without the screw); auto exposure jumped while hands were in view, giving `appeared`/`vacated` on all 12 cells and a spurious H5 change. Fixture stable: no realignment, no `pose_epoch_break` |
| `preflight-EXPO.mp4` (`47f0c44f…`), hand sweep over device and cells | pass: 7/7 markers, 0 events, 19/19 drift fits, no realignment; paper brightness 164.0–164.7 throughout |

Capture standard for every further take (phone "Pro video" mode, all
values manual): ISO 400, shutter 1/50 s (matches 50 Hz mains), white
balance 5600 K, manual focus 0.6, UHD 30 fps. The phone shows EV +0.4,
which is not adjustable in this mode and has no effect with ISO and
shutter fixed. Lighting stays unchanged during a take.

Owner decision 2026-10-28: E1 is skipped without a further pre-flight B
retry, because the failure is screw-well observability, not the fixture
(§7 "marginal" confirmed). E1 is reported as "not run: source change not
observable from overhead in pre-flight B". Next is E2.

## 9. Commitment ledger

| Item | SHA-256 | Committed before |
|---|---|---|
| `nouv-E1.script.json` | pending | E1 take |
| E1 site inventory | pending | E1 `observe` |
| E1 outputs (`events.json`, `custody.json`) | pending | E1 ground-truth reveal |
| `nouv-E1.actual.json` | pending | E1 scoring |
| `nouv-E2.script.json` | pending | E2 take |
| E2 site inventory (`C:\ANASTYLOS\data\proto-01\E2\E2.inventory.json`: W-GREEN, W-YELLOW, W-BLUE, W-RED; D-pad and Start/Select excluded as wider than a cell) | `b66e1e0881e67438f19e082d5e4207d41a3e9ec8939cdd2a6394e1ab4c14ed1a` | E2 `observe` |
| E2 outputs | pending | E2 ground-truth reveal |
| `nouv-E2.actual.json` | pending | E2 scoring |

## Anhang: Ablauf heute (Operator, Deutsch)

1. Aufbau nach `docs/PROTO-01.md` Anhang A, Abschnitte A und B
   (UV-Material entfällt; nichts auf das Papier kleben). Halterung nach
   §8a: Nagel-Anschläge im MDF. Zusätzlich das zweite Handy als
   Zeugen-Kamera schräg und nah an Gerät und Tray, außerhalb des
   Overhead-Bildes.
2. Vorab-Test A: 15 s statisch, Hände raus, als
   `C:\ANASTYLOS\incoming\proto-01\preflight-A.mp4` ablegen, Bescheid
   geben, auf „A bestanden“ warten.
3. Foto mit Lochlabels H1…Hn an mich.
4. Vorab-Test B (eine Schraube, ca. 30 s, mit echtem Drehmoment) als
   `preflight-B.mp4`, Bescheid geben, Schraube wieder einsetzen, auf
   „B bestanden“ warten. B prüft zugleich die Halterung (§8a).
5. Skript auswürfeln, `nouv-E1.script.json` mit Nonce schreiben, nur
   den Hash schicken, Datei vom PC entfernen. Auf „Hash committet“ warten.
6. Take E1: Zeugen-Handy starten, dann Overhead. 10 s statisch, je
   Schraube Hände ganz weg, herausdrehen, allein in die Zielzelle, Hände
   ganz weg; C1 und C2 an ihrer Stelle; 5 s statisch. Nichts sprechen.
7. Overhead-Original nach `C:\ANASTYLOS\incoming\proto-01\` kopieren.
   Zeugen-Video auf dem Handy lassen.
8. Zeugen-Video ansehen, `nouv-E1.actual.json` schreiben, Hash schicken,
   Datei vom PC entfernen.
9. Schrauben messen (Länge, Kopf-Ø, Gewinde-Ø) und notieren.
