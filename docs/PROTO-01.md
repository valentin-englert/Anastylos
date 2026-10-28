# PROTO-01: protocol capture for the first physical correspondence

Status: protocol ready, not executed. Owner: Technical Lead (verifier).
Prepared 2026-10-28 against `main` @ `027ab2b0`. Contract context:
`ARCHITECTURE.md` → "Correspondence contracts and owner decisions".

## 1. Question and claim boundary

Does the existing, unchanged custody observer produce a correct automatic
source-to-cell link when the capture follows a protocol that exposes the
source transition (device at rest, hands-off pauses, one part per cell)?

The only variable relative to CUSTODY-01 (SNES-01, 0/5 sources) is the
capture protocol. Software, configuration and thresholds stay frozen.
A positive result supports "automatic source-location recovery under
protocol P with N interventions". It does not support free, natural
disassembly, instance identity of identical parts beyond their custody
chain, or generalization beyond this unit.

## 2. Frozen software (verify before every run)

The observer is not on `main`. It runs read-only from the sibling
worktree `C:\Users\HP\Documents\Anastylos-custody`, branch
`feat/custody-ledger`, committed unchanged as `496e323f` (2026-10-28),
with `C:\Users\HP\Documents\Anastylos\.venv` (Python 3.12.10,
OpenCV 4.14.0, NumPy 2.5.4). Checked 2026-10-28: 20/20 tests pass.

The hashes below are of the working files in that worktree. Nine of
them had CRLF line endings, which `.gitattributes` stores as LF, so a
fresh checkout of `496e323f` elsewhere differs byte-wise (the canonical
config hash does not change). Run PROTO-01 from that worktree; anywhere
else, identify the code by the commit instead of these file hashes.

| File | SHA-256 |
|---|---|
| `custody-ledger/custody.py` | `ce094cc7debf3afe84cb30651036b123124ea9df8b43bc885d37dad700d60bb8` |
| `custody-ledger/anastylos_custody/cli.py` | `b01675fa352ad608ccd4f86942850303dc2062d437046a5dcb1dff883b4f5889` |
| `custody-ledger/anastylos_custody/config.py` | `529cc72376dd464953432b4c7cbb7115b28c518d8dea0d78153cfecae28e3b27` |
| `custody-ledger/anastylos_custody/device_alignment.py` | `c8c21ace4fd7da3366ca4f782dc0e72b8d080a70cc5b273cb170052c0c8f42ec` |
| `custody-ledger/anastylos_custody/evidence.py` | `8f4a04d3bd0112f613b8ee0ea7caab38b7a366a367ca3ae0e21d65bd9f45711a` |
| `custody-ledger/anastylos_custody/footage.py` | `48d75c7e2e541af55124062df475b7d9ccdaf61138bd4e748b278bffb722be7a` |
| `custody-ledger/anastylos_custody/linker.py` | `ecdda3fc5c1470a7e5582c64021b3d4268300d30cca669b3466793d9418cfacb` |
| `custody-ledger/anastylos_custody/observer.py` | `aacaf3db5d512412b350bfdea7b98cbec4e3320e6a35a25c4d60c7cf796cb45a` |
| `custody-ledger/anastylos_custody/scoring.py` | `ddb0db7acb4f43113a5c45d90bf820b1ea1a22b2c66b93780a6d1efc14d40976` |
| `custody-ledger/anastylos_custody/session.py` | `3db95ef228dbe84575618f8d1a4e2e222f695e6756ce345e39b74b402a00d6a8` |
| `custody-ledger/anastylos_custody/sheet.py` | `8716e8b15dabbe483ddc0d06f837536ba5aa493f8a0dbcc75f973e4e6d211ea3` |
| `custody-ledger/anastylos_custody/signals.py` | `be7e2d0b60429a837d0f9a62c31f13e2b69940b99f6fc3ded877f0aab8a32804` |
| `custody-ledger/anastylos_custody/zones.py` | `d61706c42a3c7dc09da2acc5cc7f4182fb8888e4f3e29448403c18e1a5f2efab` |
| `custody-ledger/anastylos_custody/__init__.py` | `1c0d50662522792da4e73b56ce99fd2fe2468caebb4b5dbe3f8595ace356a928` |
| `custody-ledger/custody-config.json` (file) | `ba84253df6e1b84d1daaef5c3bc74251456ead4e2c741d2cbfb3c8e17731a1f6` |
| `custody-config.json` (canonical) | `10b58c2dbca521edbc1a150988791f2cbc807f37a5de14f03c1d64cb950304e7` |
| `change-detection/harness-config.json` (file / canonical) | `3e6a0343…` / `a2308f66af38385a425e8839abd15c1e5253ffb9728c2d7ff57bb1fb15885812` |
| `calibration/calibration-config.json` | `a5f32cb54b57ed49096fa7c5d4a11be6ef2e2b932b6d3d248fcf4be59c17357e` |
| `change-detection/detect.py` | `73efd8d55351c536f96dbdcfe5b66254bc0103c269267193c621ac70b9a594ba` |

