# SNES-02 evidence contact sheets — index

Paired-view contact sheets and detail crops backing the SNES-02
observability analysis. All stills are decoded from the immutable
originals (`C:\ANASTYLOS\incoming\20261028_144225CAM1.mp4`,
`C:\ANASTYLOS\incoming\VID_20261028_144224CAM2.mp4`) with the validated
mapping cam2_PTS = cam1_PTS + 0.76896 s (see `../sync-manifest.json`).
Tile labels carry exact decoder frame indices + PTS + per-pair residual
(all residuals within ±20 ms; gate was ±40 ms, 0 failures in 179 pairs).

Conventions: times below are CAM1 video PTS unless marked C2.
Display pixels: CAM1 720×1280, CAM2 1080×1920, origin top-left.

## Teardown overview (this directory)

`skim-CAM1-1of4..4of4.jpg`, `skim-CAM2-1of4..4of4.jpg`: 0.5 fps full-span
skim (6×5 tiles). Phase segmentation: static 0–8 s; back-shell screws
12–91 s; cover removal 92–96 s; finger-removed PCB screws 104–118 s;
PCB lift 120–130 s; buttons/pads to tray 132–181 s.

## Paired case sheets (`paired/`)

| Sheet(s) | Span (cam1) | Content |
|---|---|---|
| `pair-SY1` | 12.0–12.4 @10fps | Sync check 1: approach-to-engage, pose progression consistent |
| `pair-SY2` | 84.0–84.4 @10fps | Sync check 2: D3 deposit, hand tray→controller in both views |
| `pair-SY3` | 92.0–92.4 @10fps | Sync check 3: shell half-open, same lift pose both views |
| `pair-P1c-1of2/2of2` | 12–32 @0.5fps | E1 engagement context (C2: tip at S1; C1: hands occlude) |
| `pair-P1d-1of3..3of3` | 30–35.5 @3fps | R1 lift + D1 deposit + reposition |
| `pair-P2c-1of2/2of2` | 37.5–56.5 @0.5fps | E2a/E2b engagement context, incl. 41.5 interruption |
| `pair-P2b-1of2/2of2` | 40–44 @3fps | E2a lift, D2a tray visit, finger probe, E2b re-engagement |
| `pair-P2d-1of2/2of2` | 55–60 @3fps | R2b lift, D2b deposit, 180° rotation, E3a start |
| `pair-P3c-1of2/2of2` | 58.5–84 @0.5fps | E3a engagement context (C2: tip in B; C1: occluded) |
| `pair-P3d-1of2/2of2` | 82.5–87.5 @3fps | R3 lift, D3 deposit, reposition, E4 start |
| `pair-P4c` | 85.5–91.5 @1fps | E4 engagement context (C2: tip at S5; C1: occluded) |
| `pair-P4d-1of2/2of2` | 90–95 @3fps | R4 lift, D4 tray visit (no deposit), pry, shell separation |
| `pair-R3T-1of2/2of2` | 83.5–84.8 @10fps | R3/D3 dense: lift from A-post, descent to cell 1 |
| `pair-R4T-1of3..3of3` | 90.4–92.0 @10fps | R4/D4 dense: lift, tray-area handling, pry start |
| `pair-N1` | 7.5–12.5 @1fps | Negative: hands present, no tool, no removal |
| `pair-N4` | 95–98.5 @2fps | Loose screw on sheet + finger recovery to tray |
| `pair-N5-1of2/2of2` | 103–112 @1fps | Negative: finger-only PCB screw removals, driver parked |

## Detail crops (`details/`, grid overlay in original display px)

Site/tip evidence: `back-before`, `back-E1`, `back-postE1` (S1 region
before/during/after); `S1-before`, `S1-eng`; `S2aw`, `S2bw`,
`S2b-liftw` (finger occlusion + hole glimpse); `S3w-t1/t2/t3/t4`,
`S3w-785/805` (E3a site-B stability + B→A switch 78.5–80.5);
`B-after`, `B-pre2` (B empty after / occluded before); `S5-eng2`,
`S5-lift2`, `S5-trans2`, `S5-pre10/11` (S5 edge post); `full-E1/E2/E3/E4-c2`
(orientation frames); `R1-trans6/7` (screw ON magnetic tip — only direct
tip-carry); `R3-trans` (tip already at tray, out of ROI); `R4tip-1`
(C1 hand occlusion during R4 descent).
Tray counts (CAM1 cell 1): `tray-D1a/D1b`, `tray-P2b-pre/mid`,
`tray-D2/D3/D4/N4/N4b/N5`, `cell1-91/96/840/844`, `D2b-1/2/3`
(placement), `D4dip-1/2` (tip away from cell 1, count already 4),
`tray-62/72/828` (arm/hand occlusion samples 62.5–82.8).
Loose screw: `loose-c1` (455,440), `loose-c2` (700,1430).

Superseded/duplicate work crops (mis-aimed ROIs, redundant samples)
were left in `snes-02/work/crops/` and are not deliverables.
