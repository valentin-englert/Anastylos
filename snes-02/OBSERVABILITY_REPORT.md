# SNES-02 observability report (Muse A)

Independent analysis of the dual-camera SNES teardown recording.
Times are CAM1 video PTS (seconds) unless marked C2; C2 runs ahead by
the validated +0.76896 s (CAM2 started first). Display pixels:
CAM1 720×1280, CAM2 1080×1920, origin top-left.

## 0. Verdict: PASS (scoped)

The physical site, the actual removal, and the destination are
**separately observable — but never all three from one camera, and
never all three for every screw.** Concretely, from footage alone:

- 5 distinct screwdriver↔site engagements exist (a 6th does not: PCB
  screws are finger-removed). 4 of 5 sites are localizable, all from
  the oblique view only.
- 4 removals are tool-transported with deposit evidence; the 5th is a
  loosening whose screw fell during cover removal and was
  finger-recovered. Tip-carry is directly visible once (R1).
- All 5 tray destinations are observable from the overhead view (cell 1
  counts 0→5 across the analyzed span).
- Zero false source/removal claims arise on the 5 negative controls.

Scope limits (not failures of method): S2a's site is finger-occluded
in both views; tray counts are blind 57–84 s (arm occlusion, closed
deductively); screw-vs-hole appearance is ambiguous (dark screws);
cross-camera linkage of a screw (site→tip→cell) is always inference,
never single-frame observation. No observation files are exported in
this revision: the `anastylos-observation-v1` contract is in conflict
(Muse A format withdrawn, owner decision pending).

## 1. Scope and method

Analyzed: the full 181 s (CAM1) / 183 s (CAM2) teardown at 0.5 fps skim
plus 179 exact paired stills (10 dense windows, per-pair residual gate
±40 ms, 0 failures) and ~70 full-resolution grid-overlay crops for
coordinate measurement. Coordinates were read by eye off labeled grids;
uncertainties are honest radii, not fitted errors. Every cited frame
below is identified by (camera, decoder frame, PTS) in the paired
sheets (`contact-sheets/`) and the pair logs (`work/cases/*/​*-pairs.json`).

Not seeded: no ground-truth times, no source boxes, no known answers
were used (none exist for SNES-02). The handoff file named in the
mission (`HANDOFF_NEXT_CHAT_2026-10-28.md`) was not found anywhere;
analysis proceeds from repo docs plus pixels. No SAM calls, no cloud,
no new dependencies, no matcher modifications, no commits.

## 2. Teardown timeline (CAM1 PTS)

| Span | Phase |
|---|---|
| 0–8 | Static scene: controller back-up on coded sheet |
| 8–12 | Hands enter, reposition controller (no tool) |
| 12–32 | E1: unscrew S1 (right-edge post) |
| 32–37 | D1 deposit (cell 1: 0→1), reposition |
| 37–41 | E2a: unscrew S2a (bottom-edge post) |
| 41–43 | D2a deposit (1→2), finger probe, re-engage |
| 43–56 | E2b: unscrew S2b (second bottom-edge post) |
| 56–59 | D2b deposit (2→3), 180° rotation |
| 59–79 | E3a: unscrew B (left-lobe post, 20 s — stubborn) |
| 79–84 | E3b: engagement at empty A-post (no removal) |
| 84–86 | D3 deposit (3→4), reposition |
| 86–91 | E4: loosen S5 (right-end post, no extraction) |
| 91–92 | R4/D4 tray visit: no deposit (tip never enters cell 1) |
| 92–95 | Pry + back-shell separation |
| 95–98 | Loose screw (S5) on sheet, finger recovery (4→5) |
| 96–104 | Shell placed aside, PCB exposed |
| 104–118 | Finger-only PCB screw removals to tray |
| 120–130 | PCB lifted out, placed aside |
| 132–181 | Buttons, pads, D-pad to tray cells (not analyzed frame-by-frame) |

## 3. Positive cases (5 screwdriver engagements)

### P1 — E1 at S1 (CAM1 12.2–31.9)

- CAM2: shaft descends to edge post; tip at surface point (770,800)
  ±25 px @f443/14.767 s. Tip partially self-occluded by shaft;
  insertion into head unresolved. Contact observed (shaft terminates
  at surface, sustained 19 s, removal follows).
- CAM1: hands cover the controller for the whole engagement; site
  unlocalizable; engagement inferable (driver in hand over controller).
- Removal R1 (31.6–32.3): screw silhouette ON the magnetic tip in C2
  @f973/32.433 and f983/32.767 — the only direct tip-carry in the set.
  C1 shows the descent to tray but cannot resolve the screw (~7 px).
