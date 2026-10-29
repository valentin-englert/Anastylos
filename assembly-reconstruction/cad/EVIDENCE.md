# CAD-ENGINE Wave-1 evidence report

Lane: 4. CAD-ENGINE. Worktree `C:/Users/HP/Documents/Anastylos-agentcam-cad`,
branch `feat/agentcam-cad`, base `9f4a496d74350f19012df577bc5278da66babcf7`.
Contract `ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663`
(13/13 file hashes verified OK against `CONTRACT.json`, 2026-10-29).
Python `C:\Users\HP\AppData\Local\Programs\Python\Python312\python.exe`,
3.12.10. Hardware: none attached -- physical gates PENDING_EVIDENCE.

## 1. Baseline (2026-10-29 12:58 CEST)

- `assembly-reconstruction/cad/` did not exist; no CAD suite, compiler,
  worker, or STEP artifact anywhere in the worktree.
- Existing suite, from `assembly-reconstruction/`:
  `python -m unittest discover -s tests` -- 54 tests OK.
- `cadquery` not importable in system Python (shared env clean).
- FreeCAD: no `FreeCAD`/`FreeCADCmd` on PATH, no `C:\Program Files\FreeCAD*`.
- pip index reachable; `cadquery 2.8.0` listed (candidate pin available).
- First measurable failing capability: no validated path from
  `cad-feature-model-v1` to a genuine STEP solid (nothing to run).

## 2. Decisive CadQuery Windows gate -- PASS (clock 12:59-13:02 CEST)

Binding 2 h backend clock started 12:59 CEST at isolated-venv creation;
smoke verdict PASS at 13:02 CEST. No fallback needed.

### 2.1 Environment (isolated worker only)

| Item | Record |
|---|---|
| venv | `assembly-reconstruction/cad/.venv` (gitignored), Python 3.12.10, pip 26.2.2 |
| install | `pip install "cadquery==2.8.0"`, PyPI, 2026-10-29 ~13:00 CEST, ~51 s |
| resolved | `cadquery 2.8.0`, `cadquery-ocp 7.9.3.1.1` (OCCT 7.9.3), 45-pin closure |
| freeze | `evidence/gate-freeze.txt` (45 pins incl. venv pip) |
| artifact hashes | `evidence/gate-pypi-hashes.json` (45 pkgs, 1689 PyPI files, SHA-256) |
| footprint | ~1157 MB venv on disk |
| shared env | untouched (`import cadquery` still fails on system Python) |

### 2.2 Smoke (synthetic plate 80x50x6 + through-hole r3, `C:/tmp/cadgate/`)

| Gate step | Result |
|---|---|
| box + plate-with-hole build | 1 valid solid, V=23830.353996706148 mm^3, bbox [80,50,6] exact |
| invalid negative thickness | rc=2 pre-kernel reject in 0.15 s, kernel never loaded |
| STEP export | 19014 bytes, sha256 `f586d63543ee43b769c62fab3c6e472abed4f1511c62491e75f66e05c3d22deb` |
| fresh-process reopen | 1 solid, dims exact, volume rel err ~1.7e-16 (gates 0.01 mm / 1e-5) |
| +1 mm regen (x 80->81) | dx=1.0 exact, y/z unchanged, rev2 sha `0e8229e62856166f070a1333806462c74eddccf8522662154b4b030e5b65c89b` |
| preview | 1045-edge tessellation wireframe SVG from reimported solid, sha `8d7c87dfd2bb290c9bf3897d3072656925a99b31fd55d2d2433ad96d05b85c95` |
| cold build+export | 2.33 s (budget 60 s) |
| warm rebuild | 0.004 s (budget 10 s) |
| peak working set | 331.7 MB (ceiling 2 GiB) |
| worker timeout | 120 s fixed subprocess timeout, exercised on every `run` |

### 2.3 License gate -- proposal for contract owner (NOT integrated)

Compact table: `evidence/gate-licenses-compact.txt` (full metadata texts
retained in session scratch `C:/tmp/cadgate/licenses.txt`). Mostly
permissive (Apache-2.0/MIT/BSD/PSF). Flags requiring owner review before
any SPDX/`THIRD_PARTY_NOTICES.md` edit or redistribution:

1. `casadi 3.8.2` declares LGPLv3+ (pulled via cadquery; unused by v1 ops).
2. `cadquery-ocp` wheel metadata says Apache-2.0 for the bindings, but it
   bundles OCCT (LGPL-2.1 with exception per blueprint) -- obligations
   need owner review, not a metadata copy.
3. `ezdxf`, `trame-common`, `wslink` ship empty license fields -- unresolved.
4. No `LICENSE` conclusion drawn here; no inventory file touched in this lane.

