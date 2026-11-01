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