- Destination D1 (32.3–40.0): cell 1 empty @f970/32.328 (C1), 1 screw
  @f1200/39.993 (C1). Cell 1 is outside C2's FOV.
- Before/after at site: S1 occupied-before unclear (cable proximity);
  empty-after unobservable (hand covers site @f993/33.1). Removal rests
  on tip-carry + tray increment, not on hole appearance.
- Alternative considered: tip pressing beside the post, not in it —
  rejected (19 s sustained contact + extraction + deposit).

### P2 — E2a at S2a (CAM1 37.0–41.0)

- CAM2: shaft-visible only; tip and site under operator fingers for
  the whole ~3.5 s engagement. No localization possible (location
  correctly absent, not guessed). Contact unknown (occluded).
- CAM1: hands occlude; same verdict.
- Removal R2a + D2a (41.0–42.5): lift observed; tray visit observed;
  count 1→2 between f1200/39.993 and f1700/56.657 with no other tray
  motion in between. Tip-carry not confirmed (fingers).
- S2a distinctness from S2b: inferred from two extractions + two
  deposits from the bottom-edge region (a twice-emptied single hole is
  impossible); hole separation unresolved (~50 px region).

### P3 — E2b at S2b (CAM1 43.0–56.3)

- CAM2: tip occluded by fingers during engagement (as E2a); hole
  glimpse (795,910) ±40 px @f1713/57.100 as the driver lifts.
  Location from the fresh hole, corroborated by shaft axis.
- CAM1: hands occlude throughout.
- Removal R2b + D2b (56.3–57.7): lift observed; placement motion
  directly observed (tip in cell 1 setting the screw down @f1720/57.323,
  f1730/57.657). Pile count of 3 never cleanly confirmed (occlusions
  57–84 s) — D2b success is closed deductively (4 at 90.984 with no
  other path; the rival hypothesis of a failed D2b is excluded in §5).

### P4 — E3a at post B (CAM1 58.7–79.0)

- CAM2: best-observed engagement in the set. Tip seated IN the post
  (505,735) ±15 px @f1898/63.267, stable across 60.5/68.5/76.5/78.5
  samples; B→A switch bracketed to CAM1 78.5–80.5. Contact observed.
- CAM1: hands occlude throughout.
- B occupied-before: unobserved (fingers @59.1 during rotation
  settle); B empty-after observed @f2538/84.600.
- Removal R3 + D3 (83.8–84.5): lift from A-post (see N6 — the screw
  rode the tip from B); D3 placement into cell 1; 4th screw stacked in
  cluster (3 resolved @f2532/84.385, 4 confirmed @f2730/90.984 with no
  intervening visit).

### P5 — E4 at S5 (CAM1 85.8–90.65)

- CAM2: tip at edge post (695,495) ±20 px @f2648/88.267, contact
  observed. Short engagement (~4.9 s, cf. E2a's 3.5 s).
- CAM1: hands occlude throughout.
- Loosening WITHOUT extraction: driver lifted bare; D4 tray visit
  90.7–91.9 never entered cell 1 (tip over cell 6 / parked; count
  stays 4 while tip is demonstrably elsewhere @f2742/91.384). S5 stayed
  in its well, fell during cover removal, landed at (455,440) on the
  sheet — far from the tool transit path, at the cover-removal spot —
  and was finger-recovered to cell 1 @98.5 (count 4→5).
- P5 is a positive *engagement* with a localized site, but its
  *removal* is multi-agent (tool loosened, gravity + cover motion
  dropped, fingers deposited). A matcher claiming tool-transported
  removal for S5 would be wrong.

## 4. Negative / ambiguous controls (5)

### N1 — hands, no tool (CAM1 7.5–11.5)

Palms on sheet, no tool in frame, no controller contact, no removal.
Clean true negative: no interaction, no state change, no deposit.

### N3 — pry + cover lift (CAM1 91.3–95.0)

Screwdriver used as a LEVER at the shell seam (C2 tip coarsely
(650,620) ±50 @f2803/93.433; C1 tip unresolved), then shell separation
with visible gap growth (C2) and PCB exposure. Tool contact without a
fastening site: must not yield a site or removal claim. Note the pry
*is* a tool interaction (not "nothing happened") — the control is
about attribution, not activity.

### N4 — loose screw + finger recovery (CAM1 95–98.5)

Screw present on sheet at (455,440)±10 (C1 @f2880/95.983) /
(700,1430)±15 (C2 @f2903/96.767); picked by fingers @97.5–98.0;
cell 1 count 4→5 @f2956/98.516. Removal-without-deposit followed by
deposit-without-tool. Origin (S5) is report-level inference from
elimination; frame evidence claims sheet-presence only.

### N5 — finger-only PCB screws (CAM1 103–112)

Multiple small-screw removals by fingers, screws accumulating in tray
cells; screwdriver visibly PARKED throughout (C2). Removal without
screwdriver: a tool-based matcher must stay silent here (no tool
events), while a completion audit must still notice the tray changed.

### N6/E3b — engagement at the empty A-post (CAM1 79.5–83.8)

Looks exactly like a removal engagement: tip seated in a post
(440,490) ±15 (C2 @f2498/83.267), 4 s sustained, then lift + tray
visit. Yields nothing — the single D3 deposit is B's screw (A-hole
already empty-looking at 63.3/77.3 while the driver worked B; count
chain allows exactly one screw from the E3a/E3b span). The strongest
negative in the set: appearance of removal with no removal.