Any mismatch: stop and report. No parameter, threshold or code change
for this experiment.

## 3. Materials (owner)

- The SNES-style controller, back-shell screws in place.
- A simple jig on the A3 marker sheet (`surface_a3`) that keeps the
  controller from moving and covers no marker.
- Empty, numbered tray cells of the calibration kit.
- One phone on a fixed overhead stand, **4K 30 fps** (SNES-02's 720p
  overhead gave ~7 px per screw). Diffuse light, all markers in frame.
- Screwdriver, calipers, UV marker pen, UV flashlight, one die.

## 4. Sealed ground truth (owner, before recording)

1. Photograph the controller in the jig and label every screw hole
   `H1`…`Hn` on that photo (positions only, no answers).
2. Measure each screw (length, head diameter, thread) with calipers.
3. Mark each screw with a unique UV code on the shank (1, 2, 3 … dots),
   invisible under normal light.
4. Roll the die to fix the removal order and which cells receive which
   removal; choose the hole for control C1 (still present when touched).
5. Write `proto01.sealed.json` with: hole → UV code, screw measurements,
   scripted order of holes and target cells, control definitions,
   operator name, date. Compute its SHA-256 and send only the hash to the
   Technical Lead before recording. Keep the file away from the
   processing lane until scoring.

After recording: read each cell's UV code under UV light, photograph
each cell, and add the result to a second sealed file (`proto01.cells.json`,
hashed the same way).

## 5. Pre-flight (mandatory, same setup, before the real take)

Record 15 s of the empty, static setup and run `custody.py observe` on
it with the frozen configuration. Required: sheet registration accepted
and no zone event. If registration is rejected (known risk: marker-10
corner bias, see `ARCHITECTURE.md`), report to the Technical Lead before
recording; do not tune.

## 6. Capture script (~3–4 min, one continuous take)

1. 5 s static, hands out of frame.
2. For each scripted hole in order: hands fully off device and tray for
   ≥2 s → unscrew and remove one screw → carry it → put it alone into its
   scripted empty cell → hands fully off device and tray for ≥2 s.
   "Off" means out of the device and tray area, not merely away from the
   hole: in SNES-02 the sites were covered during and often after every
   interaction.
3. Control C1 (inserted at its scripted position): engage the screwdriver
   in a hole that still holds its screw, turn back about a quarter turn,
   withdraw; screw stays; hands off ≥2 s.
4. Control C2 (after at least one deposit): move one deposited screw from
   its cell into an empty cell; hands off ≥2 s.
5. 5 s static at the end.

Log any deviation from the script immediately (time and what happened).

## 7. Processing (producer lane, no access to sealed files)

Copy the original MP4 read-only to `C:\ANASTYLOS\incoming\`, record its
SHA-256, write outputs to `C:\ANASTYLOS\data\proto-01\<run>\`.

```powershell
# from Anastylos-custody\custody-ledger, with Anastylos\.venv python
python custody.py observe --video <proto01.mp4> --inventory <proto01_inventory.json> --out <run>
python custody.py link --events <run>\events.json --out <run>\custody.json
```

The site inventory (`custody-site-inventory@1`) uses the hole labels and
positions from the owner's labelled photo, read from the first static
frame. It is disclosed setup, recorded as an intervention, and must be
written before `observe` runs.

## 8. Scoring (Technical Lead, after the producer has frozen its outputs)

- A link is a `candidate_temporal_exclusive` proposal site → cell. It is
  correct if the UV code found in that cell belongs to that hole.
- Order check: observed `site_changed` events sorted by time must follow
  the scripted hole order.
- Control C1: no `site_changed` for the C1 hole before its scripted
  removal; control C2: no link that attributes the receiving cell to a
  new source.
- Report separately: correct, incorrect, ambiguous and unattributed
  links; sources detected per hole; control outcomes; interventions;
  protocol deviations.

## 9. Acceptance criteria (proposed preregistration thresholds)

| Outcome | Criterion |
|---|---|
| PASS | ≥1 correct link, 0 incorrect links, both controls clean |
| STRONG PASS | PASS and correct links for ≥3 holes |
| FAIL | 0 `site_changed` events, any incorrect link, or a control producing a removal or a new-source link |
| INCONCLUSIVE | hash mismatch, registration rejected, or a logged protocol deviation that breaks the script |

At most two takes with identical frozen software. A third take needs
owner approval. After two FAIL results the protocol path stops: the
MVP is then built as an operator-assisted slice and labelled as such,
and the failure is reported as the result.

## 10. After a PASS

Operator confirms the proposed links using the evidence crops. Then the
existing chain: `custody.py export-pairs` → `change-detection/detect.py
run` → `custody.py bind` → `assembly-memory/memory.py import --region` →
`memory.py record tray-link --confirmed-by` → `assembly-reconstruction/
reconstruct.py build`. This chain was last executed on SNES-01 with an
operator-declared source (see
`C:\ANASTYLOS\data\snes-01\custody-v1\COMMANDS.md`).

Keep every run, including failed ones, and write the experiment card
in the format of research log §9.
