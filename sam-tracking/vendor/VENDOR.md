# Vendored upstream: meta-sam-parser

Source: https://github.com/meta-models/meta-sam
Package: `meta-sam-parser` 0.1.0 (dependency-free, Python >= 3.10)
Pinned commit: `40318970a81301f31e97cdd7d605117293332b0f`
Vendored: 2026-10-28, via `raw.githubusercontent.com` at the pinned commit.

## Files (verbatim, unmodified)

SHA-256 at vendor time:

- `53e106f6334284aba7a22bd59421fbb425d61d37dcb2c5281d7775323799b3a3  meta_sam_parser/__init__.py`
- `7dca9fc121679897aa257a29446a2deaf37530b6810298c0592fb912a4674a53  meta_sam_parser/_errors.py`
- `00cf56257e57511a5636c2b41b398ddb3ee92087761a2075ad59c94a143d570f  meta_sam_parser/_mask_codec.py`
- `7d6e75b14d4cbdc725203c03227c154137491110b7efc3a77cdbb4dde854e621  meta_sam_parser/_mask_conversion.py`
- `9596fcf5d91811e29962d93aa7b91c8d476d227935c2e08b1eecf869c34d4454  meta_sam_parser/_object_prompt.py`
- `afee9678ffa1c50e077f13f2f84b4162fd45ea71c8e25b23509d13fb465f52ae  meta_sam_parser/_segmentation.py`
- `41836971940ad6a5753d28b8b909c87b4fe27de45017de0248ea02433cdc786d  meta_sam_parser/_stream.py`
- `6e0a87d2786450d8259162fd9e14bd95a494b57d7b016992f32dea3530e72ffb  meta_sam_parser/_types.py`
- `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  meta_sam_parser/py.typed`
- `0e548d73d11cfff58d242e8f400fe0843023d622fb9308edd297a7b3b9aecf65  pyproject.toml`
- `4585702556f8487fae314cd4b64c41b240aabc23e025c963c95f5d3c4f95e4da  README.md`
- `4dea99bfaa016e21bc860d73f344236bd1e5c4977d1a9a8fd32f822b500ae1be  LICENSE.meta-sam`
- `7e4ac06f04564a8388ceaea5f7f8da061c37100ed1302922f6a29c4bf71cdd30  compatibility.json`

Re-verify: `Get-FileHash sam-tracking/vendor/meta_sam_parser/* -Algorithm SHA256`
plus the four loose files above, and compare against this list.

## License

SAM License (custom, not SPDX-listed), full text in `LICENSE.meta-sam`.
Recorded in the SPDX inventory as `LicenseRef-SAM-License` with an open
human review item (same pattern as the ML Kit terms). Redistribution
carries this agreement file per its section 1.b.i.

## Why vendored, not installed

- Zero dependencies; a pip install would add a PyPI pin the offline
  Python core must not take on.
- Byte-reviewable: the exact parser bytes used for evidence decoding
  are committed next to the evidence schema that cites them.
- No network import is added to any existing module; only
  `sam-tracking/` (explicitly cloud-isolated) imports this package.

## Do not edit

These files are upstream bytes. ANASTYLOS code adapts to them in
`../anastylos_sam/`; fixes belong upstream, not here.
