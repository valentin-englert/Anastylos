# ANASTYLOS assembly memory core

Append-only disassembly memory. Standard library only, no virtualenv needed.

Each session keeps `<session-dir>/memory.jsonl`: one JSON event per line.
Events are never edited or deleted; a correction appends a new event whose
`supersedes` names the earlier id. Millimetre coordinates are stored verbatim
and never transformed. Nothing here names parts: an observation records what
was seen and where, plus the evidence behind it.

Event kinds:

- `observation` — a removal seen in a source view: `source_view`, `bbox_mm`,
  `centroid_mm`, and an evidence reference (`result_json`, `pair_id`,
  `region`, plus the file's sha256 when it is on disk).
- `tray_link` — an observation placed in a tray cell. The tray snapshot
  (`sheet_id`, `number`, `rect_frame_mm`) is resolved from
  `calibration/calibration-config.json` at record time. Every link carries
  either an evidence basis or a human confirmation; anything else is rejected
  and nothing is written.

"Where does this go?" returns the part's ORIGINAL source location with its
evidence. The tray only locates the stored part and resolves the removal
observation behind it.

```powershell
py -3.12 assembly-memory\memory.py --session-dir sessions\day1 record observation --view fixture_back --bbox 52.0,40.5,6,6 --centroid 55.0,43.5 --result-json results\monday\screw_01\result.json --pair-id screw_01 --region R1
py -3.12 assembly-memory\memory.py --session-dir sessions\day1 record tray-link --observation obs-0001 --sheet surface_a3 --tray 3 --confirmed-by operator
py -3.12 assembly-memory\memory.py --session-dir sessions\day1 where --observation obs-0001
py -3.12 assembly-memory\memory.py --session-dir sessions\day1 where --tray 3 --sheet surface_a3
```

Query answers are JSON on stdout. `where --tray` reports `located` for one
candidate, `ambiguous` with every candidate's source location when a tray
holds indistinguishable parts, and `unknown` when nothing is linked. An
observation without a tray still answers with its source location
(`unassigned`). A malformed log aborts loudly with the line number; the
reader never guesses.

## Test

```powershell
cd assembly-memory
py -3.12 -m unittest discover -s tests -v
```
