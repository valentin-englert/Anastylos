# VOICE-01 — Sprache und Wake-Word in der Android-App (Xiaomi)

- Worktree `C:\Users\HP\Documents\Anastylos-integration-spatial`,
  Branch `integration/spatial-capture-v1`. Gerät: Xiaomi M2101K6G
  (Android 13, Kamera 0, kalibriert per XCAL Lauf 4).
- Referenzverhalten: `Anastylos-drawing-lab` (`research/drawing-proof`),
  `4187de16` / `064df816`: `tools/live/providers/transcribe.py`,
  `tools/session-ui/server.py` (`POST /api/voice/transcribe`),
  `tools/session-ui/web/app.js` (Wake-Word, Sprachaktivität, Sprachausgabe).
  Workstation-Dateien wurden **nicht** geändert.

## 1. Realität vor der Änderung (Code + Gerät)

- Die App **filmt und streamt nicht**: CameraX bindet nur Preview,
  ImageAnalysis und ImageCapture. Es gibt keine Video-Abhängigkeit
  (`camera-video` fehlt), kein `RECORD_AUDIO`, kein `AudioRecord`,
  kein `TextToSpeech`.
- Die App hält die Kamera exklusiv. Eine zweite App kann **nicht**
  gleichzeitig mit derselben Kamera filmen. Ein Livestream des Arbeitsfelds
  geht nur über Bildschirmspiegelung/-aufnahme (z. B. scrcpy) oder eine
  zweite Kamera.
- Der gepaarte, verschlüsselte Weg (Receiver `https://…:8443`,
  `/v1/pair`, `/v1/packages`, `/v1/guided/*`; G0-Kontrollvertrag
  `capture-companion/control/contracts`) hat **keinen** Nachrichtentyp für
  Audio, Operator-Eingaben oder zu sprechenden Text.

## 2. Was jetzt auf dem Xiaomi funktioniert (gemessen 2026-11-01)

| Prüfung | Ergebnis |
|---|---|
| Mikrofon parallel zur Kamera-Analyse | **ja**: `AudioRecord` 16 kHz mono, Quelle VOICE_RECOGNITION, `recordingState=3`; Android zeigt Kamera- und Mikrofon-Indikator gleichzeitig |
| Kamera-Analyse während Mikrofon läuft | unverändert: 1920×1080, Analyse Ø 36–45 ms, Sensor→Ankunft Ø 62–66 ms |
| Bildschirmaufnahme parallel (`adb shell screenrecord`, 30 s) | vollständig, 74,6 MB, ohne Abbruch. Hinweis: `screenrecord` nimmt **keinen Ton** auf; Tonqualität einer echten Stream-Aufnahme ist damit **nicht** geprüft |
| Sprachausgabe | `TextToSpeech` en-US verfügbar (`tts ready=true`) |
| Sprachaktivität | Abschnitte werden lokal erkannt (`segment ms=…`), ohne Audio oder Text zu loggen |
| Transkription, Wake-Word auf Transkript, Antwort sprechen | **nicht möglich**: kein Uplink im Vertrag (Abschnitt 5); die App meldet „Voice link not available yet (awaiting TL contract CR-VOICE-01)" und sendet nichts |

Messwerte Raumgeräusch: siehe Abschnitt 4.

## 3. Änderungen (Android)

Neues Paket `org.anastylos.capture.voice`:

| Datei | Aufgabe |
|---|---|
| `SpeechSegmenter.kt` | Sprachaktivität wie Referenz: Energie > max(0,004; 3 × adaptives Grundrauschen), 0,3 s Vorlauf, Ende nach 0,8 s Stille, max. 8 s, min. 0,4 s. Rein, getestet |
| `WavEncoder.kt` | WAV mono 16-bit PCM, 16 kHz (24 kHz erlaubt), ≤ 32 MB; nur im Speicher |
| `WakeWord.kt` | Muster wie `app.js`: optional „hey/ok", dann „muse/meta/mews", dann Befehl; nur Wake-Word → „Yes?" und 6 s Annahme ohne Wake-Word; feste Befehle „next question", „repeat"; alles andere wird verworfen |
| `VoiceLink.kt` | Naht Handy ↔ Workstation (Transkript holen, Operator-Eingabe senden, geprüften Sprechtext zurück). Ausgeliefert: `PendingContractVoiceLink`, das nichts sendet |
| `VoiceController.kt` | Mikrofon-Thread, taub während das Handy spricht, während ein Abschnitt transkribiert wird und während Push-to-talk; TTS en-US; Push-to-talk |

Weitere Dateien: `AndroidManifest.xml` (`RECORD_AUDIO`, erst beim Einschalten
angefragt), `di/AppContainer.kt` (ein Mikrofon-Besitzer), `MainActivity.kt`
(Lautstärke-runter = Push-to-talk, nur bei eingeschaltetem Modus),
`ui/guided/GuidedCaptureScreen.kt` („Listen for Muse", roter Indikator
„MICROPHONE ON", „Hold to talk", Statuszeile; Transkripte werden nie angezeigt).

