# Retrospective custody matcher

Deposit-triggered retrospective source-to-tray matching engine. It reads
`anastylos-observation-v1` events (frozen contract in
[`SCHEMA.md`](SCHEMA.md)) and emits `anastylos-correspondence-v1`
decisions: `PROPOSED`, `AMBIGUOUS`, or `UNKNOWN` per tray addition.

This is a separate runtime from the diagnostic geometry tool in
[`../retrospective_custody.py`](../retrospective_custody.py). That program
remains geometry/evidence inspection only: it does not implement contact,
removal, deposits, or correspondence, and this matcher does not change it.

Standard library only. No network, no SAM inference, no Assembly Memory
writes. `VERIFIED` never appears in output; every proposal carries
`requires_confirmation: true`.

## Run

From this directory (`tools/retrospective-custody/matcher`):

```powershell
# Validate observation files first (Muse A entry point)
py -3.12 validate.py --obs <obs.json> [--obs <more.json>] [--sync <sync.json>]

# Match: one deposit-triggered decision per tray addition
py -3.12 match.py --obs <obs.json> [--obs <more.json>] [--sync <sync.json>] --out <new-or-empty-dir>
```

`--out` receives `report.json` (the full correspondence document);
stdout prints the counts and report path. Exit 0 on success, 2 on any
contract or I/O error. Multiple `--obs` files are merged; event ids must
be unique across all of them.

## CLI examples (synthetic)

```powershell
py -3.12 validate.py --obs examples\proposed-01.json
py -3.12 match.py --obs examples\proposed-01.json --out out-proposed
# counts: 1 deposit, 1 proposed

py -3.12 match.py --obs examples\ambiguous-02.json --out out-ambiguous
# counts: 1 deposit, 1 ambiguous (two viable sources)

py -3.12 match.py --obs examples\unknown-03.json --out out-unknown
# counts: 1 deposit, 1 unknown (cross-camera order unverified)

py -3.12 match.py --obs examples\unknown-03.json --sync examples\sync-ab.json --out out-synced
# counts: 1 deposit, 1 proposed (validated sync resolves the order)
```

These examples validate logic, not physical correspondence.

## How a decision is reached

For each supported, observed tray addition, every observed site becomes
one hypothesis. A hypothesis is `SUPPORTED` only when all of these hold:

1. A verified `present` state for the site is definitely before the deposit.
2. A verified `absent` state follows that present (delayed verification
   after the deposit is allowed).
3. Present and absent are epoch-consistent (same pose epoch, unstated
   epoch, or an explicit observer site link across epochs).
4. A tool/hand interaction possibly overlaps the present-to-deposit
   window; distinct grasp actors need a handoff link.
5. Stated appearance on both sides is compatible (class, drive, marks,
   dimensions within 0.5 mm or 10%).
6. The site is not already attributed to an earlier deposit without an
   observed restock (one site supplies one part per cycle).

Contact alone never proves removal: without verified present-to-absent
evidence the hypothesis cannot pass. Addition alone never proves origin:
without a surviving source hypothesis the deposit is `UNKNOWN`.

Deposit decision: exactly one `SUPPORTED` hypothesis and no
insufficient competitors and no material visibility gap in the transit
window yields `PROPOSED`. More survivors, unobserved alternatives, or an
occluded window yields `AMBIGUOUS`. Zero survivors yields `UNKNOWN`.
A tray removal after an addition flags it `superseded_by` and releases
its attribution for later re-deposits (temporary-deposit chains).

## Reason codes

`DEPOSIT_UNSUPPORTED`, `DEPOSIT_INFERRED`, `SOURCE_PRESENT_UNVERIFIED`,
`SOURCE_ABSENCE_UNVERIFIED`, `SOURCE_STILL_PRESENT`,
`SOURCE_NOT_EMPTIED_LOOSENED_ONLY`, `TEMPORAL_ORDER_UNVERIFIED`,
`CROSS_CAMERA_ORDER_UNVERIFIED`, `POSE_EPOCH_UNMAPPED`,
`MISSING_INTERACTION`, `ONLY_COMPETING_INTERACTIONS`,
`HANDOFF_UNVERIFIED`, `CLASS_MISMATCH`, `APPEARANCE_MISMATCH`,
`DIMENSION_MISMATCH`, `SOURCE_ALREADY_ATTRIBUTED`,
`OCCLUDED_TRANSIT_WINDOW`, `UNOBSERVED_ALTERNATIVE`,
`MULTIPLE_VIABLE_SOURCES`, `NO_SOURCE_OBSERVATIONS`.
Notes (non-blocking): `APPEARANCE_UNOBSERVED`,
`INTERACTION_LOCATION_UNATTRIBUTED`.

## Integration instructions for SNES-02 observations

1. Emit one or more `anastylos-observation-v1` files per the frozen
   [`SCHEMA.md`](SCHEMA.md). Keep runtime observations free of
   ground-truth labels, operator verdicts, and seeded identities:
   `site_ref` values are your track labels, not answers.
2. Use actual PTS values in `time` and `evidence.pts_ms`. Use one
   `clock_id` per camera; never hand-align two cameras by eye.
3. If source and tray evidence come from different cameras, supply an
   `anastylos-sync-v1` manifest from an independent measurement (clap,
   flash, shared clock) with direction, uncertainty bounds, and a
   `basis_ref`. Without it the matcher abstains on ordering.
4. Record `pose_epoch` on source observations whenever the device moves;
   add `pose_change` events with explicit `site_links` for sites you
   tracked across the move. Untracked cross-epoch sites abstain.
5. Declare material blindness as `visibility_gap` events (occlusion,
   missing frames, out-of-frame) instead of bridging it silently.
6. Mark uncertain tray changes `supported: false`; the matcher will
   answer `UNKNOWN` rather than guess from them.
7. Validate before delivery: `validate.py --obs ... [--sync ...]` must
   print `VALID`. Real observation files must be consumable with no
   matcher change; report contract gaps instead of working around them.

## Missing upstream observations (honest list)

No module currently produces these automatically; for SNES-02 they are
manual annotation until stated otherwise:

- Verified source present/absent timelines per site (change-detection
  gives planar regions, not tracked site states).
- Timestamped tool/hand interaction candidates with contact verification.
- Supported/unsupported tray addition and removal events per cell.
- Independently validated cross-camera clock offsets with uncertainty.
- Device pose epochs and cross-epoch site tracking.
- Declared visibility gaps (occlusion, missing frames).
- Component appearance per source and per deposit (class, drive, marks,
  dimensions) from a cited recognizer or measurement.

## Test

```powershell
cd tools\retrospective-custody\matcher
..\..\..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

22 deterministic tests: the 12 required behavior cases (unambiguous
removal, touch-without-removal, loosened-without-removal, two sources
and one destination, two-and-two assignment, delayed visibility,
temporary deposit, handoff, uncertain timestamps, cross-camera views,
pose change, identical screws), plus deposit-support, occlusion,
never-verified, CLI integration, duplicate-id rejection, and validator
negative cases. Synthetic-only; see above.
