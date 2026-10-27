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
  `region`, plus `result_sha256`: SHA-256 over the result file's bytes,
  computed by the importer at import time — null when the file is not
  readable — never read from inside the result file itself).
- `tray_link` — an observation placed in a tray cell. The tray snapshot
  (`sheet_id`, `number`, `rect_frame_mm`) is resolved from
  `calibration/calibration-config.json` at record time. Every link carries
  either an evidence basis or a human confirmation; anything else is rejected
  and nothing is written.

"Where does this go?" returns the part's ORIGINAL source location with its
evidence. The tray only locates the stored part and resolves the removal
observation behind it.

```powershell
py -3.12 assembly-memory\memory.py --session-dir sessions\day1 import --result-json results\monday\screw_01\result.json
py -3.12 assembly-memory\memory.py --session-dir sessions\day1 record tray-link --observation obs-0001 --sheet surface_a3 --tray 3 --confirmed-by operator
py -3.12 assembly-memory\memory.py --session-dir sessions\day1 where --observation obs-0001
py -3.12 assembly-memory\memory.py --session-dir sessions\day1 where --tray 3 --sheet surface_a3
```

`import` reads one change-detection `result.json` verbatim (view, regions,
provenance) and appends one observation per region (`--region R1` imports a
single region). Results without trustworthy coordinates
(`registration_invalid`, `view_mismatch`, `input_error`) are refused, as is
any result without a `rectified.view`. `--kit` optionally records the kit
config's bytes hash as `kit_sha256`. Manual `record observation` remains
for observations without a result file. These are the only producer paths
into the log: no recognition reader writes here (see
[`ARCHITECTURE.md`](../ARCHITECTURE.md)).

Query answers are JSON on stdout. `where --tray` reports `located` for one
candidate, `ambiguous` with every candidate's source location when a tray
holds indistinguishable parts, and `unknown` when nothing is linked. An
observation without a tray still answers with its source location
(`unassigned`). A malformed log aborts loudly with the line number; the
reader never guesses.

## Test

```powershell
cd assembly-memory
..\.venv\Scripts\python -m unittest discover -s tests -v
```

The unit suite (`test_memory.py`) is stdlib-only and runs on any Python 3.12.
The integration suite (`test_integration.py`) drives the synthetic harness,
so it needs the calibration virtualenv.
