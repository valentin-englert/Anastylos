# XCAL run 4 — method fixed before fitting (owner decision 2026-10-31)

Owner (valentin-englert), 2026-10-31: the Xiaomi M2101K6G must carry a
profile now; no further captures; rules may be adapted if recorded.

Rule (written before run 4 was computed):

1. Inputs = run 3 exactly (`xcal-run3`): 18 hand-held tilted ChArUco views,
   every 5th tripod ChArUco view, every 3rd tripod A3 fit view; the 26 A3
   validation views (odd sorted index) stay untouched.
2. Outlier rule (standard single-pass rejection): every **fit-side** view
   whose per-view PnP reprojection RMS under the run-3 profile exceeds
   `MAX_VIEW_RMS_GATE_PX` = 1.5 px is removed. Run 3: exactly one view
   (A3, 488 mm, 1.79 px). Nothing else is removed.
3. Refit **once** with the unchanged `calibration/cal01_joint.py`, then the
   unchanged `cal01_scale_check.py` on the 26 untouched A3 views and the
   CAL-01 scale anchor (481.0 ± 0.2 mm vs 480.98 mm), then
   `spatial/validate.py profile`.
4. All gates unchanged (held-out ≤ 0.8 px, cross-board ≤ 1.5 px, per view
   ≤ 1.5 px, 25–40 views). If run 4 fails, the result is final; no run 5.
5. A passing profile is bundled as `measured-profile-M2101K6G.json` with
   this rule named in its `limitations`.

Known weakness, stated openly: the rejection criterion is error-based and
was chosen after run 3 was seen. The independent evidence is the
untouched cross-board set and the scale anchor, not the fit itself.
