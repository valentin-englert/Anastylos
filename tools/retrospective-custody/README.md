# Retrospective custody experiment

This is an offline, advisory origin-localization experiment. It reads a video
clip and a cached `sam-evidence-v1` screwdriver track, verifies the clip and
mask hashes, derives a narrow-terminal geometry candidate from each mask, and
anchors each trace record to the clip's decoded frame index and PTS.

The optional `--source-roi` is a fixed operator setup rectangle in decoded
display pixels (`left,top,right,bottom`). It describes the work area only; it
does not supply contact events, event times, source labels, or deposit windows.
The geometry can nominate a narrow mask end inside that area, but that end is
not a verified screwdriver tip. The current A3 mask can cover only the handle,
so the runner marks physical contact unknown.

The current video runner does not detect source-object presence/absence or
destination deposits. It keeps source change as
`SOURCE_CHANGE_UNVERIFIED`, marks those capabilities `NOT_IMPLEMENTED`, and
leaves their counts null. Retrospective matching is not implemented; the
runner makes no source-to-deposit claim and does not write to Assembly Memory.

## Run

Use the repository's existing `.venv` with OpenCV and NumPy. No dependency is
added. Run from the repository root and write evidence only to the task's
authorized `random/retrospective-custody-20261028/` directory:

```powershell
.venv\Scripts\python.exe tools\retrospective-custody\retrospective_custody.py `
  --clip <clip.mp4> `
  --evidence <evidence.json> `
  --source-roi <left,top,right,bottom> `
  --out random\retrospective-custody-20261028
```

The output directory must be new or empty. At most 12 crops around selected
mask narrow ends are written, alongside `trace.jsonl` and `report.json`. All
crop and trace hashes are recorded in the report. The clip, cached evidence,
transcript, and every referenced mask are hash-verified. The
`media.clip.src_sha256` value is preserved as sidecar provenance but is not
independently verified unless the original source video is supplied to a
separate verifier.

## Interpretation

- `narrow_end_in_source_area` means only that a cached mask had a sufficiently
  elongated, non-border-clipped shape and its narrow end fell inside the fixed
  work-area ROI. It does not mean contact or source localization.
- Mask rasters are mapped to their recorded boxes with nearest-neighbor
  resizing, matching the existing SAM renderer convention. Coarse masks remain
  coarse evidence.
- `sam_confidence` is retained as provider metadata; it is not a probability
  of tool identity, contact, removal, or physical correspondence.
- Frame indices and PTS are clip-local. This module does not map them to the
  full source video's timeline.
- Frame indices and PTS use the decoded clip only. The sidecar `-ss` value is
  not used to map these records to the original video timeline.

## Test

```powershell
cd tools\retrospective-custody
..\..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