## 5. Closed deductions (evidence chains, not observations)

Three questions could not be answered from any single frame and were
closed by elimination over counts + motions. Each chain is falsifiable
(the missing/alternative evidence is named).

**D2b succeeded (tray 2→3 at ~57.3).** Placement motion directly
observed (tip in cell 1 @57.3–57.7). Rival: D2b failed, D4 deposited
S5 instead (both give tray=4 at 91.0). Excluded: D4's tip never
entered cell 1 (over cell 6 @90.984; parked left @91.28–91.48 while
cell 1 already holds 4). With D4 excluded, the 4th screw at 90.984 can
only come from D3, the 3rd only from D2b. Would-be falsifier: a screw
in any non-cell-1 tray cell (all verified empty at 96.0).

**D3 made 3→4 and D4 added nothing.** Cell 1 holds 4 @90.984 with no
tray visit between 84.4 and 90.98 except D4's, and D4's tip demonstrably
stayed out of cell 1. Hence the 4th screw was placed by D3 @84.2
(stacked/hidden in the 84.385 cluster) and D4 placed nothing.
Would-be falsifier: any cell-1 tip entry 84.4–90.98 (none in R4T pairs).

**S5 was loosened, not extracted.** E4 (4.9 s) + bare lift + no deposit
+ loose screw appearing at the cover-removal spot (455,440), far from
the R4 transit path. A tip-carried S5 dropping in transit would have
landed along controller→tray (x 150–400, y 800–1100); it did not.
Would-be falsifier: a 6th screw anywhere (tray + sheet total exactly 5
at 98.5) or S5's screw visibly riding the R4 tip (tip hand-occluded in
C1, out of frame in C2 — unobservable either way).

## 6. Overhead-only vs paired-view comparison

Evaluated strictly separately: first what CAM1 alone supports, then
what the pair adds. Scores below are over the 5 positive + 5 negative
cases of §3–§4.

| Question | Overhead only (CAM1) | Paired (CAM1+CAM2) |
|---|---|---|
| Engagement detected (tool at controller) | 5/5 (driver-in-hand over controller) | 5/5 |
| Fastening site localized | **0/5** (hands cover all sites, all engagements) | **4/5** (S1 ±25, S2b ±40, B ±15, S5 ±20; S2a occluded) |
| Tip resolved | 2/5 (tray descents only, never at site) | 4/6 incl. E3b (S1 partial, B, A, S5; S2a/S2b under fingers) |
| Contact observed (tip at site) | 0/5 | 4/6 (S1, B, A, S5; S2a/S2b unknown) |
| Removal: screw out (any evidence) | 4/5 via tray increments only (no visual of extraction) | 5/5 (R1 tip-carry direct; R2a/R2b/R3 via lift+deposit+hole; S5 via fall+recovery) |
| Removal: tip-carry seen | 0/5 (7 px screws) | 1/4 transported (R1; R2a/R2b occluded, R3 too fast for sampling) |
| Destination (tray cell + count) | 5/5 (cell 1, counts 0→5; blind 57–84 s) | 5/5 (no C2 contribution: cell 1 outside C2 FOV) |
| Negative controls clean | 4/5 (N3 pry tip unresolved; rest clean) | 5/5 |
| Cross-camera screw linkage (site→cell) | impossible (no sites) | inferential only (never single-frame; see §5) |

Genuinely view-caused improvements (not resolution): site
localization (occlusion geometry — the oblique angle sees under the
hand arch where the top-down view cannot), contact judgment, pry-tip
resolution, loose-screw spotting redundancy.

