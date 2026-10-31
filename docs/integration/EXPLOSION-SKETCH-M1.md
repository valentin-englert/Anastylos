# Integration note — explosion sketch M1 (assembly-reconstruction)

Suggested one-line status for [`ARCHITECTURE.md`](../../ARCHITECTURE.md)
(do not apply in parallel; leave for the integration owner):

| `assembly-reconstruction/` | read-only Assembly Memory → Assembly State v1 → technical explosion SVG | `reconstruct.py` | branch `feat/explosion-sketch-m1` | projector 1.1.0 projects declared removals + storage/groups; mask contours from change-detection `mask.png`; 61+ tests; real FULL-01 replay outside repo |

Additive contract fields (`kind`, `declaration`, `storage`/`storages`,
`compatible_groups`, `representation`, `phases`) are documented in
`assembly-reconstruction/ASSEMBLY_STATE_V1.md`. Schema version stays
`assembly-state-v1`. No SAM handoff. No changes to E3-B frozen files,
`tools/agent/**`, `tools/live/**`, `tools/guide/**`, or active memories.
