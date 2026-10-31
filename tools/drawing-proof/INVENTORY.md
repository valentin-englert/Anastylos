# Drawing proof — inventory of decisions (baseline r1)

Classes: **G** fixed general procedure · **A** computed from the image · **M** chosen by a model (none yet) ·
**O** operator-given (or would be) · **S** tuned for this controller/frame by the developer.

| Decision | Value in r1 | Class | Where it should come from in the product |
|---|---|---|---|
| Frame choice (P3 f300) | picked by the developer after viewing frame-hunt crops | S | quality pre-sort (A) → Muse picks from ≤ 6 candidates with purpose (M) → operator may veto (O) |
| Sheet registration, homography | custody sheet reader, `custody-config.v15.json` | G + A | unchanged |
| Region of interest (125,100,175,90 mm) | drawn by the developer from the rectified page | S | operator tap/box or Muse box on the rectified page (O/M); gate `INCOMPLETE_VIEW` catches a too-small box |
| Rectification scale 10 px/mm | chosen once | G | fixed |
| Otsu level on L* (136 on f300) | computed per image | A | unchanged |
| Closing kernel 15 px | chosen once | G (weakly S) | fixed |
| Shadow rule: b* ≥ 135, local sd ≤ 4, within 80 px of the seed rim | thresholds measured on f300 (shadow b* 137–138, sd 0.9–2.7) | **S** | biggest transfer risk; replace by a lighting-independent rule or by SAM; until then gate `SHADOW_DOMINANT` |
| GrabCut band 12 px, 5 iterations, seed from Otsu | chosen once | G | fixed |
| Outline tolerance 0.2 mm (Douglas–Peucker) | chosen once | G | fixed; measured deviation reported |
| Light regions L* ≥ 175, min 15 mm² | set on f300 | S (weak) | generic for light inserts on darker parts only |
| Dark regions L* ≤ 55, solidity ≥ 0.85, ≥ 100 mm² | set on f300; yields nothing there | S | not used for transfer |
| Holes: enclosed, ≥ 8 mm², circularity ≥ 0.6 | set on f300 | S (weak) | generic for round openings |
| Rubber pad box (193.8,129.5)–(228.5,155.5) mm | drawn by the developer | **O-equivalent, S** | operator or Muse box per detail; removed from the transfer baseline |
| View side "interior", orientation "as photographed" | asserted in the title by the developer | S | operator confirmation (capture-gap kind `orientation`) |
| Layout margins, line roles | fixed | G | fixed |
| Edge-fit Canny 40/120 | chosen once | G | diagnostic only, not a correctness measure |
| Quality gates (`quality.GATES`) | set before the transfer runs | G | fixed per revision |

Two identical runs show determinism, not transfer. Transfer is tested in `specs/transfer_plan_r1.json`, committed
before any of its targets was run.

## Independent reference contour

Edge support is not correctness. `quality.reference_comparison` scores a candidate mask against a reference
mask drawn by someone who did not tune the method (the owner), on the same `rectified.png`: missed rim share,
spurious rim share, extra and missed area share (all against the reference area), rim distance p95. Tolerance is
the 0.2 mm simplification tolerance in sheet-plane mm, not a part dimension. Reference masks: not yet drawn.
