# Drawing proof — transfer and SAM single-image results (2026-11-01)

All plans were committed before their runs: `transfer_plan_r1.json` (14bf04cd), `sam_plan_r2.json` (d408397d),
`sam_plan_r3.json` (4dc8b9b4). Outputs: `C:\tmp\anastylos-drawing-proof\` (not in the repo).

## Frozen classical baseline r1 on untuned inputs

| Target | Gates | Visual check |
|---|---|---|
| T1 P3 f240, T2 P3 f360 (same part, nearby frames) | pass | outline correct; weak transfer evidence (same scene) |
| T3 aftermath still, empty front shell | `HAND_IN_MASK` (2.02 %), `LOW_EDGE_SUPPORT` | outline mostly correct, 301 mm² shadow left at the bottom; **paper seen through the button openings drawn as light regions; no gate catches this** |
| T4 D-pad membrane on tray cell lines | `INCOMPLETE_VIEW`, `HAND_IN_MASK`, `LOW_EDGE_SUPPORT` | wrong (printed lines); correctly refused |
| T5 shoulder piece on tray cell lines | `INCOMPLETE_VIEW` | wrong (printed lines); correctly refused |

## SAM 3.1 single images (`sam-tracking` adapter, unchanged)

- r2, box = whole region inset 2 %: SAM segments the region itself (6/6). Concept phrases: no mask on 5/6.
  Prompt design failure; no SAM verdict.
- r3, Muse (`muse-spark-1.3-contributor`, effort `max`, image input) returns a tight box for the named part,
  SAM segments that box. Muse 6/6 valid boxes (10.6–26.1 s, 1089–2587 output tokens), SAM 6/6 masks
  (1.9–3.2 s, est. USD 0.0025 each). No manual step.
- The first r3 scoring was wrong: SAM image masks are rasters of the returned object box
  (`records[].box`), not of the whole image; `score_mask.place_mask` now places them in that box.

| Target | Muse box + SAM gates | Visual check | IoU with classical r1 |
|---|---|---|---|
| F0 P3 f300 (tuned frame) | pass | correct, no shadow | 0.9932 |
| T1, T2 | pass | correct | 0.9933, 0.9930 |
| T3 aftermath | `LOW_EDGE_SUPPORT` (false alarm) | correct, no shadow, openings not filled with paper regions | 0.9617 (classical +301 mm² shadow) |
| T4 D-pad membrane | `HAND_IN_MASK`, `LOW_EDGE_SUPPORT` (both false alarms: beige rubber, soft edge) | correct | — (classical wrong) |
| T5 shoulder piece | pass | correct | — (classical wrong) |

## What follows

- Muse box + SAM is the better general segmenter on these inputs: no shadow rule, no printed-line failure,
  no per-part threshold. Classical r1 stays the offline fallback (`PROVIDER_ERROR`).
- Gates r1 have false alarms (skin rule on beige parts, edge support on soft edges) and one blind spot
  (background through openings). Gates r2 must be revised and frozen before the next comparison.
- Still unproven: correctness against an independent reference. Owner reference masks for F0, T3, T4, T5 on
  the same `rectified.png` are needed; `quality.reference_comparison` scores them.
- Not yet built: `list_frame_candidates` (quality pre-sort) and Muse frame choice; here the frames were given.

## Frozen tool r4 on the untouched test set (`specs/testset_r4.json`, committed a0e49d1f before the run)

Muse box → SAM, no gates, no manual step, all nine images reported. Visual assessment by the developer (no
reference masks; a model mask would be a comparison proposal, not ground truth). Outer silhouette and openings
are judged separately.

| Image | Outer silhouette | Openings | Muse / SAM wall s |
|---|---|---|---|
| own P1 f300 (assembled controller, back up, cable) | correct, cable excluded | none expected, none found | 99.6 / 10.0 |
| own P2 f150 (same, other phase) | correct | none | 38.3 / 9.7 |
| own P3 f1500 (front shell with buttons) | correct | 2 found: part of the D-pad cross and a fleck on a button; incomplete | 29.1 / 10.4 |
| Arduino Mega on wood, oblique | board correct on three sides; **cast shadow included at the lower left**; Muse `fully_visible=false` (board cut by the image edge) is right | none | 33.5 / 9.9 |
| Adafruit FLORA held in fingers, black ground | correct, fingers excluded | **2 false openings inside the USB connector** | 60.6 / 5.0 |
| Bicycle sprocket on white | correct incl. teeth | 10 cut-outs and the spline bore correct; small marks at teeth questionable | 25.3 / 12.2 |
| Single screw on concrete | correct | none | 19.4 / 2.8 |
| Keycap on white | correct | none | 11.8 / 2.7 |
| Ball bearing | correct | bore found | 13.3 / 8.3 |

Outer silhouette: 8/9 without visible error, 1 with an included shadow. Openings: correct on sprocket and
bearing; on the empty FULL-01 shell (development image T3) all 7 through-openings are found but their rims
are ragged and too small (the mask shows paper seen through the hole, not the hole rim); false openings on the
FLORA connector. A good silhouette is not a complete technical drawing: inner ribs, bosses, membranes and
the rubber pad are not part of a SAM part mask at all.

`sam_drawing.py` turns a SAM mask on a rectified image into an SVG with outline and openings as separate
geometry (dev images F0 and T3).

## Demonstrator batch B1 (frame choice variant + frozen r4/r4.1 box→SAM), 2026-11-01

Specs `specs/batch_b1/` committed before the run (90b50c08). One attempt per job planned; J2 needed three
because of two own bugs, all attempts kept under `C:\tmp\anastylos-drawing-proof\batch-b1\`.

| Job | Chosen frame | Outcome (visual) | Stage seconds (sample / rectify / choose / box / SAM / svg) | Total s |
|---|---|---|---|---|
| J1 back shell (P2 0–40 s) | 0.5 s | **wrong object**: the assembled controller lying back-up, not the separate back shell; Muse called it the back shell. Top view cannot tell them apart. | 88.9 / 3.9 / 10.5 / 37.8 / 7.5 / 0.1 | 148.8 |
| J2 PCB with cable (P2 55–80 s) | 64.5 s | **wrong**: Muse box `fully_visible=false` (cable leaves the image), SAM mask covers half the board, 10 junk opening candidates; the job drew anyway (design bug, fixed: now `NEEDS_CAPTURE`, no SVG) | 35.9 / 4.1 / 11.0 / 17.6 / 7.9 / 0.1 | 76.5 |
| J3 front shell with membranes (P3 0–20 s) | 0.5 s | correct outline | 51.3 / 2.4 / 7.3 / 13.5 / 8.5 / 0.1 | 83.2 |

J2 attempts: 1 crashed silently (sheet reader `SystemExit` on one candidate, progress stuck at `running`;
fixed: candidate skipped and logged, any exit writes `failed`); 2 SAM refused the box locally (rescaled box one
pixel past the image; fixed as r4.1 clamp); 3 ran.

API use B1: Muse 8 calls (0 failed), SAM 3 calls (0 failed) + 1 local refusal before sending. Latency 77–149 s
per job, sampling the video dominates (36–89 s); Muse 18–48 s per job; SAM 7.5–8.5 s. Not live: the job runs
in the background and writes `progress.json` per stage.

Consequences: every drawing is `drawn_unconfirmed` until the operator confirms frame identity; a Muse
`fully_visible=false` stops the job with a capture request. The back-shell and PCB drawings for the explosion
still need the two hand-free captures; no explosion is drawn.

## Milestone: request a part → check → draw or request a capture → overlay → operator binding (2026-11-01)

Session-UI section "Part drawings" (`tools/session-ui/drawings.py`, routes `/api/drawings`, `/api/drawing/*`) over
the background job. Demo workspace `C:\ANASTYLOS\data\drawing-ui-demo\full01` (FULL-01 copy), specs back-shell,
pcb-cable, front-shell. Run through the real server API on 2026-11-01:

| Check | Result |
|---|---|
| J1 back shell (P2 0–40 s) | `NEEDS_CAPTURE`: Muse sees `assembled_object`, no usable tile; no drawing |
| J2 PCB with cable (P2 55–80 s) | `NEEDS_CAPTURE`: Muse `unclear`, no usable tile; no drawing (synthetic J2 regression in tests: half mask + box at image edge → `NEEDS_CAPTURE`) |
| J3 front shell (P3 0–20 s) | `NEEDS_CONFIRMATION` (frame 0.5 s precedes the first observed change at 13.8 s); SVG and overlay shown; binding buttons offered |
| Double start of the same spec | second request returns the running job |
| Worker killed with `taskkill` during the Muse call | status `failed`: "worker process ended without a final state" |
| Repeated J3 | candidate index cache hit: sampling 0.01 s (was 48 s), total 34 s (was 86 s) |

Latency per job: first run 86–127 s (sampling 48–84 s, Muse choice 14–47 s, Muse box 12 s, SAM 7–9 s); cached
34 s. API use for this milestone: Muse 7 calls (one interrupted by the kill test), SAM 2 calls, 0 failures.
Not yet checked: a browser click on the binding buttons (the server path is covered by `test_drawings.py`).

## EV-1 and MF-1 (plan `specs/plan_ev1_mf1.json`, committed 755df53b before the runs), 2026-11-01

**EV-1, before/after footprint without a model.** The quiet-frame rule (motion and skin both ≤ 25th percentile)
found only two quiet frames per video, so each video gave one pair spanning many steps.
- P3 13.5 → 48.5 s: the changed region outlines the removed ABXY membrane roughly (ragged rim, no shadow); the
  D-pad membrane removed in the same interval is **missed** (white membrane over paper seen through the white
  cross opening: almost no difference).
- P2 13.0 → 70.0 s: unusable; the pair spans opening and flipping the device, the largest region is shadow and
  cable.
- Finding: the footprint works when the object stays put and the part contrasts with what lies below; it needs
  short pairs around single steps. Revision candidate r2 (not run): quiet = low motion only with an absolute skin
  limit, pairs bounded by custody event windows.

**MF-1, multi-frame fusion of the held back shell (P2 12–24 s).** Invalid by construction: the six frames show
three different states (assembled controller 12–13 s, held controller 15–16 s, opened device 19–24 s); the separated
back shell never lies in the work area (it was put down outside). The fusion still "succeeded" (aligned IoU 0.91,
rim observed 89 %), because these states have nearly the same outline. Finding: outline similarity cannot tell
object states apart; fusion is only valid inside one confirmed state, so it needs an identity gate first
(event window or operator).

API use: Muse 6, SAM 6 (MF-1), 0 failures. EV-1 used no model.

## EFF-1: reasoning effort high vs max for the part box (plan `specs/plan_eff1.json`, committed 2e576acd before the run), 2026-11-01

Date correction (audit 2026-11-01): the plan file and this section were first dated 2026-11-02 by mistake; the
run took place on 2026-11-01. The plan file stays unchanged as committed.

6 dev images × {max, high} × 2 repeats, interleaved; script `effort_experiment.py`, raw output
`C:\tmp\anastylos-drawing-proof\eff1\eff1.json`.

| arm | valid | median latency | median output tokens | median IoU vs reference | median repeat IoU |
|---|---|---|---|---|---|
| max (r4.1) | 12/12 | 28.0 s | 2145 | 0.926 | 0.981 |
| high | 12/12 | 19.4 s | 1428 | 0.914 | 0.966 |

Pre-registered acceptance (valid 12/12; IoU within 0.03 of max: −0.012; consistency not lower by more than 0.05:
−0.015; latency lower: −31 %) is met → **adopted as r4.2** for the part box only. The frame-choice call was not
measured and stays at max (`drawing_job.CHOICE_EFFORT`). Caveats: the reference box is derived from max-arm
results, so it favours max; on T5 (shoulder piece) high returned a box about 8 % too tall in both repeats (IoU 0.91
vs 0.98) — SAM segments inside the box, so this loosens the prompt rather than cutting the part, but it is the first
thing to watch. Latency spread is wide in both arms (max 12.6–54.2 s, high 17.2–33.8 s); high mainly removes the
long tail.

Prompt cache probe (F0, max, `prompt_cache_key`): the key is accepted; calls 2 and 3 reported 790 of 815 input
tokens cached. Latency 31.1 / 28.9 / 19.9 s — not attributable to the cache, because input is tiny and reasoning
dominates. Not adopted (no measurable benefit).

API use: Muse 27, 0 failures, 0 retries.


## Audit note 2026-11-01

Paused/resume with checkpoints is covered by a unit test only (`tests/test_drawing_job.py` ResumeTest); resuming a
real interrupted job has not been shown yet. Do not claim it as demonstrated.


## CONS-1: Muse run-to-run consistency of frame choice and part box (plan `specs/plan_cons1.json`, committed 02721b66 before any request; addendum A1 in 8e4c930b), 2026-11-01

Question: how stable are the two Muse decisions the drawing job relies on when the identical request is repeated?
Script `consistency_experiment.py` (reuses `drawing_job._muse_choice` at effort max and `muse_sam.muse_box` r4.2 at
effort high, defaults untouched), raw output `C:\tmp\anastylos-drawing-proof\cons1\` (`cons1.json`, `cons1.log`,
`sheet_J*.jpg`, `overlay_*.jpg`). Run once, sequential. Not a ground-truth test: it measures self-consistency only.

Deviation from the plan (addendum A1): the first start failed locally (`KeyError 'view_side'`: the B1 specs predate the
current `CHOICE_PROMPT` fields) before any request was sent (30 local errors, 0 requests, log kept as
`cons1-attempt0-local-error.log`). Sheets stay the B1 sheets (rebuilt from the candidate/tile caches without video
decoding; mean pixel difference to the saved B1 sheets 0.67–0.93; J2 has 4 tiles because frame 1845 was skipped in B1
too); the request fields (part, scope, view side, attachments, phase events) come from the current product specs, so J3
asks for scope `assembly`, not `part` as in B1.

### Development data (all inputs were seen while the pipeline was built; no independent test set)

**Frame choice, 5 repeats per job, effort max** (gate = what the pipeline does with the answer):

| job | usable | gate outcome (modal) | tile | state | view class | latency | level |
|---|---|---|---|---|---|---|---|
| J1 back shell (5 tiles) | false 5/5 | NEEDS_CAPTURE 5/5 | none | assembled_object 3, unclear 2 | other 5/5 | 31–42 s | **L2** (state 3/5) |
| J2 PCB + cable (4 tiles) | true 2, false 3 | NEEDS_CAPTURE 3, ok(tile 2) 2 → tuple 3/5 | tile 2 in 2 of 5 | assembly 4, unclear 1 | other 4, back 1 | 32–71 s | **L2** (tuple 3/5) |
| J3 front shell (3 tiles) | true 5/5 | NEEDS_CONFIRMATION 5/5 | tile 1 5/5 | assembly 5/5 | other 5/5 | 16–23 s | **L0** |

Overall frame-choice verdict by the pre-registered rule (worst unit): **L2, operator confirmation**. Raw `view_side`
strings: J1 "exterior" 5/5, J3 "interior" 5/5, J2 "solder side" in 4 spellings and "component side" once (the opposite
side, in the repeat that picked tile 2). My `view_class` mapping mostly yields "other" because the model repeats the
request's own words, so it carries little signal (reported only, as planned).

**Part box, 3 repeats per image, r4.2 effort high** (all 18 calls valid, `fully_visible` true in all 18):

| image | min pairwise IoU | median | edge spread px (l,t,r,b) | median latency | level |
|---|---|---|---|---|---|
| F0 front p3 f300 | 0.957 | 0.959 | 19, 4, 7, 17 | 23.9 s | L0 |
| T1 front p3 f240 | 0.902 | 0.911 | 5, 2, 17, 58 | 17.2 s | L0 |
| T2 front p3 f360 | **0.899** | 0.903 | 2, 0, 72, 41 | 19.8 s | **L1** (0.001 under the L0 line) |
| T3 front empty aftermath | 0.908 | 0.933 | 7, 4, 38, 44 | 21.4 s | L0 |
| T4 D-pad membrane aftermath | 0.931 | 0.953 | 3, 2, 27, 2 | 15.6 s | L0 |
| T5 shoulder piece | 0.967 | 0.969 | 5, 3, 3, 3 | 19.3 s | L0 |

Overall box verdict: **L1, vote of 3** (driven by T2 alone; 5 of 6 images are L0).

API use: Muse 33 calls (15 frame choice, 18 box), 0 retries, 0 failures, 0 SAM; budget cap 40 not reached. Latency
over all calls: frame choice median 32.4 s (15.6–71.2 s), box median 19.3 s (14.3–25.0 s), box output median 1726 tokens
(frame choice returns no usage in `_muse_choice`).

Prediction written before the run: partly wrong. J1 did have the lowest *state* agreement, but its gate was the most
stable (always NEEDS_CAPTURE); the unstable gate was J2. Box median IoU ≥ 0.95 held for 3 of 6 images only
(0.903–0.969) and T5, the one flagged as risky, was the tightest.

### Visual review (my own reading of the saved sheets and overlays; it changes no verdict)

- T2: the three boxes agree on left/top; one repeat (repeat 3, right edge x = 1596 vs 1654/1668) ends about 4 % short
  of the shell's right end, so that box would cut the part. The other two include it. The spread is a real error in
  one repeat, not only loose edges; SAM would segment inside the clipped box and lose the right end of the shell.
- J2: tile 2 is a clean, hand-free frame of the PCB with its cable lying in the front shell. The request says
  "excluded: shells" but from above the PCB sits inside the shell, so `usable` false and true are both defensible: the
  disagreement mirrors an ambiguous request more than random noise. Tile 4 (PCB entering at the bottom edge, shell
  below) and tiles 1/3 (hands) are not candidates.
- J1: the model never found a usable tile and called the frame either `assembled_object` or `unclear` ("front shell may
  still be attached underneath"); the product conclusion (no usable back-shell frame in 0–40 s, capture needed) is
  stable even though the label is not.

### Independent scoring

None. There is no ground truth and no held-out set here; IoU between repeats says nothing about correctness, and a
consistently wrong answer would count as consistent. n is small (5 or 3 per unit): L0 means "no inconsistency seen",
not "high agreement proven" (5/5 only bounds the single-call agreement above ≈ 0.55 at 95 %).

### Decision by the pre-registered rule

- **Frame choice: L2.** Voting is not the answer: J2's split is 3/2, where a majority of 3 is barely better than one
  call (plan: voting pays only from a modal share of 4/5). `usable`/`state` must not be an automatic gate on their own:
  a single call on J2 would have stopped the job (NEEDS_CAPTURE, 3 of 5) or let it draw (2 of 5).
- **Part box: L1.** A majority of 3 is worth its cost for the box; one call is mostly fine (17 of 18 pairwise IoUs
  are ≥ 0.90) but can clip the part (T2 repeat 3).

### Proposals for the TL (nothing changed in the pipeline)

1. Box vote, in `drawing_job.run` stage `muse_box` (or a `muse_box_vote` next to `muse_sam.muse_box`, default 1 → 3):
   three calls in parallel threads, box = coordinate-wise median of the valid boxes (at least 2 valid, else
   NEEDS_CAPTURE as today), `fully_visible` by majority; store all three answers in the `box` checkpoint. Retrospective
   check on this development data: the median box of T2 is [176, 118, 1654, 796], i.e. the clipped repeat is outvoted.
   Cost: +2 Muse calls and about 3.4k more output tokens per job; latency about +5 s when parallel (median of the
   per-image maximum of three 24.0 s vs single median 19.3 s), +40 s if sequential. Voting should be re-measured
   after adoption, as EFF-1 was.
2. Frame choice: no voting. Treat "Muse found no usable tile" as a soft stop: keep NEEDS_CAPTURE but show the contact
   sheet and let the operator pick or reject a tile, and display `state` as Muse's opinion, not as a gate. A job that
   flips between "no usable tile" and "tile 2" (J2) should always reach the operator. Cost: no extra API calls.
3. A frame-choice re-run with a request that matches the footage (for J2, "excluded: shells" cannot be true from the
   top) would separate request ambiguity from model noise; not done here (outside CONS-1's budget).

Caveats: single run, one day, one provider revision; inputs are development data; sheets are B1 sheets with the
current product request fields (A1); `view_class` is uninformative with these requests; the L0/L1 line for T2
(0.8986 vs 0.90) is a fixed rule, not a finding of a sharp boundary.
