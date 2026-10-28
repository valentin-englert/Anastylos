# PROTO-01: protocol capture for the first physical correspondence

Status: protocol ready, not executed. Owner: Technical Lead (verifier).
A variant without UV instance coding is preregistered separately in
`docs/PROTO-01-NOUV.md`; neither replaces the other.
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

The source sites are initialized manually: an operator-defined site
inventory (hole positions) is written before the observer runs. Every
automatic result is therefore reported as "automatic link given an
operator-defined source-site inventory". PROTO-01 does not test
autonomous site discovery, and the inventory counts as an intervention.

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
- A simple fixture on the A3 marker sheet (`surface_a3`) that keeps the
  controller from moving and covers no marker: nail pegs in the MDF as
  in `docs/PROTO-01-NOUV.md` §8a. Nothing is taped or stuck to the
  paper.
- Empty, numbered tray cells of the calibration kit.
- One phone on a fixed overhead stand, **4K 30 fps** (SNES-02's 720p
  overhead gave ~7 px per screw). Diffuse light, all markers in frame.
- Screwdriver, calipers, one die, and a ground-truth marking kit: UV
  marker pen and UV flashlight, or the fallback in §4.3 (nail polish or
  paint markers in distinct colours).
- A USB stick or a phone to hold the sealed files off the workstation.

## 4. Sealed ground truth (owner, before recording)

The ground truth must identify each screw physically, be fixed and
sealed before recording, stay unavailable to the processing lane until
its outputs are frozen, and be readable after the session without the
video.

### 4.1 Coding and reinstalling the screws

1. With all screws in place, photograph the controller in the jig and
   label every hole `H1`…`Hn` on the photo (positions only). This photo
   may go to the processing lane.
2. Remove all screws off camera. Keep each next to a label of its
   original hole.
3. Measure each screw: length, head diameter, thread diameter. If all
   agree within 0.2 mm and look identical, the assignment in step 5 is
   random. Otherwise each screw goes back into its original hole.
4. Give each screw a unique code on the last 2 mm of the thread (tip
   end): UV ink dots (1, 2, 3 …) or, without UV ink, the fallback in
   §4.3. The code must be hidden while the screw is installed. Let it
   dry.
5. Assign screws to holes: roll the die (or shuffle) for identical
   screws; otherwise use the original holes.
6. Reinstall each screw in its assigned hole, snug with the
   screwdriver as before, not tighter.
7. Photograph the installed back again (codes hidden) to document that
   every hole is filled.

### 4.2 Script and sealing

1. Roll the die for: the removal order of the holes, the empty target
   cell of each removal (one cell each), the hole for control C1
   (touched while its screw is still in), and the cell from which C2
   moves a screw.
2. Write `proto01.sealed.json`: hole → code, measurements, assignment
   rule used, scripted order and target cells, controls, operator,
   date, and a `nonce` of at least 32 random characters. Without the
   nonce, the few possible hole → code permutations could be recovered
   from the hash.
3. Compute the hash: `Get-FileHash proto01.sealed.json -Algorithm SHA256`
   (PowerShell) or `certutil -hashfile proto01.sealed.json SHA256`.
4. Send only the hash to the Technical Lead, who commits it to §11
   before recording.
5. Move the file off the workstation (USB stick, phone or an encrypted
   archive) and delete the copy on the PC: agents on this PC can read
   any local file, so a local copy is not sealed. Keep a paper copy.

### 4.3 Fallback without UV ink

Use distinct, durable colours (nail polish or paint marker; one colour
per screw, or one to four dots of one colour) on the thread tip, as in
§4.1 step 4. This is acceptable for PROTO-01 because:

- the custody observer and linker never use part appearance; they
  decide from site and cell changes over time, so a visible mark cannot
  steer the automatic result;
- the code is hidden while the screws are installed, so the site
  inventory, written from the first static frame, cannot encode it;
- the hole → code mapping stays sealed, and the codes are read
  physically after the session.

Report it as "visible code on thread tip" instead of UV. Do not use
this fallback in later experiments whose methods read appearance
(matcher appearance fields, Muse vision).

If no marking is possible at all: sealed script plus an independent
witness camera (a second phone close to device and tray whose video is
never given to the processing lane). The Technical Lead annotates the
witness video before opening the custody outputs. This is weaker
(visual, and dependent on script compliance) and is labelled
"script + witness ground truth".

### 4.4 After recording

Read each cell's code (UV light or by eye), photograph each cell, and
write `proto01.cells.json` (cell → code, with its own nonce). Hash it
the same way, send the hash, and store the file like the first one.

## 5. Pre-flight (mandatory, same setup, before coding the screws)

Pre-flight clips are setup checks and are never scored. The Technical
Lead runs them with the frozen software.

**Pre-flight A (registration).** Record 15 s of the static setup with
all screws in place and hands out of frame. Run `custody.py observe`
without inventory. Pass: exit 0, `registration.report.valid` true,
0 events, all camera-drift fits accepted. Record `n_markers`,
`reprojection_rms_mm` and whether earlier attempts failed.

If registration becomes valid only after attempts with all markers
failed, the setup depends on a marker being hidden. Fix it physically
(board lying flat, camera perpendicular and centred, no glare on
markers) and repeat. Never tune software; covering a marker on purpose
needs owner approval and is reported.

**Pre-flight B (signal check, one screw).** Same setup, about 30 s:
static, hands off; remove one screw with the pauses of §6 and put it
into an empty cell; static. Run `observe` with a one-hole inventory,
then `link`. Pass: a `site_changed` event for that hole and an
`appeared` event for that cell. Otherwise change only lighting, camera
height or framing, log the change and repeat. Reinstall the screw
afterwards. After pre-flight A, do not touch camera or sheet; if either
moves, repeat pre-flight A.

