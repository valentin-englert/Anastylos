# Contract Owner Approval Requests — Wave 1.5 integration

Date: 2026-10-29. Lane: `integration/spatial-capture-v1`.
Status: REQUESTED, none approved. Integration blocks nothing on these:
affected features are preserved on the branch with the gate reported
as BLOCKED/PENDING, and no SPDX, inventory, or frozen-contract bytes
were edited to work around them.

## R1. MCP isolated-adapter-environment exception (`mcp==2.3.0`, MIT)

- Scope: allow installing the live MCP SDK into an adapter-only
  process for `tools/capture-control/adapter.py` (nine guided tools).
- Record: `tools/capture-control/requirements-mcp.txt` (pin record
  only; explicitly "NOT the project manifest").
- Consumers: guided MCP host smoke test, `test_adapter.py`
  (`test_nine_tools_registered`, currently skips with reason).
- Meanwhile: handler logic stays covered without the SDK
  (`test_handlers.py` green); live host smoke is NOT_TESTED.

## R2. CAD isolated-venv exception + SPDX/notices edit authorization

- Scope: (a) recognize the isolated CadQuery worker venv as the
  sanctioned CAD runtime; (b) authorize the exact SPDX inventory +
  `THIRD_PARTY_NOTICES.md` edits that add its closure (45 pins).
- Closure (verified live 2026-10-29, `pip freeze` == committed
  `assembly-reconstruction/cad/evidence/gate-freeze.txt`, 45 lines, no
  diff; 1689 PyPI filename/sha256 entries in
  `gate-pypi-hashes.json` match): `cadquery==2.8.0`,
  `cadquery-ocp==7.9.3.1.1`, `cadquery-ocp-proxy==7.9.3.1.1`,
  `casadi==3.8.2` (LGPLv3+, installed METADATA),
  `numpy==2.5.4`, `vtk==9.6.2`, `nlopt==2.11.0`, + 38 more pinned.
- Consumers: CAD build/reopen/run/export-fcstd paths, Wave-2 metric
  CAD tests. Request originates at `cad/EVIDENCE.md:131-133`.
- Meanwhile: venv preserved and reused read-only; no reinstall, no
  inventory edit; CAD suite green (63 tests) on recorded closure.

## R3. OCCT redistribution / SPDX conclusion

- Scope: rule the SPDX conclusion for OCCT bundled inside
  `cadquery-ocp` (blueprint: LGPL-2.1 with exception).
- Evidence: installed `cadquery-ocp` METADATA says `Apache-2.0` and
  the wheel ships only the Apache bindings LICENSE; no LGPL/OCCT
  text found anywhere under the installed `cadquery_ocp*` tree.
  Verified obligations (Apache bindings text) are distinguished from
  the unresolved legal interpretation (bundled-OCCT terms).
- Consumers: any redistribution of the CAD environment or STEP
  pipeline beyond this workstation.
- Meanwhile: reported PENDING; never marked PASS.

## R4–R6. SPDX conclusions for three empty-license packages

- Scope: conclude one SPDX license each for `ezdxf==1.4.5`,
  `trame-common==1.2.9`, `wslink==2.6.0` (installed METADATA
  `License` blank; `gate-licenses-compact.txt` UNKNOWN; confirmed
  live 2026-10-29).
- Consumers: same redistribution scope as R2/R3.
- Meanwhile: PENDING; `pip-licenses` is not installed (no new tool
  installed for this audit).

## R7. Parametric contract decisions D1–D4

Source: `assembly-reconstruction/cad/PARAMETRIC-PROPOSAL.md:29-41`.

- D1: adopt `anastylos-cad-param-edits@1` +
  `anastylos-cad-param-provenance@1` as versioned contracts vs
  lane-local naming (the two overlay schemas). Consumers: CAD-ENGINE,
  CAD-3 loop, manifest/regen tooling.
- D2: confirm operator-wins + `superseded_estimate` recording as the
  cross-wave merge invariant. Consumers: CAD-3 refinement, regen
  worker, operator UI.
- D3: confirm `.FCStd` Document.xml+GuiDocument tree shape (no shape
  blobs) and schedule a human FreeCAD open/edit/recompute/STEP
  session. Consumers: CAD export, FreeCAD operator, STEP downstream.
- D4: rule `param-provenance.json` into the revision manifest vs a
  lane-side artifact (also settles the compiler-input vs
  revalidatable-spec/parameter-provenance placement for the manifest
  producer). Consumers: manifest producer/consumers, custody bundle.
- Meanwhile: existing compiler + parametric behavior preserved
  untouched; no frozen G0 schema was modified; no versioned G0 change
  proposal is filed (none required — the pending items are additive
  lane contracts, and the G0 cad-feature-model schema is untouched).

## R8. Frozen G0 identifier rename (only if ever desired)

- Scope: renaming the frozen contract ID `anastylos-agentcam-g0`
  (`CONTRACT.json:2`, `contracts/README.md:1`) is owner-only and NOT
  requested by integration (default: no change; renaming needs
  migration planning across all consumers).
- Meanwhile: user-facing borrowed branding removed only where safe
  and local (5 doc/--help strings); schema IDs, wire protocols,
  database names, and public interfaces unchanged.

## Explicitly NOT Contract Owner scope (routed elsewhere)

- Measured print scale for all 5 sheets + controller height
  (operator, Wave-2 measurement plan; `measured_print_scale` all
  NULL today) — Wave-2 readiness matrix.
- Android SDK provisioning + JDK 21 question (operator/infra; exact
  blockers documented in the integration report).
- `EVIDENCE.md` "45 pins incl. venv pip" wording nit (CAD lane;
  freeze has 45 lines, no pip entry).