### 2.4 FreeCAD step -- PENDING_EVIDENCE

FreeCAD is not installed on this workstation (PATH + Program Files
checked). Per EVALUATION-HARNESS, genuine FreeCAD UI reopen evidence is a
human-session item; it is queued for the hardware session, not retried here.

## 3. Trusted compiler work (synthetic)

Implemented in owned paths only (`assembly-reconstruction/cad/` +
scoped `assembly-reconstruction/README.md` section):

- `anastylos_cad/validate.py` -- stdlib-only v1 schema-shape + semantic
  checks: kinds `box|cylinder|through_hole`, finite/bounded dims,
  proper-rotation `T_CAD_B`/`T_CAD_feature` (det=+1, orthonormal; legacy
  `T_D_B` rejected), parent existence, hole-on-solid, depth==thickness,
  acyclicity. Never raises on malformed input.
- `anastylos_cad/compile.py` -- stdlib-only deterministic `model.py`
  generation from fixed templates; parameters travel as one JSON
  literal; generated code calls only `json.loads`,
  `builder.build_solids`, `main` (AST-proven in tests).
- `anastylos_cad/builder.py` -- allowlisted kernel calls (venv only):
  makeBox/makeCylinder/through-cut honoring `T_CAD_feature`, STEP
  import/export, validity/volume/bounds reports.
- `anastylos_cad/worker.py` + `cadwork.py` -- fixed-arg CLI
  (`validate`/`build`/`reopen`/`run`), per-job outdir that must be
  absent-or-empty (no revision overwrites), 1 MiB spec / 256 MiB STEP
  bounds, fresh-process reopen via same-interpreter subprocess.
- `anastylos_cad/preview.py`, `evidence.py` -- stdlib-only SVG
  projection (labelled projection, model hash cited),
  feature-evidence/validation/manifest writers, and a data-only
  missing-feature envelope for the later CAD->Core `cad_context` handoff.
- `fixtures/synthetic/` -- 3 valid + 6 invalid models mirroring the
  frozen contract mutations; `tests/` -- 46-test suite.

## 4. Verification

| Command (from stated dir) | Result |
|---|---|
| `cad/`: `.venv/Scripts/python -m unittest discover -s tests` | 46/46 OK, ~20-45 s (exit artifact of stderr capture; "OK" authoritative) |
| `assembly-reconstruction/`: system `python -m unittest discover -s tests` | 54/54 OK, unchanged |
| root: `python -B tools/compliance/check.py` | 0 errors, 1 warning, 10 open reviews (== G0 record) |
| frozen `cad-feature-valid.json` through lane validator | PASS, solids `[base_plate]` |
| 6 frozen invalid mutations through lane validator | all reject with specified class |

Repair iterations used: 2 (test-side AST proof + `+1` int; `located` /
`cq.Location` kernel API shape). No golden expectation was changed to fit
implementation; no skips, no threshold edits.

## 5. Per-gate verdicts (Wave-1 CAD scope)

| Gate | Verdict | Evidence |
|---|---|---|
| Windows backend smoke (E9-serialization/H14-artifact-synthetic) | PASS | section 2 + 46-test suite |
| CAD-2 trusted compiler, synthetic | PASS | section 3, deterministic regen proven |
| CAD-2 STEP reopen (fresh worker) | PASS | round-trip gates in every `run` |
| CAD-2 FreeCAD UI reopen | PENDING_EVIDENCE | FreeCAD absent; human session |
| CAD-1 metric fit / H13-H15 physical | PENDING_EVIDENCE | no hardware, no caliper, no photos |
| CAD-3 active loop / H16 | NOT_TESTED | needs Core/Android + physical set |
| CAD-4 assembly placement | NOT_TESTED | after D evidence (Wave 4+) |

## 6. Failures routed / follow-ups

- None routed to other lanes; no lane-owned file outside CAD touched.
- To CONTRACT-OWNER: license gate proposal above (casadi/OCCT/unknown
  fields) + isolated-venv exception for the single-manifest policy need
  explicit authorization before any integration or SPDX edit.
- To INTEGRATION-QA: `cadwork.py run` + committed fixtures are ready as
  synthetic scorer inputs; `model.py` regenerates offline under the
  worker venv.
- To hardware session: FreeCAD install + UI reopen; matte rigid part +
  caliper + phone for H13-H16.

## 7. Rollback

Revert the lane commit(s) on `feat/agentcam-cad`; delete `cad/.venv`
(recreatable from `evidence/gate-freeze.txt`). No shared file, schema,
memory log, or environment was modified, so nothing else rolls back.
