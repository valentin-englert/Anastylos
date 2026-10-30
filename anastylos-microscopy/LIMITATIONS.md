# Limitations (v0.1.2)

1. **No real-image inspection validation.** The Core has not processed a real
   microscope capture. A short live preview was visible on the Pi HDMI output,
   but no frame was saved, handed to the Windows Core or evaluated. No GTX 970
   accuracy is known. All bundled images are synthetic and labelled.
2. **OCR unavailable on the development box.** smd, pcb_ocr and markings were
   exercised only with a deterministic stub engine; with no Tesseract they
   report `unavailable` (never a guess). Real OCR quality on microscope
   frames is unknown.
3. **Capture confinement is improved but not a universal race-proof sandbox.**
   v0.1.1 and v0.1.2 passed actual Windows NTFS tests for an in-root junction,
   an outside-root junction, and validation/inspection agreement. The
   implementation resolves the frame, checks reparse-point and file identity,
   opens and rechecks it, then hashes/decodes/preserves the same bytes. A
   writer may still replace one legitimate in-root file with another; the
   manifest hash detects changed bytes, but this is not a proof against every
   filesystem race or filesystem provider. Treat untrusted capture folders as
   read-only copies on a local disk. Windows symlink tests were **PENDING**:
   creating symlinks failed with `WinError 1314` under the QA account. Tests
   requiring POSIX `O_NOFOLLOW` or POSIX rename races are also unavailable on
   Windows. Fail-closed policies may reject legitimate hard-linked frames,
   reparse points other than supported links/junctions (for example cloud
   placeholders), `:` in names (NTFS streams), reserved device names, and
   trailing dot/space names.
4. **Resistor reliability remains inadequate for automatic identification
   (QA-03).** The unchanged reader still returns 22.9 kOhm for the synthetic
   2.2 kOhm glare fixture; the Core holds that case as `ambiguous` while
   retaining the native result. v0.1.2 also holds results when at least 10% of
   any decoded band's pixels have a channel at or above 250. This rule reduced
   held-out synthetic false accepts from 43 to 23, while correct accepted
   candidates fell from 24 to 15. Among retained held-out candidates, 15 were
   correct and 23 incorrect (39.5% precision); only 38 of 196 native `ok`
   results remained candidates. The residual 8.2 Ohm vs expected 82 kOhm
   examples had internally consistent, unclipped bands and survived the
   evidence checks. Scores and the clipping threshold are not calibrated
   probabilities. These synthetic results do not establish real-image
   reliability. Do not use automatically inferred resistor values as confirmed
   specifications; keep operator confirmation separate.
   
   In the 1,200-render development grid, v0.1.2 accepted 294 correct and 25
   incorrect candidates, and held back 270 correct / caught 344 incorrect
   native `ok` reads. Development precision was 92.2%, but this is a synthetic
   development score, not an unbiased performance estimate.
5. **No mm without calibration.** Without a valid calibration (provenance +
   matching resolution) all geometry is in pixels. The example calibration
   (160 px/mm) is fictitious. Calibrations are not checked against lens/zoom
   state, which the MUSTOOL does not report; the operator must keep them
   matched.
6. **Reconciliation is shallow.** Cross-reader comparison covers resistance
   value and component category only; other disagreements are left visible
   per reader, not resolved.
7. **Reader versions.** The readers expose no version numbers; identity is
   their code hash compared with the hashes pinned for a172fbb9. Another
   revision runs but is flagged `matches_pinned_revision: false`.
8. **Scores are uncalibrated.** Reader scores are heuristics, not
   probabilities.
9. **Determinism scope.** Replay is byte-for-byte only with the same reader
   code, OpenCV/NumPy build and OCR engine version; the `run` block (timings,
   host) is excluded from the digest by design.
10. **No identification database.** Candidates never become manufacturer,
   rating or part-number claims; markings remain transcriptions.
11. **Unschematised outputs.** Eval reports (`anastylos-microscopy-eval-report/1`)
   still have no formal JSON Schema; `batch.json` has one since v0.1.1.
12. **Exit codes describe processing, not recognition.** Exit 0 means every
   image was inspected; it says nothing about whether a component was
   identified (abstentions are success). An OCR reader that is `unavailable`
   beside a working reader does not make the exit code non-zero; it is
   reported in `batch.json` `ocr.unavailable_entries`.
13. **Stub-OCR held-out artefact (unchanged from v0.1.0).** With the
   size-keyed stub OCR used in tests, held-out item `mic01-103` scores
   `false_confident` because the stub returns "472" for every 360 px-wide
   panel. It is a property of the test double, not of a real engine.
14. **Pi appliance service is not provisioned.** A short manual GStreamer
   preview from the MUSTOOL through the UGREEN adapter was visible on the Pi's
   HDMI monitor. The preview process ended; no service is installed, no
   evidence frame has been captured through the node, and preview/capture
   coexistence, automatic boot, reconnect and long-duration operation remain
   untested. Physical gates MC-HW-03 and MC-HW-05 remain pending. The node
   requires an externally installed `v4l2-ctl`, an already-active MJPG
   format, and the Pi OS GStreamer/PyGObject packages; it does not configure
   camera controls or infer calibration.
15. **Out of scope:** camera control, Raspberry Pi deployment, Assembly Memory
   writes, tray-counter, missing-parts, AI models.
