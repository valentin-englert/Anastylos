# sam-tracking third-party notes

Preserved here and referenced from the repo root
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md), not duplicated.

## meta-sam-parser 0.1.0 (vendored source)

- Upstream: https://github.com/meta-models/meta-sam, Meta Platforms, Inc.
- Pinned commit `40318970a81301f31e97cdd7d605117293332b0f`, vendored
  2026-10-28. Files: `vendor/meta_sam_parser/*.py`, `pyproject.toml`,
  `README.md`, `compatibility.json`, `LICENSE.meta-sam`.
- Per-file SHA-256: `vendor/VENDOR.md`.
- License: SAM License (custom, not SPDX-listed), full text in
  `vendor/LICENSE.meta-sam`. Recorded in `tools/compliance/spdx.json`
  as `LicenseRef-SAM-License` with an open human review item.
- Role: official SAM 3.x wire-grammar parser — Responses stream
  adapter, box/mask record parsing, mask decode (`lossless` /
  `one_bit`), COCO RLE/SVG conversion, object-prompt builder.
- Dependency-free, Python >= 3.10. No code in `anastylos_sam/`
  modifies it; all adaptation lives on our side.

## Meta Model API (service, not shipped code)

- `https://api.meta.ai/v1` — file upload + Responses streaming for
  model `sam-3.1`. Docs: https://dev.meta.ai/docs/sam/overview.
- Paid, billed per image / per video frame (see README). No SDK is
  used; the adapter speaks HTTPS via stdlib `urllib`.
- Only SNES benchmark imagery is uploaded, never personal data, and
  only with explicit `--live --upload-ok` operator opt-in.