Resolution confound, explicitly NOT attributed to angle: CAM2 is 1080p
vs CAM1 720p (~7 px/mm vs ~2.4 px/mm at the work area). Screw-on-tip
(R1), hole-vs-head appearance, and the A-hole emptiness judgment all
depend on pixels-per-mm at least as much as on viewpoint. A 1080p
overhead camera would likely recover tray-adjacent tip details but
*not* the occluded sites — the 0/5 site score is geometric (hands),
while the 0/5 tip-carry score is mostly resolving power. Conversely,
CAM2's tight framing costs it the tray: deposit evidence is CAM1-only
regardless of resolution.

Net: the pair is complementary, not redundant. CAM1 owns the deposit
ledger; CAM2 owns engagement localization. Neither camera observes a
complete custody event (site + carry + cell) for any screw; every
source→deposit link crosses the camera boundary and is therefore
inference over paired evidence, never observation.

## 7. What is missing (evidence gaps)

1. S2a site: finger-occluded in C2, hand-occluded in C1; approximable
   only to a ~50 px region by shaft extrapolation (not emitted as a
   location — correctly absent, not guessed).
2. Tray counts 57–84 s: forearm/hand occlusion (3 verified samples);
   D2b/D3 counts closed via placement + deduction, not direct piles.
3. Screw-vs-hole appearance: dark screws at 20–35 px (C2) make
   occupied/empty judgments appearance-weak; all presence claims lean
   on motion + counts, never on head rendering (no drive recess was
   ever resolved).
4. Turning motion: never directly visible (symmetric driver handle);
   "loosening" is corroborated by outcome, not by observed rotation.
   All engagements are honestly `engagement`, none `loosening`.
5. R3/R4 tip-carry: transport too fast (0.3–0.5 s) for the 3 fps
   sampling and out of the 10 fps ROIs; R4's tip additionally
   hand-occluded (C1) and out of frame (C2).
6. S5's well 90.65–94: never examined with the screw in/out question
   in mind after the fall hypothesis won on location evidence.
7. Buttons/pads phase (132–181 s): scanned, not analyzed; tray cells
   beyond cell 1 unexamined in this revision.
8. A-post ontology: empty screw well vs non-screw recess vs molding
   detail — indeterminate from pixels; affects only the E3b label
   (engagement-without-removal either way).

## 8. Gate assessment (mission §5)

- "Six distinct positive screwdriver interactions": **5 exist.**
  The 6th was not manufactured: the only other screw removals in the
  footage are finger-only (PCB, N5). Gate verdict:
  `NOT_EVALUATED (as specified)` — the specified 4/6 gate cannot be
  evaluated because its 6-event precondition fails on this footage.
  Reference only (not a gate conversion): 4 of the 5 found engagements
  carry site points with cited frames; S2a is accepted as
  unlocalizable rather than guessed.
- "Zero falsely unique source claims on negative controls": **met.**
  No source, removal, or deposit claim is made on N1/N3/N4/N5/E3b;
  N4's origin stays explicitly unresolved at frame-evidence level.
- "Every accepted localization backed by real frames": **met.**
  All six points (S1, S2b, B, A, S5, cell 1) cite exact
  (camera, frame, PTS) readings; S2a is accepted as *unlocalizable*
  rather than guessed.

## 9. Artifacts

- `snes-02/sync-manifest.json` — acquisition + sync manifest
  (schema `snes02-sync-manifest-v1`,
  sha256 `586b0d1be2bc2005e1c7bef5ee49c828dc7bd2aa701d444e657be73f2075b586`).
- `snes-02/contact-sheets/` — 8 skim + 30 paired + 55 detail sheets
  with exact frame/PTS labels; index in `SHEETS.md`.
- `snes-02/work/` — extraction logs (`cases/*/​*-pairs.json`, 179
  pairs, 0 residual failures), PTS dumps, sync/static scripts,
  remaining work crops. Intermediates, not deliverables.
- WITHDRAWN pending owner contract decision:
  `snes-02/observation-contract-v1.md` (v1a, superseded by
  `tools/retrospective-custody/matcher/SCHEMA.md` proposal),
  `snes-02/observations/snes02-observations.v1a-WITHDRAWN.json`
  (41 events, A-format, do-not-consume). Eval annotations likewise
  paused (they key on observation event ids).

## 10. Verdict rationale

PASS, scoped. The footage supports: overhead tray ledger (5/5
destinations), oblique engagement localization (4/5 sites), four
tool-transported removals plus one loosening, five clean negatives,
and three deductively closed count chains — each claim tied to exact
frames. It does not support: a 6th screwdriver engagement (absent),
S2a localization (occluded), direct rotation observation, or any
single-view custody event. A retrospective matcher consuming future
canonical observations should expect AMBIGUOUS/UNKNOWN as the common
case wherever a transit window meets a hand — which on this footage
is nearly everywhere the action is. That is the finding, not a
failure of analysis.
