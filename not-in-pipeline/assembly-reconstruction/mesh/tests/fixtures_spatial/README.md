# fixtures_spatial — correctly-labelled synthetic fixtures

All files in this directory are **synthetic test data only**.
No real camera, image, calibration, mask, or board is represented.
Every JSON fixture carries `"fixture_provenance": "synthetic"` plus a
`fixture_note` starting with `SYNTHETIC_TEST_FIXTURE_ONLY`.

Sidecars staged at test runtime are NOT committed here: they are generated
in `tmp/` from the frozen production vector
`capture-companion/control/contracts/vectors/spatial-valid.json`
(which is itself labelled `SYNTHETIC_SCHEMA_FIXTURE_ONLY`) with
per-view synthetic IDs, synthetic image bytes
(`synthetic-mesh-fixture:<tag>` marker), and explicit synthetic labels in
`provenance.producer`, `limitations`, and `pose.method`.
Committed sidecars would risk confusion with real evidence; runtime
generation keeps every hash pinned and every label explicit.

## Inventory

- `profile.synthetic-v1.json` — valid `anastylos-camera-calibration@1`
  profile (25 calibration views, 8 held-out, `validated`) with synthetic
  K/dist. Passes production `validate_profile` as `accept`.
- `config.synthetic-v1.json` — adapter ROI grid + carve params for tests.
- `mask-meta.synthetic-template.json` — mask sidecar shape template.
  `provenance` stays `manual` (editor `synthetic-test-operator`) because
  `anastylos_mesh/spatial_adapter.py` rejects `synthetic` mask provenance
  (`MASK_PROVENANCE_NOT_OPERATOR`: real-evidence-only gate). This is a
  production-API gap for synthetic end-to-end tests: committed mask
  fixtures cannot use `provenance: synthetic` and still reach READY.
  The synthetic label lives in `fixture_provenance` + `editor` instead.
- Runtime files (in `tmp/`, never committed): `capture_set.json`
  (with `fixture_provenance: synthetic`), `sidecars/*.json` (synthetic
  producer/limitations/method), `packages/*/manifest.json` (with
  `fixture_provenance: synthetic`), staged image bytes (with
  `synthetic-mesh-fixture` marker), mask `.png` + `.json`.

## Production staleness rule (test 8)

`validator.py:POSES_AGE_GATE_MS=150`:
- `solve_source=saved_image` + `sample_age_ms>150` → review
  `STALE_SAMPLE_NOTED` (verdict `needs_review`, not metric-ready).
- `solve_source=timestamp_interpolated|unknown` + `sample_age_ms>150` →
  reject `STALE_SAMPLE`.
- `solve_source=timestamp_interpolated|unknown` + age missing → review
  `UNKNOWN_SAMPLE_AGE`.

Test 8 stages `timestamp_interpolated` with bracketing samples and
`sample_age_ms=200` to hit the true `STALE_SAMPLE` reject via the
production validator.
