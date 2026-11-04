# MIC-01..MIC-05 workflows and evaluation

| id | workflow | readers | scoring |
|---|---|---|---|
| MIC-01 | printed SMD resistor code: decode or abstain | smd | top code == visible marking -> correct; abstention expected on unmarked |
| MIC-02 | PCB silkscreen designators | pcb_ocr | set of designators: correct / partial / miss, false positives listed |
| MIC-03 | semiconductor markings, no invented part numbers | markings | top line transcription == visible marking; never a verified spec |
| MIC-04 | fingerprint enrollment + retrieval incl. look-alike negatives | fingerprints | top gallery id; negatives must not be `ok` |
| MIC-05 | unknown / unreadable / unfocused must not yield specs | any (auto) | `candidate` -> false_confident, else correct_abstention |

`unavailable` / `error` items are counted separately, never as abstentions.

## Dataset (`anastylos-microscopy-dataset/1`)

`schemas/microscopy-dataset.v1.schema.json`. Each item: id, relative image
path, sha256, `split` (`dev` | `heldout`), workflow, mode/readers/options,
expected ground truth. `provenance.kind` is one of `synthetic`, `sample`,
`real_microscope`; `is_msi_gtx970_capture` may be true only for real frames
of the board.

## Held-out discipline

1. Develop on `dev` only.
2. `eval --print-fingerprint` -> harness fingerprint (Core code + reader code
   hashes + version). Any code change changes it.
3. `eval --split heldout --frozen-config <fingerprint>`; mismatching or
   missing fingerprint is refused. Report records `heldout_access: true`.

Reports always carry `hardware_validation: "not_claimed"`. A real GTX 970
set yields `gtx970_validation: "real_images_scored_pending_operator_review"`,
never "validated".

## Creating the real GTX 970 set (to do)

Photograph the board with the MUSTOOL chain at a fixed profile, write a
capture manifest, choose ROIs, have the operator record ground truth (what is
visibly printed, not what a datasheet says), split 70/30 dev/held-out before
looking at results.
