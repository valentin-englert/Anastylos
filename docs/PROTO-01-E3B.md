# PROTO-01-E3B — independent test of observer 1.7 + linker 1.7 (preregistered)

Status: preregistered 2026-10-29, before any E3-B footage exists. Owner decision 2026-10-29: evaluate the
current pipeline (observer 1.7.0, linker 1.7 after PROV-01) on new footage with sealed ground truth and fixed
criteria. Nothing is tuned on E3-B. `PROTO-01-E3A.md` stays as registered (frozen observer 1.2, `e057cf4f`);
E3-B does not replace or edit it.

## 1. Question and claim boundary

On a device and arrangement never used in development, without an inventory, names or coordinates, does the
frozen pipeline (a) link deposits to their sources correctly when the capture protocol makes removals observable
(segment A), (b) avoid any incorrect link at natural pace (segment B), and (c) write every automatic link to
Assembly Memory with its inference stated?

Claim if it passes: correct automatic source-to-deposit links under the protocol, and no incorrect automatic
link at natural pace, for one new device, one take per segment, one setup. Not instance re-identification, not
recessed or hidden fasteners, not generalisation beyond this device class.

## 2. Frozen software and configuration

Pipeline commit `7b2606a7` (main); scoring tools commit `a76891bb`. No pipeline file changed between them
(`git diff 7b2606a7 a76891bb` touches only `tools/proto-01/` and `docs/FULL-01.md`). SHA-256 of the files as committed (LF):

| File | SHA-256 |
|---|---|
| `custody-ledger/custody.py` | `ce094cc7debf3afe84cb30651036b123124ea9df8b43bc885d37dad700d60bb8` |
| `custody-ledger/anastylos_custody/observer.py` | `3e83a5b1f9f41fb25e5ee71841c0f8b71766f68ee86d83ca8c4f09f94caa424e` |
| `custody-ledger/anastylos_custody/linker.py` | `50d23d22600b164268c8c60546d4c6d887c2513b9268bc6fea95e48d640aa7da` |
| `custody-ledger/anastylos_custody/proposals.py` | `2fb065d4ad4dd09c45c36ce374f7c2a258a391d1334d64bc8d7694ad4cbc1115` |
| `custody-ledger/anastylos_custody/hands.py` | `50786b7fa1c2fddfd4071327d44d9537d3104765b848342fd798b37b8e47777b` |
| `custody-ledger/anastylos_custody/signals.py` | `2f19e7148d8789912aaf897e840034f6c7bc3aaec0c245f88fb6dd4b7d54fd1f` |
| `custody-ledger/anastylos_custody/session.py` | `1954f828999911fdb481253308459c1231aeeb616767ed5eec59afeab30c3f0f` |
| `custody-ledger/anastylos_custody/device_alignment.py` | `eba85788bd9aee9b85db63ba99436b5bb1c5ec3a921ff4405fd268a7e721ba92` |
| `custody-ledger/anastylos_custody/sheet.py` | `fb80196c23479aa336b9b6d3d67b531ae1abe33fdb28f6942551784d8d2b68ba` |
| `custody-ledger/anastylos_custody/evidence.py` | `7e93aca4c16e71b8272043053f2f732974243e1ee6b844a2165cade89709eea6` |
| `custody-ledger/anastylos_custody/zones.py` | `8ae8940c5274e07ec4689d6c191ca1592159ef18b53d47aaa556287b8485a356` |
| `custody-ledger/custody-config.v15.json` | `763c7219e75d47e4d585a16620def08cbbe5bb7632f8473063bfdf9ae542e57f` |
| `change-detection/harness-config.json` | `3e6a0343f66bb52210586c3c7f32ff59ffa2df1e221049e41b86a84e61372b71` |
| `calibration/calibration-config.json` | `a5f32cb54b57ed49096fa7c5d4a11be6ef2e2b932b6d3d248fcf4be59c17357e` |
| `tools/session/session.py` | `6d86e23b90b20e8a60b0279a2b1b36e2183fd59958ef5d54e8d41c62cb5cca4b` |
| `tools/session/parts.py` | `30bfc0eb36223a9ad3c31c389273537ccc5218e9c11df8ccad5e0a286e4678f1` |
| `tools/session/answers.py` | `03889c594712510994f25eb8f69e22ba20d72e7649b514e49b3be38a3bc1575d` |
| `assembly-memory/anastylos_memory/store.py` | `759dde341cc205c58ac048de5edc0c7877d6eec74fdf1dbd0093104ce84e73a2` |
| `tools/proto-01/score_e3b.py` | `c0423596e451c09a41ef11ce9ed16dda692a351eb2940a215b4dcd9f3b6f37b3` |
| `tools/proto-01/e3b_grid_frame.py` | `aed76fbda05e776ddc65fd2dc200723cd0c95092a7ac3612340dee53813cce8a` |

Versions: observer 1.7.0, hands 0.2.0, proposals 0.4.0, linker 1.7. Tests at freeze: custody 95, memory 29,
session 25, guide 12, reassembly 11, reconstruction 54, proto-01 scorer 6.

If `main` changes any of these files before processing, processing runs in a separate worktree checked out at
`a76891bb` (owner authorisation needed for creating it), never on modified code.

Development history E3-B must be independent of: E2 / E2-D / E2R (SNES front shell, four face buttons),
FULL-01 (SNES controller, complete disassembly), SNES-01. All thresholds were set on these.

## 3. Arrangement and capture (owner, sealed)

**Device.** Not a SNES controller or any part of it. An object the pipeline has never seen, resting on the work
area of `surface_a3`, with 6–8 removable parts that each fit a 26 mm tray cell. No part may be hidden inside
the device or held by a recessed screw (out of scope, §1).

