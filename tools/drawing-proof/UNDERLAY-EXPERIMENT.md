# Underlay experiment UL-1 (pre-registered, not yet recorded)

Question: does a different underlay make outline and openings more reliable than the white sheet?

## Conditions (same camera stand, same light, hands out of frame ≥ 2 s per shot)

| ID | Underlay inside the dashed work area (markers stay visible) | Expected effect |
|---|---|---|
| W | white sheet (today) | baseline: grey cast shadows, printed lines nearby |
| C | saturated matte colour absent from the parts (blue or magenta foam rubber or paper) | shadows keep the hue (only darker), so part vs background separates by colour; **through-openings show the underlay colour**, which tells a real hole from a dark recess |
| B | backlight: a tablet or LED tracing pad showing plain white under one thin paper sheet | silhouette and through-holes become bright, independent of part colour and shadows (outline and openings only, no surface detail) |

Optional C2: the same part on two different colours without moving it (slide the colour sheet under it, or use the
tablet showing two colours): a pixel that changes colour between the two shots is background.

## Parts

Back shell exterior up, PCB component side up (cable laid away from the board), front shell interior up. One
shot per part and condition: 9 shots (12 with C2).

Added 2026-11-02 (owner's objects, never seen by any tuned step; same three conditions, one shot each):
open-end wrench (glossy metal: reflections, thin jaws), Ender 5 display (flat electronics with ribbon cable),
pistol fidget toy (a multi-part assembly: also a candidate for a second full disassembly). Underlays available:
coloured foam rubber (condition C) and an LED tracing pad (condition B) with the A4 ArUco sheets around it.

## Measured, fixed before recording

Frozen r4.1 Muse box → SAM and `quality.plausibility`; visual check of outline and openings, each separately;
for C and B additionally a deterministic colour/brightness key (no model) as the offline comparison. Report all
shots. Known risks: colour cast on glossy parts, white-balance drift (lock exposure/white balance if the phone
allows), parts of the same colour as the underlay, foam thickness (2–3 mm) lifts parts above the sheet plane;
the homography stays on the sheet, so record the thickness.
