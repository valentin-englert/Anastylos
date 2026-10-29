# PROTO-01-E2R — memory-guided reassembly of the E2 parts (preregistered)

Status: preregistered 2026-10-28, before any E2R take. Owner directive
2026-10-28 (P0: observer v1.1 and E2-R, proving the return path).
Disassembly evidence: [`PROTO-01-NOUV.md`](PROTO-01-NOUV.md) E2 (§10–§12).

## 1. Question and claim boundary

Does memory-guided reassembly put every part recorded in the E2 Assembly
Memory back into its recorded source well, and does the system flag a
deliberate wrong placement, as checked from an overhead video by custody
observer v1.1 and `tools/reassembly/returns.py`?

Claim if it passes: for four parts with an operator-defined well
inventory, the return path is observed and checked against memory. Not
claimed: part identity (the check follows temporal custody, not
appearance), discovery of wells, natural hands-only capture.

## 2. Software and version boundary

- E2 (disassembly) stays a **v1.0** result: commit `496e323f`, hashes in
  `PROTO-01.md` §2. It is not re-scored.
- E2R runs **observer v1.1**: branch `feat/custody-ledger`, commit
  `86122666`, worktree `Anastylos-custody`. Changed files against v1.0:
  `observer.py` `ea2b014fefc9abf2a2dffc6df94d7a98fec715505ae576516c41800cf58a4895`,
  `session.py` `989f194f3fab838643f2fc2cf14cc7eb14b028efd2a260ec46d7025bbb5005d3`,
  `zones.py` `16bc1a16d04e61b96426b3a3c8114b8b925d873feadc56552ef7e504e132345c`;
  all other files and both configs as in `PROTO-01.md` §2. 26/26 tests pass.
