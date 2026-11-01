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
