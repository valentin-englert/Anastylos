# Audit findings (v0.1.0)

Sources read-only: Anastylos `a172fbb9e127e7a3a93b128516b6592bfbbc81ef`
(10 reader modules, AGENTS.md, ARCHITECTURE.md, README.md,
THIRD_PARTY_NOTICES.md, calibration/requirements.txt) and blobcam
`e180aac2480a2c038de5ee61b66702210633af87` (docs + acquisition/config/frames
modules, reference only). Files were fetched via the GitHub API and each was
verified against the tree's git blob SHA-1. Neither repository was modified.

1. **Readers are reusable as libraries.** Every module exposes a pure
   `analyze(...)`; all 543 of their own tests pass unchanged on Linux /
   Python 3.12. No reader code was copied or edited.
2. **No reader version numbers.** Identity is pinned by LF-normalised code
   hashes (`src/anastylos_microscopy/pinned_reader_hashes.json`), plus
   internal constants where present (fingerprint DESCRIPTOR_VERSION,
   connector OBSERVATION_VERSION).
3. **Calibration spelling divergence.** `pixels_per_mm` (polarity, washer,
   screw-measurement) vs `px_per_mm` (pcb-ocr, fingerprints, connectors).
   Kept as-is; translated per reader by the adapter.
4. **Inconsistent status fields.** screw-measurement reports `verdict`; others
   `status`; statuses differ in vocabulary (ok/ambiguous/unknown/rejected/
   enrolled/observed). The adapters map them to one Core vocabulary and keep
   the native value (`native_status`).
5. **OCR dependency.** smd, pcb-ocr and markings depend on Tesseract via
   pcb-ocr's backend; markings imports pcb-ocr by a sys.path insert. With
   Tesseract missing they return `unavailable` honestly.
6. **Context-dependent readers.** screw-measurement and washer require a
   `view`; polarity requires a family; fingerprints need a gallery. The Core
   reports `input_error` / `unavailable` instead of guessing.
7. **False-confident resistor read** on a synthetic glare image (22.9 kOhm for
   2.2 kOhm, score 0.926). Not tuned away; documented in LIMITATIONS.md.
8. **Core bug found and fixed during testing:** byte-identical frames in one
   capture session produced the same inspection id and overwrote each other;
   the id now includes the capture context (manifest frame index, declared
   hash, session, component observation).
9. **BlobCam** concepts reused only as ideas (original frames untouched,
   per-frame sha256, UTC `Z` times, orientation as metadata, operator-owned
   gates). BlobCam's gate table (G-001..G-004 PASS, rest pending) is
   BlobCam's own and was not changed or re-verified.
10. **Local Anastylos backup archive on the box was corrupt**; the API fetch
    replaced it. No `.git` metadata exists in the fetched tree, so
    `readers` shows `detected_git_head: null` there; a real checkout reports
    its HEAD (read from `.git` files, git is never executed).
11. **tray-counter, missing-parts** are excluded from the microscope registry
    (they are not single-component microscope readers).

## v0.1.1 addendum (independent QA of v0.1.0)

Codex QA on Windows confirmed three further findings, fixed in v0.1.1
(QA_FIX_REPORT.md): QA-01 batch exit status always 0; QA-02 capture frames
reachable outside the capture root through junctions/symlinks; QA-03 the
false-confident glare read of item 7 is now held as `ambiguous` by a Core
evidence assessment (root cause: a 4 px highlight sliver read as a fifth
"white" band plus channel clipping, which the gray-level glare metric missed).
The QA-03 sweep additionally showed that the unchanged reader's `ok` is often
wrong on stressed synthetic renders; see LIMITATIONS.md item 4.
