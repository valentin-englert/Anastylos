# FULL-01 — exploratory full disassembly (development data)

Label: **FULL-01 exploratory / development data.** Not E3-A, not
independent validation, not scored. Purpose: learn what the current
pipeline can and cannot represent across a complete controller
disassembly, before the A3 mat is replaced. E3-A stays preregistered and
untouched ([`PROTO-01-E3A.md`](PROTO-01-E3A.md)).

## 1. Setup (unchanged)

Current `surface_a3` sheet on MDF, nail fixture, overhead phone in Pro
video (ISO 400, 1/50 s, 5600 K, MF 0.6, UHD 30), witness phone running
continuously. Camera and sheet do not move between phases; if either
moves, record a new 10 s static before continuing and log it.

## 2. Phases, files and baselines

One overhead file per phase in `C:\ANASTYLOS\incoming\`. Every file starts
with **≥ 10 s static, hands out** (baseline of that pose) and ends with
**≥ 5 s static**. Hands may be bare; tweezers optional (log when used).
After each removal or deposit: hands out of view for ~2 s. No speaking.

| File | Pose | Content |
|---|---|---|
| `FULL-01-P1.mp4` | controller back up in the nails | remove the back-shell screws one at a time, each into its own empty cell |
| `FULL-01-P2.mp4` | transition | lift the controller out, open the shell, take out the PCB (with cable) and the back shell; parts that do not fit a cell go to a fixed place **outside** the sheet; place the front shell interior up in the nails; end with 10 s static (this is the P3 baseline pose) |
| `FULL-01-P3.mp4` | front shell interior up | remove the remaining parts one at a time: face buttons, D-pad, Start/Select, membranes, shoulder parts; a part goes into an empty cell only if it lies fully inside it; otherwise to its fixed outside place |

Cell use (owner, 2026-10-29): twelve cells are not enough for one part
per cell, so **all back-shell screws share one cell** and **the two
identical white rubber nubs** (between the PCB sensor and the L/R buttons)
**share one cell**; every other part that fits gets its own cell, and a
cell is never reused for a different part type. Parts that do not fit go
outside the sheet, each at its own labelled place (paper slips `OUT-A`,
`OUT-B`, …). Expected consequence: only the first deposit into a shared
cell can be linked; later ones are deposits into an occupied cell and stay
`ambiguous` by the linker's rule, and identical parts in one cell are a
compatible group, never individual identities.

## 3. Inventory policy

- No operator-named source inventory is given to the pipeline. Sources
  come from adaptive discovery (`custody.py discover`) per phase.
- For comparison only, the Technical Lead may afterwards build an
  operator-defined inventory from a labelled still of each pose; such
  runs are labelled "operator inventory".
- P2 is a pose transition: no source tracking is claimed across it. The
  P1 device pose ends when the controller is lifted; P3 starts a new
  pose from its own baseline.
- Parts stored outside the sheet are never linked automatically. They
  enter Assembly Memory only as operator-declared observations
  (`declared_by`), clearly marked.

## 4. Ground-truth log (owner)

From the witness video, after recording, one CSV per phase
(`FULL-01-P1.log.csv`, …), kept on the stick until the Technical Lead
has hashed the processing outputs:

```text
step,video_time_mm:ss,part (own words),from,to,tool,notes
1,00:14,screw,top-left hole,cell-1,screwdriver,
2,01:02,PCB with cable,device,OUT-A,hands,cable stays attached
```

`from` and `to` in the owner's own words or as `cell-N` / `OUT-x`. Send
only each file's SHA-256 (`Get-FileHash … -Algorithm SHA256`); the
Technical Lead commits the hashes before looking at any log.

Also log, in the same files under `notes`: every intervention (pushing
the device back, re-seating a nail, a part dropped, tweezers used, a
part moved twice).

## 5. Processing (Technical Lead)

Per phase, with frozen code only (no tuning on FULL-01): observer v1.3
with `custody-config.paper.json` (worktree `Anastylos-custody-v13`):
`discover` → `observe --inventory <discovered>` → `link`. Outputs to
`C:\ANASTYLOS\data\full-01\<phase>\`, hashed and committed before the
logs are opened. Then a descriptive report, no pass/fail:

- per part: source found? deposit seen? link and status; or why not
  (unobservable source, part outside, pose change, too small, low
  contrast);
- phase transitions and what the pipeline did at each;
- manual interventions and declared parts;
- integration problems for memory, guide and the next mat.

## 6. Expected limits (stated before recording)

Screw removals are likely not observable from overhead (pre-flight B).
Parts larger than 26 mm, the PCB with cable and the shell halves cannot
be deposited in cells and will only appear as operator-declared. Moving
the device from P1 to P3 ends every source location of P1.