**Capture standard** as in `PROTO-01-NOUV.md` §8b (fixed overhead phone, ISO 400, 1/50 s, 5600 K, fixed focus
0.6, UHD 30, the calibrated `surface_a3` sheet). Bare hands; no tweezers or nails required.

**Segment A — protocol (video `E3B-A.mp4`).** At least 4 parts, removed one at a time:
- before each removal the device is still and both hands are off the sheet for ≥ 2 s;
- after each removal the part goes directly into an empty cell, then both hands leave the sheet for ≥ 2 s;
- one part per cell; no cell adjacent to the previous one.

Controls in segment A:
- **shift:** one part is slid within the device by ≥ 5 mm and left there (no link may cite it);
- **arm rest:** one hand rests motionless over the device for ≥ 5 s (no link may lie inside that interval);
- **pose push:** after the last deposit, the device is pushed by ≥ 10 mm and left (no link may cite a source
  change after the push).

**Segment B — natural pace (video `E3B-B.mp4`).** The remaining 2–4 parts are removed at natural speed, without
pauses. Hands may stay on the device. Include once a **batch carry**: two parts picked up together and put into
two cells. Every part still goes into its own empty cell.

**Blinding.** The owner rolls the part order and target cells (or writes them freely) before recording and does
not show them to the Technical Lead. A witness video (second phone) is recorded as in `PROTO-01-NOUV.md` §4.

**Ground truth (owner, before scoring).** After recording, the owner:
1. runs `py tools\proto-01\e3b_grid_frame.py --video E3B-A.mp4 --t 1 --out gridA.png` (and the same for B), and
   reads each part's source point in sheet mm from the grid (centre of the part before removal);
2. reads each part's removal time in the main video (the first second the part is lifted, ± 1 s);
3. writes one `e3b-actual@1` file per segment (template below), hashes it and sends only the hash.

```json
{"schema": "e3b-actual@1", "segment": "A",
 "steps": [{"part": "S1", "source_point_mm": [182, 140], "removal_t_s": 12.0, "destination": "cell-4"}],
 "controls": {"shift": {"point_mm": [210, 150], "t_s": 40.0},
              "arm_rest": {"from_s": 50.0, "to_s": 56.0},
              "pose_push": {"t_s": 80.0}}}
```

`destination` is `cell-N` or the owner's word for an off-sheet place. Segment B lists the batch carry as two
steps with the same `removal_t_s`.

## 4. Processing (Technical Lead, blind)

1. Verify the §2 hashes (`git show 7b2606a7:<file> | sha256sum`) and the video hashes the owner sends.
2. For each segment, with the frozen code:
   `py tools\session\session.py --video E3B-<S>.mp4 --phase <S> --custody-dir custody-ledger --config custody-config.v15.json --memory <out>\memory --out <out>\<S>`
3. Hash and commit (or ledger) all outputs, including `parts.json`, `registry.json`, `pass2\events.json`,
   `pass2\custody.json`, the memory log and `questions.json`. Only then request the sealed actual files.
4. Score each segment: `py tools\proto-01\score_e3b.py --run <out>\<S> --memory <out>\memory\memory.jsonl --actual e3b-<S>.actual.json --out <out>\<S>.score.json`.
5. Run `tools/guide/package.py` and the FULL-01 invariant script (`session-runs-v21/FULL-01/audit/check_invariants.py`
   logic: every automatic tray link carries `inference`, evidence files hash-verified).

No answers are applied before scoring; questions are scored as raised, not as answered.

## 5. Criteria (fixed now; implemented by `score_e3b.py`)

Per link:
- **Correct:** a true step put a part into the linked cell, its removal time lies inside the linked removal window
  ± 2 s, and its source point lies inside a linked source box grown by 3 mm.
- **Incorrect:** anything else.
- **Unsupported:** a hand covered a linked site at the window's start or end sample (± 0.25 s), or an accepted
  device realignment inside the window happened while a hand covered a linked site (the FULL-01 P1 artefact
  pattern), regardless of correctness.

| Outcome | Segment A | Segment B |
|---|---|---|
| FAIL | any incorrect or unsupported link, a control violated, or an automatic tray link without `inference` | same |
| PASS | 0 incorrect, 0 unsupported, controls clean, correct links ≥ 50 % of the parts that went into cells | 0 incorrect, 0 unsupported (no minimum number of links) |
| STRONG PASS | PASS and ≥ 80 % correct and source recall ≥ 80 % | — |
| NOT PASS | none of the above, e.g. no incorrect link but < 50 % correct | — |
| INCONCLUSIVE | hash mismatch, sheet registration rejected, or a deviation that breaks the script (owner and TL agree before scoring) | same |

E3-B as a whole passes only if A passes and B passes.

Reported regardless:
- per-link verdicts with reasons and artefact flags;
- every `ambiguous` / `unattributed` deposit with its reasons;
- the number of questions per part;
- source recall;
- the batch-carry outcome;
- control results;
- the inference recorded on each automatic link;
- any deviation from this protocol.

A failure is reported as such. A later fix is a new version and needs a new independent test.

## 6. Commitment ledger

| Item | SHA-256 | Committed before |
|---|---|---|
| this document | git commit of this file | any E3-B recording |
| part order / cells (owner) | pending | recording |
| `E3B-A.mp4`, `E3B-B.mp4` | pending | processing |
| processing outputs | pending | requesting the actual files |
| `e3b-A.actual.json`, `e3b-B.actual.json` | pending | scoring |
