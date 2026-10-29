# ANASTYLOS trusted CAD worker (`assembly-reconstruction/cad/`)

Narrowly owned CAD-ENGINE lane: a trusted restricted feature-spec
compiler plus an isolated CadQuery/OCP Windows worker. It turns a
validated `cad-feature-model-v1` document into a regenerable
construction script, a genuine STEP solid, a hash-cited preview, and
per-feature evidence/uncertainty -- without ever executing
model-authored Python.

The existing 2D projector next door is untouched and stays stdlib-only;
this worker is a separate runtime by design (see the CAD backend
decision in the frozen blueprint). Architecture source of truth stays in
[`ARCHITECTURE.md`](../../ARCHITECTURE.md); contract schemas and vectors
live read-only in `capture-companion/control/contracts/`.

## Runtime layout

```text
cad/
  anastylos_cad/   validate.py (schema+semantic, stdlib) / compile.py (script gen, stdlib)
                   builder.py (allowlisted kernel calls, venv) / worker.py (CLI)
                   preview.py + evidence.py (SVG/evidence/manifest, stdlib)
                   params.py (stable params, edits, provenance, merge; stdlib)
                   fcstd.py (native parametric .FCStd + macro; stdlib + OCP check)
  cadwork.py       CLI entry (mirrors ../reconstruct.py)
  tests/           46-test suite (see below)
  fixtures/synthetic/  valid models + 6 invalid mutations (all labelled synthetic)
  evidence/        committed Windows-gate records (freeze, PyPI hashes, licenses)
  .venv/           isolated worker env, gitignored (Python 3.12.10, CadQuery 2.8.0)
```

`validate`, `compile`, `preview` and `evidence` import nothing beyond the
standard library, so contract checks run anywhere. `builder` and the
`build`/`reopen`/`run` worker paths require the isolated worker venv.

## Worker environment (isolated, pinned)

The CAD worker must never be installed into a shared environment. Setup,
from this directory, exactly once per checkout:

```powershell
C:\Users\HP\AppData\Local\Programs\Python\Python312\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install "cadquery==2.8.0"
```

This resolves the recorded 45-package closure (`evidence/gate-freeze.txt`,
PyPI SHA-256 table in `evidence/gate-pypi-hashes.json`, ~1.2 GB on disk).
License review is owned by the contract owner: the closure is mostly
permissive but contains `casadi` (LGPLv3+), OCCT bundled inside
`cadquery-ocp` (review LGPL-2.1-with-exception obligations), and three
packages with empty license metadata -- see `EVIDENCE.md`. No dependency
or SPDX inventory was edited in this lane; integration needs explicit
contract-owner authorization.

## Run

```powershell
# from the assembly-reconstruction/cad directory, worker venv python:
.venv\Scripts\python.exe cadwork.py validate --spec <model.json>
.venv\Scripts\python.exe cadwork.py build --spec <model.json> --out <empty-dir>
.venv\Scripts\python.exe cadwork.py reopen --step <model.step>
.venv\Scripts\python.exe cadwork.py run --spec <model.json> --out <empty-dir>
# parametric follow-up (stdlib except export-fcstd's kernel cross-check):
.venv\Scripts\python.exe cadwork.py apply-edits --spec <model.json> --edits <edits.json> --out <empty-dir>
.venv\Scripts\python.exe cadwork.py export-fcstd --spec <model.json> --out <model.FCStd>
.venv\Scripts\python.exe cadwork.py merge-estimates --current <model.json> --provenance <prov.json> --estimate <model.json> --out <empty-dir>
```

Editing flow: `apply-edits` writes a new `feature-model.json`
(revision+1, base hash linked) plus `param-provenance.json`
(estimated vs operator-specified + history); regenerate with `run`;
merge later machine estimates with `merge-estimates` (operator values
always win, superseded estimates recorded, never applied). `export-fcstd`
writes a native parametric Part tree (Box/Cylinder/Cut, no shape blobs)
plus a `.FCMacro` rebuild script, after proving tree/kernel geometry
equivalence. Contract-extension proposal: `PARAMETRIC-PROPOSAL.md`.

`validate` is stdlib-only and also runs on the system Python. `run`
writes one revision bundle: `feature-model.json`, `model.py` (generated
trusted construction script), `model.step` (mm units), `preview.svg`
(tessellation wireframe projected from the reimported solid),
`feature-evidence.json`, `validation.json`, `manifest.json` (SHA-256 over
everything). Exit codes: 0 ok, 2 pre-kernel validation reject, 3
kernel/round-trip failure, 4 usage or I/O error. `--out` must be absent
or empty: revisions are never overwritten. `model.py` regenerates
offline: executing it calls only the allowlisted builder entry with the
embedded validated parameters (proven by `test_compile.py`).

Example (synthetic plate with through-hole):

```powershell
.venv\Scripts\python.exe cadwork.py run --spec fixtures\synthetic\plate_hole.json --out out\rev1
```

## Tests

```powershell
# from the assembly-reconstruction/cad directory:
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

56 tests: frozen-vector alignment (the G0 `cad-feature-valid.json`
passes; all 6 invalid mutations reject with the specified class),
validator edges (reflection, non-rigid frames, NaN/Inf, depth mismatch,
legacy `T_D_B` name), compiler determinism + AST no-foreign-code proof,
kernel build validity, fresh-process STEP round-trip (0.01 mm / 1e-5
gates), +1 mm regen isolation, pre-kernel rejection ordering, worker
guards (no-overwrite, size bounds), preview/evidence/manifest checks,
and offline `model.py` regeneration, plus 10 parametric tests (stable
IDs, edit/reject classes, determinism, operator-wins merge, FCStd
structure, independent tree/kernel geometry match, macro content,
CLI edit-chain with hole move). The existing
`assembly-reconstruction` suite (54 tests, stdlib-only) must keep
passing unchanged; run it from its own directory.

## Validation scope

- Synthetic: full 46-test suite green on the gated Windows runtime;
  Windows gate smoke (box + plate-with-hole + invalid case, STEP
  reopen, +1 mm regen) PASS -- see `EVIDENCE.md`.
- Real hardware: no physical acquisition, no caliper checks, no FreeCAD
  UI reopen (FreeCAD is not installed on this workstation). All physical
  gates (E10/E11, H15/H16) are PENDING_EVIDENCE, and every synthetic
  result is labelled `evidence_class=synthetic`.

## Known limitations

- v1 operation vocabulary only: `box | cylinder | through_hole`.
  Profile extrudes, blind cuts, slots and lofts are versioned v2 work.
- One solid per revision; hole placement follows the hole feature frame.
- Preview is an orthographic tessellation wireframe, not a shaded
  render; STEP carries geometry, not construction history.
- The missing-feature envelope (`evidence.missing_feature_envelope`)
  is data-only handoff for a later-wave viewpoint planner.
