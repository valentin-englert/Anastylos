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