Mitbehoben (Owner-Freigabe „alles fixen, was stört"):
- `GuidanceViewModelTest`: 18 Fehlschläge aus `b65cc0a0` (`SystemClock`
  bzw. `Log` nicht gemockt). `armClock` wird jetzt aus dem injizierten
  `nanoClock` abgeleitet (gleiche Zeitbasis), das Arm-Logging ist
  injizierbar; Verhalten in Produktion unverändert.
- Verwaister Gradle-Test-Executor (PID 13412, Elternprozess 17680 tot)
  beendet; Standard-Build läuft wieder. JVM-Crashlogs aus dem
  Android-Ordner in den Scratchpad verschoben.

Regeln aus dem Auftrag:
- Kein Schlüssel auf dem Handy; kein Netzwerkaufruf ohne Vertrag.
- Stille wird nie gesendet; Abschnitte leben nur im Speicher.
- Gesprochen werden nur Texte, die die Workstation liefert, plus der feste
  Statussatz „Yes?" aus dem Referenzverhalten.
- Ein Transkript wird als `anastylos-live-operator-input@1`
  (`source: speech_transcript`) übergeben; Memory-Schreiben nur nach
  „yes" im Koordinator, nicht auf dem Handy.

Tests: Android-Unit-Suite **470/470 PASS** (neu: `WakeWordTest` 3,
`SpeechSegmenterTest` 4, `WavEncoderTest` 2), Lint 0 Fehler.
APK `07d13121…` auf dem Xiaomi installiert und hash-geprüft.

## 4. Messwerte

| Messung | Wert | Bedingung |
|---|---|---|
| Sprachabschnitte (= potenzielle Uploads) pro 10 min | **2** (beide 8,0 s, am Längenmaximum geschnitten) | Raumgeräusch am Arbeitsplatz, 16:50–17:00, kein gezieltes Sprechen; Grundrauschen 0,0046–0,0059 RMS |
| Kamera-Analyse in derselben Zeit | durchgehend (117 Diagnosefenster à 60 Frames), Thermal Status 0 | Mikrofon dauerhaft offen |
| Mikrofon-Start → erster Abschnitt | Mikrofon läuft 1,4 s nach dem Einschalten, TTS 1,3 s später bereit | |

Einordnung: Das ist **kein** Werkstattlärm-Test (keine Werkzeuge,
kein Gespräch gezielt erzeugt). Ein Abschnitt ist noch keine
Fehlauslösung: Ohne Wake-Word am Transkriptanfang wird er verworfen. Er
kostet aber einen Transkriptionsaufruf (Referenz: 0,18 USD pro Stunde Audio;
2 × 8 s ≈ 0,0008 USD pro 10 min).

Nicht messbar ohne Uplink: Latenz Sprechende → Antwortbeginn, verpasste
Wake-Words bei 20 Befehlen, Fehlauslösungen auf Wake-Word-Ebene. Die
Workstation-Referenz misst 1,5–4,1 s Transkriptionslatenz pro Clip und
5/5 erkannte Wake-Words (synthetisch).

## 5. Vertragsanfragen an den TL

**CR-VOICE-01 — Audio-Uplink über den gepaarten Receiver-Weg.**
Vorschlag: `POST /v1/guided/voice/transcribe` am Receiver, gleiche TLS-
und Token-Prüfung wie `/v1/guided/*`. Body `audio/wav`, mono 16-bit,
16 kHz, ≤ 8 s (≤ 260 kB). Der Receiver ruft `transcribe.py --stdin` als
Unterprozess (wie `session-ui/server.py`), speichert nichts und antwortet
mit `{status, transcript, latency_s}` aus `anastylos-live-transcript@1`.
Fehlerfälle: 415 (kein WAV), 413 (zu lang), 502/504 (Provider).

**CR-VOICE-02 — Operator-Eingabe hoch, Sprechtext runter.**
Vorschlag: `POST /v1/guided/voice/input` mit
`anastylos-live-operator-input@1` (`source: speech_transcript` oder
`command: next_question|repeat`, `session_id`). Antwort
`{status, speak: {text, kind: l0_question|read_back|status|verified_guide, basis_ref}}`;
`text` stammt ausschließlich aus dem Koordinator bzw. aus geprüften Claims,
nie aus freiem Modelltext. Der Receiver braucht dafür eine Verbindung zur
laufenden Agent-Session (`tools/agent`, `integration/agent-v1`); das ist
eine Workstation-Änderung.

## 6. Was ohne Workstation-Änderung nicht geht

- Transkription vom Handy aus (kein Receiver-Endpunkt; der vorhandene
  `session-ui`-Endpunkt ist unverschlüsseltes HTTP außerhalb der Paarung
  und wäre ein konkurrierendes Protokoll).
- Die Abnahme „Muse, next question" → gesprochene Frage → „Screw from the
  back shell" → „yes, that is correct" → Commit in einer Demo-Memory.
- Messung von Latenz und Wake-Word-Trefferquote auf dem Handy.

Sobald CR-VOICE-01/02 umgesetzt sind, wird nur `PendingContractVoiceLink`
durch eine Implementierung über `ReceiverClient` ersetzt; der Rest der
Android-Seite ist fertig und getestet.
