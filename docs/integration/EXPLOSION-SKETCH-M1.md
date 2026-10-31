# Integration note — explosion sketch M1 (assembly-reconstruction)

Suggested one-line status for [`ARCHITECTURE.md`](../../ARCHITECTURE.md)
(do not apply in parallel; leave for the integration owner):

| `assembly-reconstruction/` | read-only Assembly Memory → Assembly State v1 → SVG; controller schematic sheet | `reconstruct.py build` / `schematic` | branch `feat/explosion-sketch-m1` | projector 1.1.0; `schematic` emits assembled+exploded A3 from a package; 69 tests; FULL-01 three-assembly draft outside repo |

Additive contract fields (`kind`, `declaration`, `storage`/`storages`,
`compatible_groups`, `representation`, `phases`) are documented in
`assembly-reconstruction/ASSEMBLY_STATE_V1.md`. Schema version stays
`assembly-state-v1`. Controller schematic package schema:
`controller-schematic-v1` (derived, not memory). No SAM handoff. No
changes to E3-B frozen files, `tools/agent/**`, `tools/live/**`,
`tools/guide/**`, or active memories.
