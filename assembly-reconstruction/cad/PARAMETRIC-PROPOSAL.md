# Proposal to CONTRACT-OWNER: parametric overlay contracts (v1.1 extension)

Lane: CAD-ENGINE. Status: proposal only — no shared schema was modified.
The frozen G0 `cad-feature-model-v1` stays the authoritative geometry
contract; everything below is additive and lane-local until the owner
adopts it.

## What the lane implemented (already working, tested)

- Stable parameter IDs `{feature_id}.{role}` derived from the validated
  feature spec (`anastylos_cad/params.py`): dimensions, hole radii,
  hole centers; through-hole depth is derived (= parent thickness) and
  rejects direct edits.
- Operator edits document (`anastylos-cad-param-edits@1`, proposed):
  base-model hash pin, author, reasoned per-parameter values; applied
  atomically with `revision+1` + `parent_model_sha256`, deterministically.
- Provenance sidecar (`anastylos-cad-param-provenance@1`, proposed):
  per-parameter origin (`estimated` vs `operator-specified`) plus
  append-only revision history; the original machine estimate is always
  `history[0]`.
- Merge rule: operator-specified values always win; fresh machine values
  for those parameters are recorded as `superseded_estimate`, never
  applied silently.
- Native `.FCStd` export: Part::Box / Part::Cylinder / Part::Cut feature
  tree with editable properties (no imported BRep/STEP blob), plus a
  runnable `.FCMacro` rebuild script. Tree/kernel geometry equivalence
  is verified by an independent OCP rebuild on every export.

## Requested owner decisions

1. Adopt (possibly renamed) `anastylos-cad-param-edits@1` and
   `anastylos-cad-param-provenance@1` as versioned lane contracts, or
   keep them lane-local with a different `anastylos-cad-...` prefix.
2. Confirm the merge rule (operator wins, superseded estimates recorded)
   as the cross-wave invariant for CAD-3 refinement loops.
3. Confirm the `.FCStd` layout (Document.xml feature tree + GuiDocument,
   no shape blobs) as the interchange shape, and schedule the human
   FreeCAD UI session (open → edit Length → recompute → STEP export),
   which this lane cannot execute (FreeCAD not installed).
4. Rule on whether `param-provenance.json` joins the revision manifest
   bundle or stays a lane-side artifact.

## Acceptance evidence this lane holds

- `tests/test_parametric.py`: 9 tests (IDs, edit/reject classes,
  determinism, merge, FCStd structure, tree/kernel match, macro).
- Demo: plate 80×50 r3 → edits (x=90, r=4) → regen bbox [90,50,6],
  volume 23830.354 → 26698.407, y/z unchanged; FCStd reopen → XML
  Length edit 90→95 → OCP recompute bbox [95,50,6] → STEP reimport
  matches ≤1e-5. See EVIDENCE.md follow-up section.
- Real-FreeCAD open/edit/recompute: PENDING_EVIDENCE (no install).
