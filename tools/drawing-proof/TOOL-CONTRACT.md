# Drawing tools for Muse — contract draft `anastylos-drawing-tools@0.1`

Muse orchestrates; it never writes code, thresholds or geometry. Every tool is deterministic or a recorded
model call, every input is validated, every output carries hashes. A failed gate ends in an operator question
or a capture request, never in retuning.

## Loop (per part, at most 3 segmentation attempts, then a capture request)

```
list_frame_candidates → [Muse] choose_frame → segment_part → (gates) → render_drawing
                                                      └─ fail → ask_operator | request_capture
```

## Tools

| Tool | Input | Output | Who decides |
|---|---|---|---|
| `list_frame_candidates` | `source` (video + sha256, or still), `stride_s` (0.5), `max` (≤ 6) | candidates: `id`, frame index/t, rectified thumbnail path + sha256, `registration_ok`, `sharpness`, `motion_to_neighbours`, `skin_fraction_work_area` | deterministic ranking (registration ok, low motion, low skin, high sharpness) |
| `choose_frame` (Muse output, strict schema) | candidate list + `need` (`outer_boundary`, `view_side`, `detail`) | `candidate_id` ∈ list, `purpose` ∈ need enum, `region_mm` [x,y,w,h] inside the page, `part_phrase` (≤ 6 words, for a concept prompt), `reason` (≤ 30 words) | Muse; validator rejects ids not offered, regions off the page, or a phrase with numbers |
| `segment_part` | `candidate_id`, `region_mm`, `method` ∈ `classical_r1`, `sam_box`, `sam_concept` | `mask_sha256`, gates (`quality.GATES`) with `flags`, overlay path | method choice by Muse from the enum; parameters fixed per revision |
| `render_drawing` | `mask_sha256` with status `ready_for_operator_review` | SVG + report path and sha256 | deterministic; refuses any other status |
| `ask_operator` | `kind` ∈ `confirm_outline`, `confirm_view_side`, `mark_region` + overlay path | operator answer (typed or screen) | operator; only a confirmation makes an outline "confirmed" |
| `request_capture` | flags + binding | `anastylos-capture-gap-input@1` for `tools/agent/capture_gaps` (kind `outer_boundary`, `missing_side`, `orientation`) | deterministic mapping from flags |

## Failure states

`REGISTRATION_FAILED`, `NO_PART_IN_REGION`, `INCOMPLETE_VIEW`, `HAND_IN_MASK`, `LOW_EDGE_SUPPORT`,
`SHADOW_DOMINANT`, `PROVIDER_ERROR` (SAM/Muse call failed; falls back to `classical_r1`), `BUDGET_EXHAUSTED`,
`ATTEMPTS_EXHAUSTED` (3 attempts → `request_capture`). Known blind spot (r1): background seen through an
opening of the part is not detected by any gate (transfer T3); until fixed, `confirm_outline` is mandatory.

## Not allowed

Free text from Muse as geometry; thresholds or code from Muse; a drawing from a mask that failed a gate;
binding a drawing to a memory record without operator confirmation; video segmentation.