**Verification of the pre-flight path on existing footage**
(2026-10-28, frozen software, first 15 s of each recording, outputs in
the Technical Lead's scratchpad):

| Clip | Result |
|---|---|
| SNES-02 CAM1 (720p overhead) | valid at frame 0 with 7 markers, RMS 0.406 mm, 0 events, 13/13 drift fits |
| SNES-01 (4K overhead) | 12 attempts with all markers rejected (1 RANSAC outlier, max error 1.50–1.58 mm > 1.5 mm); valid at 2.55 s with 6 markers (15 not used), RMS 0.36 mm, 0 events, 13/13 drift fits |

Whether registration passes therefore depends on the physical setup;
pre-flight A decides it for the PROTO-01 setup.

## 6. Capture script (~3–4 min, one continuous take)

1. 10 s static, hands out of frame (registration is searched only in
   the first 15 s).
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
- State every automatic result as "automatic link given an
  operator-defined source-site inventory" (§1), and name the ground-truth
  variant used (UV code, visible code on thread tip, or script + witness).

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

## 11. Commitment ledger

SHA-256 of sealed files, committed before the step they protect.

| File | SHA-256 | Committed before |
|---|---|---|
| `proto01.sealed.json` | pending | recording |
| `proto01.cells.json` | pending | hand-off of custody outputs to scoring |

## Anhang A: Checkliste Aufbau und Aufnahme (Operator)

Zeitbedarf grob: Aufbau 30–45 min, Vorab-Tests 15 min plus Auswertung,
Ground Truth 30–45 min, Aufnahme 5 min.

**A. Material**
- [ ] Controller, alle Rückschrauben eingesetzt
- [ ] A3-Markerbogen `surface_a3` (Marker 10–16, Tray-Zellen 1–12)
- [ ] Nagel-Anschläge im MDF (`docs/PROTO-01-NOUV.md` §8a), verdecken keinen Marker und keine Zelle; nichts auf das Papier kleben
- [ ] Smartphone mit starrem Stativ über dem Bogen, mehrere GB frei
- [ ] Diffuses Licht ohne Spiegelung auf den Markern
- [ ] Schraubendreher, Messschieber, Würfel
- [ ] UV-Stift und UV-Lampe, sonst Nagellack oder Lackstift in 5 Farben
- [ ] USB-Stick oder Handy für die versiegelten Dateien, Papier und Stift

**B. Aufbau**
- [ ] MDF-Platte liegt flach und rutschfest auf dem Tisch (Unterlage oder Anschlag unter bzw. neben der Platte, nicht auf dem Papier)
- [ ] Controller zwischen den Nagel-Anschlägen, Rückseite oben, alle Löcher frei sichtbar, Kabel locker nach oben vom Brett geführt
- [ ] Zellen 1–12 leer
- [ ] Kamera senkrecht über der Bogenmitte; alle 7 Marker und alle 12 Zellen im Bild
- [ ] 4K, 30 fps; Fokus und Belichtung gesperrt; kein Zoom; Nicht stören an
- [ ] Ab jetzt Kamera und Bogen nicht mehr berühren

**C. Vorab-Test A (Registrierung)**
- [ ] 15 s statisch aufnehmen, Hände außerhalb des Bildes
- [ ] Original nach `C:\ANASTYLOS\incoming\proto-01\preflight-A.mp4` kopieren, Bescheid geben
- [ ] Erst weiter nach „A bestanden“

**D. Vorab-Test B (eine Schraube)**
- [ ] Ca. 30 s: 10 s statisch, eine Schraube mit Pausen herausdrehen, allein in eine leere Zelle legen, Hände weg, 10 s statisch
- [ ] Als `preflight-B.mp4` ablegen; Loch und Zelle mitteilen
- [ ] Schraube wieder einsetzen; erst weiter nach „B bestanden“

**E. Ground Truth (§4)**
- [ ] Foto mit Lochlabels H1…Hn, Schrauben eingesetzt
- [ ] Schrauben ohne Kamera entfernen, messen, notieren
- [ ] Gewindespitzen codieren, trocknen lassen
- [ ] Zuordnung festlegen (Würfel bei identischen Schrauben), wieder einsetzen, Foto
- [ ] Skript auswürfeln: Reihenfolge, Zielzellen, Loch für C1, Zelle für C2
- [ ] `proto01.sealed.json` mit Nonce schreiben, Hash berechnen, nur den Hash schicken
- [ ] Datei vom PC entfernen, Papierkopie behalten

**F. Aufnahme (§6), erst nach „Hash committet“**
- [ ] Skript ausgedruckt neben dem Aufbau
- [ ] 10 s statisch
- [ ] Je Schraube: ≥2 s Hände ganz weg, herausdrehen, tragen, allein in die Zielzelle, ≥2 s Hände ganz weg
- [ ] C1 und C2 an ihrer Skriptstelle
- [ ] 5 s statisch am Ende
- [ ] Jede Abweichung sofort mit Uhrzeit notieren

**G. Danach**
- [ ] Original-MP4 unverändert nach `C:\ANASTYLOS\incoming\proto-01\` kopieren
- [ ] Codes in den Zellen ablesen, jede Zelle fotografieren
- [ ] `proto01.cells.json` mit Nonce, Hash schicken, Datei vom PC entfernen
- [ ] Tray bis zur Auswertung unverändert lassen
