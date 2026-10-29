# P3 — hand presence and occlusion: adversarial test plan and model feasibility

Requirement H1 is recorded in [`ARCHITECTURE.md`](../ARCHITECTURE.md)
("Correspondence contracts and owner decisions"). This file holds the test
plan the next observer version must pass and the feasibility notes for a
learned hand detector. Status 2026-10-29: planned, nothing implemented.

## 1. Signals to separate

| Signal | Meaning | Evidence it may use |
|---|---|---|
| `hand_present` | a hand or gloved hand is over or entering the zone | entry from outside the device/work area, motion, tracking over time, optional landmark model, optional colour prior |
| `occluded` | the zone cannot be seen, whatever covers it | difference against the last hand-free reference without a confirmed component change |
| `component_changed` | the zone's content changed and stayed changed while not occluded | v1.1 content change on clean samples |

A zone is `unknown` while `occluded` and not resolved; a cover that stops
moving stays `occluded` until it leaves or is confirmed as a component by
the content rule after the zone is clean again.

## 2. Adversarial cases (each becomes a synthetic or recorded test)

| ID | Case | Pass condition |
|---|---|---|
| A1 | Static skin-coloured part larger than a site (E2 W-RED, grid sites) | zone becomes clean and observable; no `hand_present` |
| A2 | Hand rests motionless over a site for 10 s | never a component event; zone `occluded` throughout |
| A3 | Gloved hand (black, blue nitrile, white) | detected as `hand_present`/`occluded` like a bare hand |
| A4 | Hands of clearly different skin tones (several volunteers) | same detection rate within a stated tolerance; reported per hand |
| A5 | Lighting change while a hand is in view (lamp on/off, shadow) | no component event caused by the change alone |
| A6 | Skin-coloured part carried by a hand and set down | `hand_present` while carried; after the hand leaves, a normal deposit |
| A7 | Hand enters from the image edge and covers half the device | sites under the hand `occluded`; others stay observable |
| A8 | Hand-coloured background object (wooden tool, cardboard) static in view | not a hand |

Synthetic signal-plane tests (like `tests/test_observer.py`) cover A1, A2,
A6 and A7 first; A3–A5 and A8 need recorded clips with sealed labels.
Demographic claims (A4) are made only from recorded tests and only for the
hands actually tested.

## 3. Feasibility: MediaPipe Hand Landmarker or a segmentation model

Facts checked 2026-10-29 on pypi.org and developers.google.com:

- `mediapipe` 1.2.0 (27 Oct 2026), Apache-2.0, classifier
  "Development Status :: 3 - Alpha"; Windows wheels (`win_amd64`,
  24.5 MB); classifiers list Python 3.9–3.14.
- Declared dependencies: `absl-py`, `certifi`, `numpy` (no bound),
  `sounddevice`, `flatbuffers`, `opencv-contrib-python` (unpinned; this
  repository already pins `opencv-contrib-python==4.14.0.94`),
  `matplotlib`.
- Hand Landmarker: model bundle `hand_landmarker.task` (palm detector plus
  21-keypoint landmark model); outputs handedness and landmarks, **no
  pixel mask**; `num_hands` default 1; IMAGE / VIDEO / LIVE_STREAM modes;
  Pixel 6 CPU 17 ms per frame. Model size and documented limitations
  (occlusion, gloves, light, skin tone) are not stated.
- Privacy notice: input is processed on device, but MediaPipe Tasks APIs
  send performance and usage metrics to Google.

Assessment:

1. **Policy blockers first.** Python in this repository is offline with
   no telemetry exceptions beyond the two documented carve-outs. The usage
   metrics must be shown to be disabled or blocked before MediaPipe can
   run here; that needs an owner decision. A new dependency also needs the
   SPDX inventory and notices updated (`tools/compliance/check.py`), and
   the model file must be stored with its SHA-256, not fetched at run time.
2. **Fit.** Landmarks answer "is there a hand and where are its fingers",
   which is what `hand_present` needs; they do not give a mask, so
   `occluded` would come from a convex hull of the landmarks, dilated.
   Gloves and heavy occlusion are undocumented and must be measured (A3,
   A7). The model looks at whole images, so overhead 4K frames need
   downscaling or tiling; small hands at the frame edge may be missed.
3. **Cost.** At the observer's 5 Hz sampling, about 20 ms per frame on CPU
   is affordable (roughly 0.1 s per second of video).
4. **Segmentation models** (SAM-style, hand–object segmentation research
   models) give masks but are heavy, mostly not real-time, and need
   separate licence checks; the repository's SAM client is a network
   carve-out, not an offline option.

Recommendation: make the colour-independent signals the primary path
(hand-free reference, entry from outside the device region, motion and
tracking; this alone fixes A1, A2 and A8 and does not depend on skin tone)
and evaluate MediaPipe as an optional second signal in a separately
versioned experiment, after the telemetry question is settled. Keep a
colour prior only as an optional per-user calibration.
