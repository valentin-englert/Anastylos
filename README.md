# ANASTYLOS

Print calibration sheets for an overhead camera, plus a planar before/after change check.

Marker dictionaries, ID ranges, and millimetre frames follow the physical sheet specification.

- [`calibration/`](calibration/README.md) — A3/A4 surfaces, fixture front and back, ChArUco board
- [`change-detection/`](change-detection/README.md) — register a before/after pair and list visual changes
- [`assembly-memory/`](assembly-memory/README.md) — append-only disassembly memory and "where does this go?" queries
- [`resistor-reader/`](resistor-reader/README.md) — experimental resistor color-code reader (synthetic-only validation)
- [`tray-counter/`](tray-counter/README.md) — evidence-backed tray addition/removal candidates from detection output
- [`pcb-ocr/`](pcb-ocr/README.md) — PCB silkscreen reference-designator extraction (needs OCR engine)
- [`smd-reader/`](smd-reader/README.md) — SMD resistor marking decoder and crop reader (reuses pcb-ocr)

Third-party attribution: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
(machine-readable [`tools/compliance/spdx.json`](tools/compliance/spdx.json),
checked by `py -3.12 tools/compliance/check.py`).
