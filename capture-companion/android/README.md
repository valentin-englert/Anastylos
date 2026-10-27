# ANASTYLOS Capture — native Android app (prototype)

Kotlin + Compose companion for ANASTYLOS Capture: offline sessions,
CameraX evidence with SHA-256 provenance, QR-bootstrapped pairing with
the Windows receiver, and operator-triggered package upload. Product
contract and protocol live in the module docs, not here:

- [`SYNC_PROTOCOL.md`](../SYNC_PROTOCOL.md) — pairing + upload protocol
- [`receiver/SESSION_FEED.md`](../receiver/SESSION_FEED.md) — in-session feed
- [`LIMITATIONS.md`](../LIMITATIONS.md) — honest boundaries (read before demoing)
- [`SCHEMA.md`](../SCHEMA.md) — evidence package contract (schema v1)

## Toolchain (all pinned, all official sources)

- JDK **21** to run Gradle — Temurin 21.0.12.1 LTS in this session.
  AGP 9.4.1 requires JDK 21; on JDK 17 the TLS round-trip test dies
  with `NoSuchMethodError: List.removeFirst()` and the Robolectric
  SDK-36 sandbox refuses to start. The wrapper honors `JAVA_HOME`
  (not `ORG_GRADLE_JAVA_HOME` on Windows).
- Gradle 9.8.2 via the committed wrapper; Android SDK under the
  gitignored `.toolchain/` directory (platform-tools 37.0.1, emulator
  37.2.12, platforms android-36 + android-37.0, build-tools 36.x).
- `minSdk` 26, `targetSdk` 36, `compileSdk` 37. Dependencies pinned in
  `gradle/libs.versions.toml`.

## Build, test, lint

From `capture-companion/android/` (PowerShell):

```powershell
$env:JAVA_HOME = '<path to>\jdk-21.0.12.1+1'   # Temurin 21, per session
.\gradlew.bat :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
```

Observed 2026-10-27: **145/145 unit tests pass, lint 0 issues**,
`app-debug.apk` (~40 MB) at
`app/build/outputs/apk/debug/app-debug.apk`.

## Emulator smoke pass

API-36 AVD (`anastylos-m2`, SwiftShader) with the debug APK: install,
clean launch, session create, in-app QR scanner live with no crash,
6 real CameraX JPEG stills sampled at the 5 s cadence, clean stop,
session + photos surviving force-stop/cold-start, zero `FATAL
EXCEPTION` in logcat. CameraX Preview surfaces render block garbage
on SwiftShader (virtual-GPU artifact; the still path is unaffected) —
viewfinder rendering stays a physical-device gate. Screenshots under
`.toolchain/emu-shots/` (gitignored, not evidence).

## Physical-device gate: OPEN

The 12-step acceptance checklist (install APK, pair over QR, capture,
sync, verify bytes + hashes, reconnect, confirm no memory mutation)
has not run on hardware — no phone was attached in this session. Do
not present emulator results as hardware validation.