- v1.1 regression on the E2 video (same inventory): all 26 events and all
  custody links identical to v1.0; the only difference is
  `provenance.observer_version = "1.1.0"`. Outputs in
  `C:\ANASTYLOS\data\proto-01\E2\regression-v1.1\` (`events.json`
  `af0ef7d4d4fe4893b54ff88a5c827884b162ee01f4176f742cfb9352c34190dd`,
  `custody.json` `22cd895f96deff5c21455e7a032f68247881571268a4d219b2d5a6a843e0a432`).
- Return checker: `tools/reassembly/returns.py`
  `67aac938beec7da358493231190f7a8fee66d433ad1031f62e1d669aba900804`
  (7/7 tests). It refuses event logs that are not v1.1 with declared
  start occupancy. Results of different observer versions are never
  combined.

## 3. Inputs fixed before the take

| Item | SHA-256 |
|---|---|
| E2 memory `C:\ANASTYLOS\data\proto-01\E2\memory\memory.jsonl` | `867fe05d4fc3e302fa1d12e7414e5066cc64b4087a023689d35437a500b0568a` |
| Plan `C:\ANASTYLOS\data\proto-01\E2R\plan.json` (reverse removal order) | `62b8087b5aa23f79e62a780dc2dc7fd85753e22a53fc855d862f23b56c2f62b8` |
| Inventory `C:\ANASTYLOS\data\proto-01\E2R\E2R.inventory.json` (E2 well rectangles; wells `empty`; cells 4, 9, 1, 6 `occupied`) | `d56188ee9fd4cea3441e09f2a18c998009e325947759acb54e86da569b46eeea` |
| Script roller `tools/proto-01/roll_e2r_script.py` | `0c4cc9ccfdf0e53bd56c1a4a249df65e66f9dad30a69fcaeaf1145df0c071c07` |

Plan (public; the operator follows what the system says):
cell-4 → W-RED, cell-9 → W-GREEN, cell-1 → W-BLUE, cell-6 → W-YELLOW.

## 4. Controls (rolled and sealed by the owner)

- **C5:** one part is lifted from its cell and put back into the same
  cell. Must not produce a well placement; at most a `back_in_a_cell`
  return.
- **C6:** one part is first put deliberately into a wrong, still empty
  well, hands away ≥ 3 s, then moved to its home well. The wrong placement
  must never be reported `consistent_with_memory`.

Sealing, witness phone, `actual` file and roles as in `PROTO-01-NOUV.md`
§3–§4 (`roll_e2r_script.py --plan … --out <stick>`, then
`record_e2_actual.py --experiment E2R`). No spoken labels.

## 5. Pre-flight

A 15 s static clip with all four parts in their cells, run with v1.1 and
the E2R inventory: registration with 7 markers, 0 events, no
`pose_epoch_break`, device realignment none or accepted. If the shell
moved since E2, the inventory is invalid: stop and re-derive the well
rectangles from the new static clip (new hash, logged here).

## 6. Processing (Technical Lead, before seeing the sealed files)

`custody.py observe --inventory E2R.inventory.json`, then
`returns.py verify --memory <E2 memory> --inventory <E2 inventory> --events <run>\events.json`.
Hash and commit all outputs before requesting the sealed files.

## 7. Scoring (fixed now)

- Unit: each `candidate_temporal_exclusive` return link.
- **Correct:** the actual record contains that movement (source zone →
  destination zone). **Incorrect:** contradicts the actual record.
- **Memory check correct:** every final well marked
  `consistent_with_memory` holds, on the owner's end photo, the part that
  memory says came from it.

| Outcome | Criterion |
|---|---|
| PASS | ≥ 1 correct link, 0 incorrect links, C5 clean, C6 wrong placement not reported consistent, no final well marked consistent that the photo contradicts |
| STRONG PASS | PASS, ≥ 3 correct links, C6 reported `MISMATCH_with_memory`, all four home wells end `consistent_with_memory` |
| FAIL | any incorrect link, a C6 wrong placement reported consistent, or a consistent mark contradicted by the photo |
| INCONCLUSIVE | hash mismatch, registration rejected, shell moved without a new inventory, or a deviation that breaks the script |

At most two takes; criteria are not changed after results are seen.

## 8. Ablauf für den Operator (Deutsch)

1. Frontschale nicht bewegen. Die vier Tasten liegen in Zelle 4, 9, 1, 6.
   Kamera wie bei E2 (ISO 400, 1/50 s, 5600 K, MF 0,6, UHD 30).
2. Statischer Clip 15 s, Hände raus → `C:\ANASTYLOS\incoming\E2R-static.mp4`.
3. Skript würfeln (eigenes Terminal, Stick als `E:`):
   `…\python.exe …\tools\proto-01\roll_e2r_script.py --plan C:\ANASTYLOS\data\proto-01\E2R\plan.json --out E:\`
   Nur den SHA-256 schicken; auf „Hash committet“ warten.
4. Take: Zeugen-Handy, dann Overhead. 10 s statisch. Jeder Schritt mit
   der Hand (Pinzette erlaubt), Hände danach ganz raus, ~2 s. 5 s statisch.
5. Original nach `C:\ANASTYLOS\incoming\E2R.mp4`; Zeugen-Video, `actual`
   (`record_e2_actual.py --experiment E2R`) und Endfotos wie bei E2.

## 9. Pre-flight result (2026-10-29): pass

`E2R-static.mp4` (`5e204c41c43beb149cb095b022c6faf2895252ce1d917320f23222162d1087fd`,
16.1 s) with observer v1.1 `86122666` (file hashes as in §2 verified) and
the E2R inventory (`d56188ee…`): registration valid with 7 markers, RMS
0.307 mm; 0 events; 14/14 drift fits; no device realignment, no
`pose_epoch_break`; all four wells `observing` with declared start `empty`.
`static\events.json` `6c77a92684b96adb81f24e57999397031edae2a953a474eccc282a91b7768f5f`.

Shell position against E2 (the observer cannot see this, it compares
with the clip's own first frame): phase correlation of the rectified
static frames of E2 and E2R gives a shift of (−0.14, +0.03) mm; the E2
well rectangles sit on the now empty wells. The E2 inventory geometry
stays valid; no new baseline needed.

## 10. Commitment ledger

| Item | SHA-256 | Committed before |
|---|---|---|
| `nouv-E2R.script.json` (rolled with `roll_e2r_script.py` `0c4cc9cc…`, plan `62b8087b…`) | `4d74a37f8dbe88863bc5d9e6a2addcda140104ec9706ced8b7f189c78cc515df` | E2R take |
| E2R outputs: `run1\events.json` / `run1\returns.json` | `dd6d2157eaffa75402aa8ec499c6bf99d2fee44b7c3e04a7af322424d33c601d` / `e2a2d84cdcd22249ca3485df0eb81550bb0255265adf20d02f090b64f8633233` | ground-truth reveal |
| `nouv-E2R.actual.json` | pending | scoring |

## 11. Deviation before scoring (2026-10-29)

Observer output of the take frozen first: `E2R.mp4`
`5a489b646ece66d780ed73314ee36a72fc86e374bc6ca4fbc3f2c8064cc5a301` (96.9 s),
`run1\events.json` `dd6d2157eaffa75402aa8ec499c6bf99d2fee44b7c3e04a7af322424d33c601d`
(observer v1.1 `86122666`, hashes of §2 verified; 81/81 drift fits).

The preregistered `returns.py` (`67aac938…`) refused the log because it
required a start-occupancy declaration for every zone, including the
eight tray cells that the inventory leaves undeclared on purpose (they
start empty, and undeclared means empty). This is a defect of the
checker, found before any return result was seen. Fix: sites and every
plan cell must be declared; other cells may stay undeclared. New
`returns.py` `6d4fd7db08e2f802b749220d5e1c716ae0528a67700b57ae3b25a0d299532259`, test updated (7/7). Scoring criteria unchanged.

Second checker defect, also found before any return result was seen: it
read time-window fields from every event, so `reference_set` events
(no window) crashed it. Fix: events that are neither a departure nor an
arrival are skipped before reading them. New `returns.py` `4e9f61d2db7520dc32bd6763c5522433f0805adc180b1ddf4e2591afdfe4f5a6`,
test added (8/8).

## 12. Take 1 result (2026-10-29): NOT PASS — 0 return links

Processed blind; outputs committed before any reveal (§10). Registration
7 markers, RMS 0.29 mm; 81/81 drift fits; no realignment; no sampling gap.

| Measure | Value |
|---|---|
| Cell departures (`vacated`) | 4: cell-4 (12.1–25.3 s), cell-9 (43.5–46.9 s), cell-6 (55.9–64.5 s), cell-1 (67.5–70.1 s) |
| Other cell event | cell-6 `changed_while_away_from_start` 79.1–90.3 s |
| Well placements | 0 — all four wells `inventory_not_observable` |
| Return links | 0 correct, 0 incorrect; C5/C6 not evaluable |

No criterion of §7 for FAIL or INCONCLUSIVE applies; PASS needs at least
one correct link, so the take does not pass.

Cause (diagnosed by tracing the frozen observer, not by ground truth):
the first 1.2 s of the recording contain small whole-frame flicker without
hands (motion fraction 0.7–2.9 % at 0.32 s and 1.12 s, likely the phone
settling after recording start). Sites get only 2 s after `inventory_t_s`
(= 0 s) to reach a first clean reference (`init_tolerance_s`); none did,
so every well stayed unobservable. Tray cells have no such limit, which is
why the four departures were seen.

Proposed take 2 (the protocol allows two takes; needs owner approval
before recording): same frozen software and the same sealed script, with
a new inventory that only moves `inventory_t_s` to 5.0 s (inside the 10 s
static start); its hash is committed before the take. Take 1 stays
reported as above and is not re-run with the new inventory. The sealed
files of take 1 stay sealed until take 2 is processed.

## 13. Take 2 preregistration (owner approved 2026-10-29, before recording)

Same frozen software (§2), same sealed script (`4d74a37f…`), same
criteria (§7). Only change: inventory
`C:\ANASTYLOS\data\proto-01\E2R\E2R.take2.inventory.json`
`edb3deb4e8709fea2a10d782f0d41bac3067e44f7945076aa76d5fa8799f028d`,
identical to the take-1 inventory except `inventory_t_s` = 5.0 s and the
basis note. Checker `tools/reassembly/returns.py`
`4e9f61d2db7520dc32bd6763c5522433f0805adc180b1ddf4e2591afdfe4f5a6` (as
fixed in §11). Video name `E2R-take2.mp4`. Take 1 is not re-run.

Owner decision (2026-10-29, before processing): instead of a new
recording, take 1's video `E2R.mp4` is re-analysed with the take-2
inventory (`edb3deb4…`). Reported as **"take 1, re-analysis after a
setup failure"**, not as an independent take: the change was decided
after take 1's wells were seen to be unobservable, but before any return
result or ground truth was seen. Software, checker, script and criteria
unchanged. The flicker itself is not fixed; the later `inventory_t_s`
only avoids it.
