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
- [`screw-reader/`](screw-reader/README.md) — screw-head drive geometry classifier (synthetic-only validation)
- [`component-markings/`](component-markings/README.md) — component marking recognition (stub-OCR validation only)
- [`component-fingerprints/`](component-fingerprints/README.md) — visual fingerprint gallery: enroll crops, rank later photographs
- [`connector-matching/`](connector-matching/README.md) — plug/socket mating-geometry analysis and counterpart ranking
- [`screw-measurement/`](screw-measurement/README.md) — calibrated screw geometry measurement (top/side views)
- [`polarity-reader/`](polarity-reader/README.md) — polarity and orientation marker recognition with uncertainty
- [`washer-spacer/`](washer-spacer/README.md) — washer and spacer recognition with geometric characterization
- [`missing-parts/`](missing-parts/README.md) — missing/displaced-part candidates from declared-state comparison
- [`cable-routing/`](cable-routing/README.md) — cable-path extraction and read-only assembly-order graph
- [`capture-companion/`](capture-companion/README.md) — offline-first mobile capture PWA plus package validator

Architecture overview and module status: [`ARCHITECTURE.md`](ARCHITECTURE.md).

Third-party attribution: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
(machine-readable [`tools/compliance/spdx.json`](tools/compliance/spdx.json),
checked by `py -3.12 tools/compliance/check.py`).
