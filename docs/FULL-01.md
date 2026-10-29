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

## 11. Colour and edge cues, observer v1.4 (2026-10-29) — development data

Measured first on development frames (FULL-01, E2R): empty printed cells
0–8 mm² grey, 0 saturated, 22–59 mm edges; yellow button 40 mm² grey but
176 mm² saturated; white rubber nubs 8 mm² grey, 0 saturated, 189 mm
edges; white D-pad membrane 0 / 0 / 261–292 mm. Saturation fixes pale
colours; white on white is only visible as structure (edges).

Branch `feat/custody-v1.3` commit `fed75867` (observer 1.4.0), 68/68 tests
(5 new: white part found by edges, yellow by saturation, printed digit
edges alone stay empty, white part removed is `vacated`, cues off keeps
v1.3). A cell is occupied when any cue fires: grey area ≥ 25 mm²,
saturated area (S > 60) ≥ 25 mm², or edge length ≥ 120 mm; a change of
cue occupancy also starts a candidate, so a part that barely changes the
grey image is still seen. `custody-config.v14.json` is the one combined
configuration (paper model + ring rule + cues). Thresholds come from
these development frames.

| Run | Result |
|---|---|
| E2 named / adaptive, linker 1.3 | 4 links each, unchanged |
| E2R re-analysis (returns checker accepts 1.4.0) | 5/5 returns, all consistent, unchanged |
| FULL-01 P2 | white nubs → cell-2 now `appeared` (was `changed_while_empty`); 0 pose breaks; L, R, nubs `unattributed` (P2 has no sources after the back shell lifts) |
| FULL-01 P3 | start state measured correctly (cells 1, 2, 8, 11 occupied); white D-pad membrane → cell-10 now `appeared`; 0 pose breaks; 7 deposits, all `ambiguous` |
| FULL-01 P1 | new: cell-6 `appeared` 38–49 s and `vacated` 52–89 s — something with edges lay in cell-6 (tool or screw?), to be checked by the owner; 4 deposits `ambiguous` |
| FULL-01 P3 + draft OUT-x | 3–9 pending; still 0 links |

What now blocks the first P3 link (cell-10 at 30–35 s, pending: the
transient 8 mm² change at 22 s and the late fragment of the face-button
membrane, besides the true D-pad membrane removal): two noise removals.
Every later deposit inherits them. Next, separately: a minimum
persistence or size for a removal event, and a looser end-of-interval
rule for late fragments of a grouped removal.

Also found: a site whose anchor ring is fully covered by other sites has
an empty ring; the anchor fraction is then NaN (`RuntimeWarning: Mean of
empty slice`), and the comparison silently takes the re-alignment path.
To be fixed as its own change.

## 12. Noise rules, linker 1.4 and observer 1.4.1 (2026-10-29) — development data

Branch `feat/custody-v1.3` commit `8cce4790`, 74/74 tests (6 new). Rules
fixed before the runs: (1) a grouped removal whose total changed area is
below 25 mm² (the minimum a tray cell needs to count a part) is reported
under `minor_site_changes`, not used as a removal; (2) fragments join when
they start in the same interval (±0.25 s) and their windows overlap, the
end may differ; (3) a site with an empty anchor ring (all ring pixels
belong to other sites) relies on the device pose instead of a silent NaN
that always ended its pose epoch.

| Run | Links (linker 1.3 → 1.4) |
|---|---|
| E2 named / adaptive | 4 automatic → 4 automatic, unchanged |
| FULL-01 P1 | 0 → 1 automatic (cell-1 ← SRC-16/33/6) — **suspect**, see below |
| FULL-01 P2 | 0 → 0 (3 unattributed; no sources after the back shell lifts) |
| FULL-01 P3 | 0 → **2 automatic by elimination**: cell-3 ← SRC-14+SRC-2 (≈ 241/144 mm, removed 51–55 s) and cell-6 ← SRC-16+SRC-17 (≈ 255/154 mm, removed 59–63 s); time and place fit the green and the blue button; 5 ambiguous |
| FULL-01 P3 + draft OUT-x | the same 2 links, now counted `operator_assisted` (a declaration preceded them) |

Minor site changes set aside: P3 SRC-3 (8.0 mm², the transient) and the
late SRC-1 fragment at 66–75 s (17.9 mm²); P1 two (24.8 and 8.6 mm²).

Caution on P1: the linked event starts at 14.4 s but ends at 89 s because
one of its sites never became clean for 75 s, and one site (SRC-33,
1050 mm²) is far larger than a screw hole. Rule (2) accepts any end as
long as start and overlap match; that is weak evidence when the end is
much later. This link is not counted as plausible until the owner checks
it on the video; a bound on the end difference is a candidate for the
next version.

None of these are verified correspondences: FULL-01 has no sealed ground
truth, and the owner compares them with the videos.

## 13. Owner's video review of the linker 1.4 links (2026-10-29)

Not sealed ground truth: the owner reviewed the witness video
(`FULL-01-Zeuge.mp4`) and the overhead clips after the outputs existed.

| Link | Owner's review | Technical Lead's check | Verdict |
|---|---|---|---|
| P3 cell-3 ← SRC-14+SRC-2 | green button emptied first, put in cell 3 | sources at ≈ 241/144 and 237/154 mm = green well; removal 51–55 s, deposit 51–56 s | **consistent** |
| P3 cell-6 ← SRC-16+SRC-17 | blue button right after, cell 6 | sources at ≈ 255/154 and 265/156 mm = blue well; removal 59–63 s, deposit 58–64 s | **consistent** |
| P1 cell-1 ← SRC-16/33/6 | unscrewing lasted until ≈ 89 s (the long window is real); first screw from the top-left hole H2 | holes in the P1 static frame: H1 151/135, **H2 183/162**, H3 215/125, H4 249/162, H5 281/135 mm. SRC-33's box (135–195 × 114–176 mm, 1050 mm²) contains H1 **and** H2; SRC-16 and SRC-6 (8–12 mm²) contain no hole | **not wrong, but not hole-level**: the source region covers two holes |

So FULL-01 now has 2 automatic links that the owner's video review
supports, at the level of a well, and 1 link whose source is too coarse to
name the hole. None is an independent validation.

Next for P1-type cases: a source whose geometry covers several candidate
sources (here two holes) should be reported as `coarse` and never stated
as a specific hole; screw holes themselves are not observable from
overhead (pre-flight B), so screws stay an operator-declared case.

## 14. Screws as an interchangeable group (owner statement, 2026-10-29)

The owner states that the five back-shell screws are identical. Per
`PROTO-01-NOUV.md` §6 the word "mechanically interchangeable" needs a
caliper measurement (length, head Ø, thread Ø within 0.2 mm); until that
is recorded, the group is **operator-declared identical, not measured**.

Consequence for memory and guidance: the screws are one compatible group
— five parts in cell 1 that belong to the five holes H1–H5, without
per-screw identity or per-hole assignment. Reassembly guidance says "any
screw from cell 1 into any of H1–H5" and names the basis (operator
statement, measurement pending). Hole-level attribution is then not needed
for reassembly; it would only matter for non-identical screws, where
tool-contact evidence plus a cell counter would be the next step.
