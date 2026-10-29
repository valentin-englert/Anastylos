# Integration proposal — mesh experiment (narrow scope)

Architecture note only. No production code, contract, or UI is changed
by this document. The hybrid Mesh/Parametric/Both mode sketch in
[CAD-COMPARISON.md](CAD-COMPARISON.md) §4 is the starting point; this
note lists what promotion would require. No UI implementation is
proposed here.

## Mode architecture note (no UI)

A future orchestrator (outside this experiment) could offer three
modes per run and record the mode in run provenance:

- `Mesh`: silhouette reconstruction only. Output: PLY/STL +
  `mesh-evidence-v0` manifest. Use: fast visual feedback, rough dims.
- `Parametric`: spec-first CAD compile only; the mesh path is skipped
  when dims come from calipers/drawings. Output: CAD evidence bundle.
- `Both`: hybrid — mesh runs first for visualization + bbox hints; an
  estimator (human today) fits `cad-feature-model-v1` features to the
  mesh + source views with `dataset_sha256` pinned to the same
  observation set; the CAD worker compiles; the operator confirms via
  `apply-edits` revisions.

Invariant: the mode must never change what either pipeline outputs
for identical inputs. Linkage between lanes is by hash citation
(`dataset_sha256`, manifest pins), never by mutation; both lanes keep
no-overwrite revisions (`export.py:54-70` here;
`cad/anastylos_cad/worker.py:58-69` there).

## Adapter promotion requirements

`anastylos_mesh/spatial_adapter.py` is experimental. Each item below
blocks production use until resolved (assumption numbers cite the
module docstring `spatial_adapter.py:66-107`):

1. **Real mask supplier.** Today masks arrive as operator-traced
   `<sha>.png+.json` pairs with an adapter-defined sidecar shape
   (assumption 8). A production mask source (tracing tool or qualified
   auto-segmenter emitting the agreed sidecar) must exist, with its
   schema owned outside this experiment.
2. **Staged-profile mandate.** The production validator silently skips
   profile binding when unstaged (`validator.py:849` has no
   else-branch); the adapter requires a staged profile
   (`PROFILE_UNSTAGED`, assumption 6). Promotion needs either a
   production-side mandatory-profile rule or a documented,
   contract-owned exception path — not adapter behavior alone.
3. **Private-API stabilization.** The adapter depends on private seam
   functions `_resolve_image_bytes` / `_load_hash_pinned_json`
   (assumption 1) and an adapter-defined `capture_set.json` file
   layout + `spatial/boards/` subdir (assumptions 2, 5). These need
   public, versioned production APIs (or a frozen import contract)
   before any consumer outside the experiment may rely on them.
4. **Epochs pinning.** `epochs.json` carries no hash pin (assumption
   4, custody-owned Wave-2 work). The adapter records its raw sha256
   and trusts store content exactly like production — acceptable for
   the experiment, not a precedent for promotion.
5. **ROI estimator or owned config.** The voxel grid has no production
   estimator; `config["grid"]` is mandatory operator input (assumption
   10). Either a validated estimator or a contract-owned manual-grid
   procedure is required.
6. **Mixed-provenance policy.** Mixed manual+auto mask sets raise
   `MIXED_MASK_PROVENANCE` (assumption 9) because `mesh-evidence-v0`
   carries one label. A promoted manifest needs a per-view provenance
   record or an owned collapsing rule.
7. **Pose-epoch registration or capture discipline.** Mixed epochs
   reject (`carve.py:133-149`); no registration transform exists
   anywhere (assumption 7). Promotion needs either a registration path
   or an enforced single-epoch capture rule.

## Contract-owner questions

1. Is `mesh-evidence-v0` (`manifest-schema.json`) acceptable as a
   starting point for a normative evidence record — and if so, who
   owns it? Until approved it stays NON-NORMATIVE and no consumer may
   treat it as a system contract.
2. What is the normative mask sidecar shape (replacing the
   adapter-defined `{mask_sha256, provenance, editor, edited_bool}`),
   and which lane owns mask schema + mask tooling?
3. Who owns the mask-provenance vocabulary for real evidence
   (`manual`/`auto` semantics, editor identity, `edited_bool`
   meaning), and the mixed-provenance collapsing rule?
4. Is the single-pose-epoch capture discipline acceptable as a
   permanent constraint, or is a registration-transform project
   planned (mesh + spatial jointly)?
5. Who owns ROI grid specification for real runs (estimator,
   procedure, or operator config)?

## What must NOT change

- **Frozen G0 occupancy rule** (`carve.py:3-5`, `:220`): any
  observed background carves; unobserved carries no evidence. Tuning
  it (e.g. voting, priors) is a new algorithm version, not a patch.
- **CAD engine**: `assembly-reconstruction/cad/` stays read-only to
  this experiment (per [CAD-COMPARISON.md](CAD-COMPARISON.md) scope
  rule). Mesh outputs may inform specs; they never mutate CAD inputs.
- **Append-only memory**: no mesh component writes to
  `assembly-memory/` (read-only consumers only, per repo contract).
- **Production validators**: the adapter imports them read-only
  (`spatial_adapter.py:177-242`). Fixes to validation logic belong in
  the production modules, never as adapter-local forks.
- **Evidence formats**: `result.json`, `memory.jsonl`, capture
  packages, calibration JSON — the experiment links to them by hash
  and changes none of them.
