# GUIDE-03 — Hackathon guided auto-shutter (ACCEPTED / FROZEN)

Worktree `Anastylos-integration-spatial`, branch
`integration/spatial-capture-v1`. Ancestor checkpoint `02c2645f`
(GUIDE-01) is in this branch. Xiaomi profile validation is the prior
commit `a71cf17b`. This note records the physical acceptance of the
hackathon shutter on top of that profile. `guidance-hypothesis-1`
is unchanged.

Device: Xiaomi Redmi Note 10 Pro `M2101K6G` (`sweet`). Profile
`measured:e92c8ea1-36a8-449f-8df9-50f0f3481548`. Focus manual, 40 cm.
Target `a3-top-center` on `surface_a3`.

Tested APK, local file
`guide02-evidence/mvp-build/app/outputs/apk/debug/app-debug.apk`
written 2026-10-31 12:12:31 local, and the installed `base.apk`:

`b69dd06822adc782b2c0ffdbd9e560d177ec2d921c04d32afdad513c51001af1`

Hackathon gate only, in `HackathonMvp`: 500 ms and 5 samples. Motion-only
rejection warns and does not block. One JPEG per arming. Next-request
double-taps inside 1200 ms are debounced in the production container.

## Positive physical tests

Five separate armings, one automatic JPEG each, no second file for the
same local request id. Session `ce4758ba-3df9-4a27-8ab9-966e19610355`,
name Guided capture.

| Local request | Image | SHA-256 |
|---|---|---|
| `local-7fc7233a` | `3952e7f3-640a-47b1-9c5f-3ca43e3586a7` | `b71e439f9d7fd983cda335a2b68a90805f4ba5d71da4eaae5fbf6f2b9b2f7175` |
| `local-d5036c5b` | `21ec2c98-131a-433d-a629-b84827972567` | `ba2c4da01c4b362aa6e801b3600a803bd15890ed2cdb3d262aa4bc79a4b4f6bd` |
| `local-287795f9` | `c1096cb6-dfbf-47e4-b049-6a803f1a5f57` | `d123874bd9c09ea3f2111415ab1feaa95f1f28efea9ca2fc81607e091dccce16` |
| `local-eaa5b5ab` | `38a6ff1d-a11f-4b22-a019-8f50ef5a91a1` | `856c35eda5c619d9929c78a94640b2eb05327cbec45783877e7980116e0dba21` |
| `local-b39b5dcf` | `ef8c30f9-5dec-456b-8b33-2c522f4cb325` | `8a602ac55f5f686aa323e245d05d2c30eb6bd8e066fd60d32dcc97bd25c1e555` |

## Negative physical tests

- Off-target: PASS. `local-efe21adb`, target `a3-oblique-left`,
  `reason=off_target`, no shutter.
- Tracking lost: PASS. `local-3a80b9e5`, `track=LOST`, dwell idle.
  A second tap was `arm ignored`. No JPEG.
- Invalid calibration: PASS. `local-6a7ee75b`, `cal=false` (Focus Auto).
  No shutter. Optics then restored to manual 40 cm (`AF off · 40 cm`)
  and the session disarmed. No JPEG.
- Background/resume: PASS for unintended capture. Home while
  `local-3a80b9e5` was armed opened the launcher and left the session
  disarmed. No JPEG. Resume showed Arm.

## Limits

- Disarm during the 500 ms dwell was not verified on the phone.
  State-machine coverage is cancel-during-dwell and disarm after a
  finished capture.
- Extra JPEG `aa8c8f7e-dd4f-40b8-99d1-27a29e72028c`, SHA-256
  `f0809a17b2b02b144066eb7a329e89becd1c0379806cf0d08ca81f40e0472023`,
  is a manual Capture now. It is not a second file of any of the five
  armings. `capture_source` stays `camera` for both paths.
- Local arming writes no Core request id. Remote evidence delivery is
  outside this acceptance.
- The hackathon shutter is separate from frozen `guidance-hypothesis-1`.
- No claim of general device compatibility or freehand robustness.
- Samsung measured profiles are unchanged.

Android guided auto-shutter is hackathon MVP accepted and frozen.
Do not change it without a new, demonstrated integration blocker.
