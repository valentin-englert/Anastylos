# FULL-01 — exploratory full disassembly (development data)

Label: **FULL-01 exploratory / development data.** Not E3-A, not
independent validation, not scored. Purpose: learn what the current
pipeline can and cannot represent across a complete controller
disassembly, before the A3 mat is replaced. E3-A stays preregistered and
untouched ([`PROTO-01-E3A.md`](PROTO-01-E3A.md)).

## 1. Setup (unchanged)

Current `surface_a3` sheet on MDF, nail fixture, overhead phone in Pro
video (ISO 400, 1/50 s, 5600 K, MF 0.6, UHD 30), witness phone running
continuously. Camera and sheet do not move between phases; if either
moves, record a new 10 s static before continuing and log it.

## 2. Phases, files and baselines

One overhead file per phase in `C:\ANASTYLOS\incoming\`. Every file starts
with **≥ 10 s static, hands out** (baseline of that pose) and ends with
**≥ 5 s static**. Hands may be bare; tweezers optional (log when used).
After each removal or deposit: hands out of view for ~2 s. No speaking.

| File | Pose | Content |
|---|---|---|
| `FULL-01-P1.mp4` | controller back up in the nails | remove the back-shell screws one at a time, each into its own empty cell |
| `FULL-01-P2.mp4` | transition | lift the controller out, open the shell, take out the PCB (with cable) and the back shell; parts that do not fit a cell go to a fixed place **outside** the sheet; place the front shell interior up in the nails; end with 10 s static (this is the P3 baseline pose) |
| `FULL-01-P3.mp4` | front shell interior up | remove the remaining parts one at a time: face buttons, D-pad, Start/Select, membranes, shoulder parts; a part goes into an empty cell only if it lies fully inside it; otherwise to its fixed outside place |

Cell use (owner, 2026-10-29): twelve cells are not enough for one part
per cell, so **all back-shell screws share one cell** and **the two
identical white rubber nubs** (between the PCB sensor and the L/R buttons)
**share one cell**; every other part that fits gets its own cell, and a
cell is never reused for a different part type. Parts that do not fit go
outside the sheet, each at its own labelled place (paper slips `OUT-A`,
`OUT-B`, …). Expected consequence: only the first deposit into a shared
cell can be linked; later ones are deposits into an occupied cell and stay
`ambiguous` by the linker's rule, and identical parts in one cell are a
compatible group, never individual identities.

## 3. Inventory policy

- No operator-named source inventory is given to the pipeline. Sources
  come from adaptive discovery (`custody.py discover`) per phase.
- For comparison only, the Technical Lead may afterwards build an
  operator-defined inventory from a labelled still of each pose; such
  runs are labelled "operator inventory".
- P2 is a pose transition: no source tracking is claimed across it. The
  P1 device pose ends when the controller is lifted; P3 starts a new
  pose from its own baseline.
- Parts stored outside the sheet are never linked automatically. They
  enter Assembly Memory only as operator-declared observations
  (`declared_by`), clearly marked.

## 4. Ground-truth log (owner)

From the witness video, after recording, one CSV per phase
(`FULL-01-P1.log.csv`, …), kept on the stick until the Technical Lead
has hashed the processing outputs:

```text
step,video_time_mm:ss,part (own words),from,to,tool,notes
1,00:14,screw,top-left hole,cell-1,screwdriver,
2,01:02,PCB with cable,device,OUT-A,hands,cable stays attached
```

`from` and `to` in the owner's own words or as `cell-N` / `OUT-x`. Send
only each file's SHA-256 (`Get-FileHash … -Algorithm SHA256`); the
Technical Lead commits the hashes before looking at any log.

Also log, in the same files under `notes`: every intervention (pushing
the device back, re-seating a nail, a part dropped, tweezers used, a
part moved twice).

## 5. Processing (Technical Lead)

Per phase, with frozen code only (no tuning on FULL-01): observer v1.3
with `custody-config.paper.json` (worktree `Anastylos-custody-v13`):
`discover` → `observe --inventory <discovered>` → `link`. Outputs to
`C:\ANASTYLOS\data\full-01\<phase>\`, hashed and committed before the
logs are opened. Then a descriptive report, no pass/fail:

- per part: source found? deposit seen? link and status; or why not
  (unobservable source, part outside, pose change, too small, low
  contrast);
- phase transitions and what the pipeline did at each;
- manual interventions and declared parts;
- integration problems for memory, guide and the next mat.

## 6. Expected limits (stated before recording)

Screw removals are likely not observable from overhead (pre-flight B).
Parts larger than 26 mm, the PCB with cable and the shell halves cannot
be deposited in cells and will only appear as operator-declared. Moving
the device from P1 to P3 ends every source location of P1.

## 7. What happened (2026-10-29) — descriptive, development data

Recorded: `FULL-01-static.mp4` (`5b4844cb…`, 7 markers, all 12 cells
measured empty), `FULL-01-P1.mp4` (`f94d72b7…`, 98 s, **no hands-off
pauses**), `FULL-01-P2.mp4` (`8fb51279…`, 76 s), `FULL-01-P3.mp4`
(`029e499d…`, 93 s). Deviations: no ground-truth CSV (owner compares the
outputs with the videos himself); P2 lifted only the back shell, so the
front shell stayed in the nails (no device pose change between P2 and
P3); L in cell 8, R in cell 11, D-pad and its membrane in free cells as
overhang tests. Processing: frozen observer v1.3 `104772a9` with
`custody-config.paper.json`, `discover` → `observe` → `link`; outputs in
`C:\ANASTYLOS\data\full-01\<phase>\`. Registration 7 markers in every
phase (RMS 0.29–0.31 mm), all drift fits accepted, no sampling gaps.

| Phase | Discovery | Tray events | Links |
|---|---|---|---|
| P1 screws, no pauses | 33 sources, 8–1050 mm² (fragments of screw holes, cable, hand shadows); the device was re-registered 34 times (pushed while unscrewing); 16 sources ended in pose breaks | cell-1 `appeared` at 25.6–28.6 s, then twice `changed_while_occupied` (more screws); cell-6 `changed_while_empty` twice (no part) | 3 deposits into cell-1, all `ambiguous` (many pending sources; occupied cell) |
| P2 back shell, PCB, nubs, L/R | 0 sources: lifting the back shell changes the whole device, so the pose epoch ends at 18.2 s and nothing later is proposed | L → cell-8 `appeared`, R → cell-11 `appeared`; nubs → cell-2 only `changed_while_empty` (white rubber on white paper stays below the paper-model threshold); cell-12 `changed_while_empty` (R overhang) | L, R `unattributed` |
| P3 front shell parts | 20 sources; the four face-button wells are found at the right places and times (≈ 53–75 s), each as 1–2 fragments | face buttons → cells 3, 6, 4, 5 all `appeared`; D-pad or its membrane → cell-7 `appeared`; cell-8 `changed_while_occupied` (overhang into L's cell); cell-10 `changed_while_empty` | 6 deposits, all `ambiguous` |

Exploratory pass-2 variant (development only, not a frozen config):
paper model plus the v1.2 ring rule (`custody-config.paper-ring.json`
`e7b4c390…`, kept in the data folder). It removes every pose break in P3
(27 `anchor_ring_changed` instead), but the links stay `ambiguous`
because 28–35 removals are pending at each deposit.

## 8. Findings for the next versions

1. **Parts that leave for unwatched places poison the linker.** Removals
   of the PCB, shells, membranes and Start/Select go outside the sheet;
   they are never deposited in a cell, so they stay pending forever and
   every later deposit is ambiguous. Needed: operator-declared outside
   deposits (`OUT-x`) as input, the counterpart of declared removals.
2. **Large removals cause mass changes.** Removing a membrane or the back
   shell changes many neighbouring sources at once (P3 14–28 s, P2 whole
   device). Needed: treat a large change over many sources as one removal
   (or as a new pose) instead of many removals.
3. **No pauses blur everything.** P1 without hands-off pauses gives one
   deposit window for several screws and 33 fragment sources. Pauses help;
   the Northstar needs hand tracking (H1) instead.
4. **White and low-contrast parts** (rubber nubs) are below the paper
   model's grey-level threshold. Needed: a colour/saturation term or
   texture, not just grey contrast.
5. **Overhang** of D-pad/R into neighbour cells shows up as
   `changed_while_*` in the neighbour, not as a false deposit — the paper
   model helps here.
6. **Config combination:** the paper model and the ring rule must be one
   frozen configuration for any next test.
7. Positive: all four face-button sources and all tray deposits of
   cell-sized parts were observed in a full disassembly.

## 9. OUT-x and the open candidates of P3 (2026-10-29) — development data

**OUT-x (linker 1.2)**, branch `feat/custody-v1.3` commit `886de0ba`,
56/56 tests (10 new: missing declaration, named closure marked
`operator_assisted`, unnamed declaration closes nothing, named source
without evidence rejected, wrong site rejected, declaration before the
removal rejected, duplicate closure rejected, overlapping removals stay
ambiguous, contradiction with an automatic link becomes a conflict, a
declaration after the deposit does not shape it). Input
`custody-declared-deposits@1`, generic (`location` is free text; nothing
about SNES parts or OUT-A…F in code). Counts separate `automatic_links`
from `operator_assisted_links`; declarations never raise the automatic
count.

**What the 28–35 open candidates of P3 are** (event groups of the
pass-2 variant with ring rule, read against overhead frames by the
Technical Lead):

| Window | Site events | Physical action |
|---|---|---|
| 14–21 s (+1 late) | 8 | face-button membrane lifted off (one removal) |
| 22 s | 1 | transient 8 mm² change |
| 24–28 s | 11 | D-pad membrane lifted off (one removal) |
| 40–44 s | 9 | D-pad removed — the same sites as its membrane, changing a second time (stacked parts) |
| 51–55 s | 2 | green button |
| 59–63 s | 2 | blue button |
| 66–75 s | 3 | yellow and red buttons (one fragment extra) |
| 78–81 s | 3 | Start/Select rubber |

About **8 real removals produced 39 site events**: 30 are fragments of
the same removal or second changes of the same site (stacked parts), 1 is
transient. The deposits in cells 3–7 therefore see 27–35 pending removals.

**OUT-x on P3 with a draft declaration** (written by the Technical Lead
from the video, *not operator-confirmed*,
`P3\outx-draft\declared-deposits.DRAFT.json`): face-button membrane and
Start/Select declared outside, each naming its largest fragment. Both
declarations close their removal, but each closes one fragment only;
27–34 removals stay pending and all 6 deposits stay `ambiguous`
(0 automatic, 0 assisted links).

Conclusion: OUT-x works as bookkeeping, but on FULL-01 the remaining
ambiguity comes from fragmentation of large removals and from stacked
parts, not from missing declarations. Next (separate from OUT-x): group
site changes that occur in the same observed interval and touch each
other into one removal event, and model a site that changes twice as two
layered removals.

## 10. Removal grouping, linker 1.3 (2026-10-29) — development data

Branch `feat/custody-v1.3` commit `341cf77e`, 63/63 tests (7 new: fragments
of one large removal become one event and link; no grouping without
geometry; same time alone never groups distant sites; close sites in
different intervals stay separate; a site that changes twice gives two
removals; declared removals are never grouped; an outside declaration
naming one fragment closes the whole event). Rule, fixed before the runs
below: same observed interval (start and end within 0.25 s) and site gap
≤ max(2 mm, 0.5 × √area of the larger site), chained; enabled by
`custody.py link --inventory <inventory used by observe>`.

| Run | Before (linker 1.2) | Linker 1.3 |
|---|---|---|
| E2 named inventory | 4 links | 4 links, unchanged |
| E2 adaptive (`E2-adaptive-r2`) | 4 links | 4 links, unchanged |
| FULL-01 P1 | 3 ambiguous | 3 ambiguous (3 groups; 4–5 pending) |
| FULL-01 P3 | 6 ambiguous, 27–35 pending | 6 ambiguous, 5–10 pending (6 groups) |
| FULL-01 P3 + draft OUT-x | 6 ambiguous, 27–34 pending | 6 ambiguous, 4–9 pending |

What still keeps P3 ambiguous (pending removals at the first deposit,
cell-7 at 44–47 s): (1) the D-pad membrane removal (24–28 s) — its deposit
into cell-10 was white rubber on white paper and only gave
`changed_while_empty`, so the removal never closes (finding 4 of §8);
(2) a transient 8 mm² change at 22 s; (3) one late fragment of the
face-button membrane whose window ends 3 s after the rest and so is not
grouped; (4) the D-pad overhanging from cell-7 into cell-8 gives a second,
overlapping `changed_while_occupied` deposit. Each later deposit inherits
these open removals and overlaps the earlier deposits' windows.

Next, each a separate step: colour or saturation in the paper model
(white and yellow parts), a minimum persistence or size for a removal
event, and treating a change in a cell next to a fresh deposit with the
same window as overhang rather than a second deposit.
