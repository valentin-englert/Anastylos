# ANASTYLOS assembly reconstruction

Read-only vertical slice: existing Assembly Memory → Assembly State v1
→ automatically generated 2D exploded-view SVG. Standard library only,
no virtualenv needed, offline.

This module is a **derived projection**, not a persistent memory
system: every output regenerates byte-identically from its inputs and
nothing is ever written back to the memory log, the graph, or any
evidence file. Shared contracts (`memory.jsonl`, detection
`result.json`, `assembly_graph.json`) are consumed verbatim and never
modified. Architecture source of truth stays in
[`ARCHITECTURE.md`](../ARCHITECTURE.md); the proposed state contract is
specified in [`ASSEMBLY_STATE_V1.md`](ASSEMBLY_STATE_V1.md).

## What it never claims

- No part names, counts, merges, or cross-observation identity: every
  card stays `identity: unknown` until an operator asserts identity
  elsewhere.
- Before/after crops are shown side by side, never interpreted as
  "this is the part".
- Candidate edges are unconfirmed proposals; only the graph's
  operator-confirmed edges render solid.
- Exploded positions are layout-only and carry no physical meaning;
  physical millimetres are verbatim paper-plane values, exact on the
  paper plane only (see `change-detection` limits).

## Run

One reproducible command produces both artifacts:

```powershell
# from the assembly-reconstruction directory
py -3.12 reconstruct.py build <memory.jsonl> [--results-dir <dir>] [--graph <assembly_graph.json>] [--embed-crops] --out <dir>
```

Output: `<dir>/assembly_state.json` + `<dir>/exploded.svg`, plus a JSON
summary on stdout with SHA-256 over both files. Exit 2 only when the
log path itself is missing; corrupt or empty logs still produce an
explicit `unavailable`/`unknown` state (exit 0), never a guess.
`--embed-crops` embeds each unique evidence image once as a data URI
so the SVG is self-contained (deterministic base64 of the file bytes);
without it, crops are linked with browser-safe relative references.

Synthetic demo (committed fixtures):

```powershell
py -3.12 reconstruct.py build fixtures\synthetic\memory.jsonl --results-dir fixtures\synthetic\results --graph fixtures\synthetic\assembly_graph.json --embed-crops --out demo_out
```

Real-hardware replay (read-only; from the original checkout, outputs
outside it):

```powershell
cd C:\Users\HP\Documents\Anastylos
py -3.12 ..\Anastylos-assembly\assembly-reconstruction\reconstruct.py build hardware-02\memory_test\memory.jsonl --graph <graph.json> --embed-crops --out C:\tmp\hw02_recon_embed
```

where `<graph.json>` comes from the read-only cable-routing graph
builder over the same log (stdlib-only `anastylos_cable.graph` +
`anastylos_memory`; no venv needed for that import path).

## Assembly State v1 (proposed)

Specified in [`ASSEMBLY_STATE_V1.md`](ASSEMBLY_STATE_V1.md): one entry
per live observation in log order (verbatim physical mm, cited region,
existing rectified crops, tray links with basis, deterministic
`layout_px` exploded coordinates), graph edges copied verbatim,
provenance hashes over input bytes, and explicit missing-evidence
records. No wall-clock timestamps: identical inputs, identical bytes.

## Terminology (read this before counting)

The SVG is an **observation review**, not a parts list: it shows one
card per memory observation, and N observations are not N verified
parts (one screw cycle already yields several). Observation index
badges (`[1]`–`[N]`) link map boxes to their cards; a "same observed
coordinates" note marks observations with identical verbatim
coordinates — a location match only, never an identity claim. No
observation is merged, named, or promoted: identity stays unknown and
candidates stay candidates until an operator asserts otherwise through
the existing confirmation channels.

## SVG layout

- Header: status, memory SHA-256, observation count.
- Physical map per `source_view`: the labeled `before_rectified`
  frame as background where available (or an explicit note), verbatim
  mm boxes at a stated px/mm scale, centroid dots, same-frame tray
  cells, badge-linked observation ids.
- Observation stack at the projected `layout_px` coordinates: one card
  per observation with index badge, before/after crop thumbnails
  beside the card (or an explicit "crop unavailable" placeholder),
  evidence, tray/operator badges, and confirmed (solid) vs candidate
  (dashed) dependency lanes.
- Notes + provenance footer: reassembly proposal with its NOT-verified
  warning, missing evidence, cycles, contradictions, input hashes,
  embedded-image count.

## Tests

```powershell
# from the assembly-reconstruction directory
python -m unittest discover -s tests -v
```

52 tests: projector contract (order, verbatim geometry, unknown
identity, tray bases, verbatim relationships, provenance hashes,
determinism, read-only inputs), provenance verification
(match/mismatch/missing/malformed hashes, tampered-byte rejection),
edge cases (missing/malformed/empty
logs, ambiguous multi-links), renderer structure (valid XML, section
separation, badges, honest labels), linked crops (browser-safe
relative hrefs resolving to real files, underlay), embedded crops
(no external refs, byte-exact base64, determinism), same-location
cross-references, CLI (outputs, `--embed-crops`, reproducibility,
exit codes), and one live integration test driving the real graph
builder (test-only coupling, same precedent as
`assembly-memory/tests` → `change-detection`).

## Fixtures

`fixtures/synthetic/`: hand-built `memory.jsonl` (5 live + 1
superseded observation, human + evidence tray links, 1 missing
result file), minimal `results/*.json` following the real detection
schema, `confirmations.json`, deterministic generated PNGs, and
`assembly_graph.json` **generated by the real graph builder**:

```powershell
cd fixtures\synthetic
python -c "import sys, json; sys.path.insert(0, r'<repo>\cable-routing'); sys.path.insert(0, r'<repo>\assembly-memory'); from anastylos_cable.graph import build_graph; g = build_graph('memory.jsonl', confirmations_path='confirmations.json'); open('assembly_graph.json','w',encoding='utf-8').write(json.dumps(g, indent=2) + chr(10))"
```

Regenerate (don't hand-edit) the graph if the log changes.

## Validation scope

- Synthetic: full 52-test suite green; byte-reproducible.
- Real hardware: replayed read-only over `hardware-02` (4
  observations, 1 human tray link, BC/CD results): state `ok`,
  4/4 regions resolved, self-contained SVG with 4 embedded rectified
  frames (8 card clips + 1 map underlay, zero external refs),
  1 candidate edge, 0 confirmed, byte-identical across runs.
  This replays one screw cycle on still photos — it is not
  live-video tracking, not a SNES controller, and not identity
  verification. No video, CAD, or Muse work was done in this lane.

## Known limitations

- Single-column wrap at 10 rows per column; large logs produce tall
  SVGs with no pagination.
- Crop embedding needs `output_files` names + `bbox_px` + rectified
  files on disk; anything else degrades to a labeled placeholder.
- Without `--embed-crops`, SVG `<image>` hrefs are relative file
  paths, so the SVG must travel with (or stay relative to) its
  evidence images.
- No multi-view physical alignment: front and back share no
  transform (per `ARCHITECTURE.md`), so each view gets its own map.
