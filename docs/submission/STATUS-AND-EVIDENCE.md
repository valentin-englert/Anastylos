# ANASTYLOS — Status und Belege (Einreichungsstand)

Stand: `docs/submission-handoff` @ `b522336d1e38c22e800a747ebc68b1af23a87a15` (2026-10-31, Arbeitsverzeichnis sauber).
Art: reine Dokumentation. Keine Runtime-Änderung, kein Modellaufruf, kein Merge/Push.

Referenzstände: konsolidiertes Main `80eb2030` (Android `b65cc0a0`, Core `66580106`);
Agent M0–M2 aus `integration/agent-v1` @ `ef614e31`; Stabilisierung `b7f2d76b`;
Provider-Lifecycle `2efc02c8`; Zeichnungsauswahl `37a268e2`; Selected-Muse `2144117b`;
Storage-Vertrag `04a97968`. Forschungsbasis: `89aad323`; Auswertung vom 31.10.2026 (extern, read-only).

Legende: **I** implementiert · **O** offline geprüft · **P** echter Providernachweis ·
**H** Hardwarebeleg (begrenzt) · **U** unabhängig validiert · **offen**.

## 1. Kompakte Capability-/Belegmatrix

| Fähigkeit | Status | Commit / Datenbasis | Grenzen |
|---|---|---|---|
| Deterministischer Kern (Live-Koordinator, Agent M0–M2, Guide-Verifier) | I + O | `b522336d`; Agent 89, Guide 51, Provider 33, Integration 12 PASS (31.10.2026, je `python -m unittest discover -s tests`); Live-Core 51, L2Sim 33, Eval 38 (Architekturindex) | Nur synthetische + FULL-01-Entwicklungsdaten; Journal-Vertrag 0.2-Felder teils inkonsistent; kein Echtzeitversprechen |
| Verifizierte Muse-Claims (Guide, A2: Modell schlägt vor, Code prüft, Template spricht) | I + O, P begrenzt | `tools/guide/claims.py`; Provider-Fixtures aus echtem Smoke-Run (nur synthetische Eingaben, 4 Dateien, `MANIFEST.json`); STORED-IN-V2-01 (s. u.) | Freitext→Claim-Lexikon; ein abgelehnter Claim verwirft die Antwort (fail-closed); Live-Modellverhalten darüber hinaus offen |
| Ausgewählte Teileabfrage (Ursprung/Ablage getrennt, hashgebunden, read-only) | I + O | `37a268e2` (Auswahl V1), `2144117b` (Selected-Guide), `04a97968` (Storage V2); S-Rückschale/Platine als Record-Lookup, Null-Ursprung-Abstinenz | Nur eine exakte `declared_removal`-ID; keine Composites/Aliase/Gruppen; kein Frame→Record-Nachweis, keine Identitäts-/Montagefreigabe |
| Positive Ablageantwort STORED-IN-V2-01 | P (ein Aufruf, begrenzt) | `04a97968`; 31.10.2026, 1 autorisierter Realaufruf ohne Retries; Modell 8,672 s, CLI inkl. Drain 8,734 s; Budget 4, benutzt 3→4, Rest 0 | Entwicklungsdaten, keine Hardwarevalidierung, kein bestätigter Frame-Link; keine weiteren Aufrufe autorisiert |
| Modell- vs. lokalem Rendererbeitrag | belegt (Trennung) | `selected_guide.py`: `_checked` läuft Shared-Verifier + Selected-Boundary; Text via `guide.en.render` aus verifizierten Claims | Modellprosa wird nie übernommen; `source: Muse + outcome: supported` heißt Claims akzeptiert, Beschreibung lokal zitiert |
| E2 Korrespondenz (Protokoll, versiegelt) | H begrenzt + U im engen Protokoll | `docs/PROTO-01-NOUV.md` §10, 28.10.2026: 4 korrekt / 0 falsch / 1 unattribuiert, STRONG PASS; Score `2691d41d…` | Nur mit operatordefiniertem Inventar (4 Mulden), ein Take, ein Setup; E1 nicht gelaufen (Schrauben unsichtbar); jeder Link braucht Operatorbestätigung |
| Capture-Pfad (GUIDE-03, CAL-01, CAMERA-01/02, HARDWARE-03, HARDWARE-01/02) | H begrenzt | GUIDE-03: Xiaomi 5 Armierungen je 1 JPEG + Negativtests (frozen); HARDWARE-03: Samsung-Transfer hashidentisch (`bcea6310`); HARDWARE-01/02: reale Vorher/Nachher-Paare | Keine metrische Rekonstruktionsvalidierung; Druckskala ungemessen; Kamera→Memory→SVG→Muse bleibt `PENDING_EVIDENCE`; Preview-~33-fps ist kein durchgehender Core-Durchsatz |
| E3-B (unabhängiger Test, Observer 1.7 + Linker 1.7) | offen (preregistriert, nicht aufgenommen) | `docs/PROTO-01-E3B.md`, Freeze Pipeline `7b2606a7`, Scorer `a76891bb`; 20/20 Git-Blob-Hashes | 19/20 rohe Checkout-Hashes wegen CRLF in `tools/proto-01/e3b_grid_frame.py` (64 CRLF vs. LF, Git meldet sauber); kein uneingeschränkter Raw-Byte-PASS; nichts normalisiert |
| Live-Latenz | gemessen (kein Produktversprechen) | L2-Arm 29.10.2026: Median 21,7 s, p95 64 s, Max 114 s; Smoke synthetisch 6,4 s; STORED-IN-V2-01 8,672 s; Provider-Schranke 367 s Transport/Backoff pro Vorschlag, Antwort-Deadline 60 s mit fixem Fallback | Latenz ≠ Prozessdauer; späte Ergebnisse ersetzen den Fallback nie; OS-/Filesystem-Stillstand ohne harte Garantie |
| Offene Hardware-/Framebindungen | offen | Keine qualifizierten Spatial-Sidecars für FULL-01-Crops im geprüften Bündel; `measured_print_scale` unset; Profil-/Pose-/Epochenbindung fehlt | Frame→S-Record-Zuordnung operatoroffen; Frontgruppe ohne einzelnen Schalenrecord; verdeckte Ränder nicht beobachtet |
| CAD / Mesh | eingefroren, kein reales Modell belegt | CAD: isolierter Worker existiert, Lizenz-/Redistributions-Reviews + Realbild-/FreeCAD-Gates offen; Mesh: synthetisch, Case-A-z-Floor-Fehler 0,2375 vs. 0,05 retained | Kein belegtes reales FULL-01-Modell; Visual Hull sieht Konkavitäten nicht; generative Ergänzung zählt nicht als Beobachtung |
| L2 vs. P0 | L2 ohne Vorteil, P0 aktiv | `tools/live/integration/README.md`: L2 0,752 vs. P0 0,803 resolved/Interruption, PREREG-Kriterium 1 verfehlt → rejected; Produkt nutzt P0 | Simulator/Orakel/Kostenmodell sind Annahmen, keine Operatormessung; L2 bleibt Forschung |
| SAM / modellbasierte Korrespondenz | verworfen | Research-Pass-01: 0 verifizierte Korrespondenzen; SAM-01 4/4 Hände, 0 Teile; DECISION-MEMO Stop-Liste; Review 31.10.2026 bestätigt | Kein neuer Befund widerlegt Laufzeit/Mehrwert; GrabCut-Frontversuch 77,25 s ohne Echtzeitversprechen |
| Technische Zeichnung / Explosionsskizze (A2) | offen (separater Worktree, nicht integriert) | `feat/explosion-sketch-m1` @ `918318e7`; Paket-SHA `cae31223…` und 3/3 Crop-Hashes belegen Bytes, nicht Geometrie; Front partieller Konturgewinn | Konkrete Fehler s. `CLAUDE-HANDOFF.md`; kein integrierter/getesteter Stand in `b522336d` |
| Session-UI (M3), Session-Report, Capture-Gap-Planer | geplant | `feat/session-ui-m3` und `feat/session-report` auf `b522336d` (keine Arbeit); `feat/capture-gap-planning` @ `248f5223` ein Commit voraus (nicht in diesem Branch) | Fertigstellung nicht vorwegnehmen; kein Demo-Versprechen |
| LIVE-01 (Live- vs. Offline-Fragen) | offen (Entwurf, nicht bindend) | `docs/LIVE-01.md`, Draft 29.10.2026, Lane E, Vertrag 0.1; ändert keine E3-B-Freeze-Dateien | Owner-Entscheidungen §13, Parameter §4, Commit-Ledger §10 fehlen; keine Aufnahme |

Entwicklungsdaten sind keine unabhängige Validierung. FULL-01 (Custody 1.7.0,
Owner-Antworten), E2-D/E2R/SNES-01 und sämtliche Schwellen stammen aus der
Entwicklung; Overfitting statt Beleg bei weiterem Tuning (DECISION-MEMO).
E2 ist ein enger Protokollerfolg, keine Generalisierung auf natürliche Demontage.

## 2. Deterministischer Kern und verifizierte Muse-Claims

- Architektur A2 (`tools/agent/README.md:3-5`): der deterministische Kern
  entscheidet; Muse schlägt nur vor und liegt nie zwischen Frage und
  Operatorantwort.
- `tools/guide/claims.py:1-8`: prüft `anastylos-guide-claims@1` gegen das exakte
  Sitzungspaket (SHA muss dem Build-Hash entsprechen); gesprochener Text kommt
  aus festen Templates, nie aus Modellprosa; ein abgelehnter Claim verwirft die
  Antwort.
- `tools/agent/selection.py:47`: bindet Ereignis, Memory-/Guide-Hash und
  Eingangspfad (Review-Tabelle, historische R-007-Korrektur).
- Provider (`tools/live/providers/meta.py`, `CONTRACT.md` §4):
  `muse-spark-1.3-contributor`, Effort `max`, beide konstant;
  `POST https://api.meta.ai/v1/responses`, striktes `json_schema`, Code-Revalidierung,
  advisory-only mit L0-Fallback; 120 s Timeout, ≤2 Retries bei 408/429/5xx,
  Request-SHA-Dedup, Call-Cap; Schlüssel nur aus `MODEL_API_KEY`, nie geloggt;
  Text-nur synthetische/minimierte Eingaben, keine Bilder/Audio/Rohdateien/Secrets;
  Contributor-Tier mit Provider-Default `store: true` (Ein-/Ausgaben dürfen
  gespeichert/trainiert werden).
- Echter Smoke-Nachweis: ein synthetischer Aufruf je Task, HTTP 200, 6,4 s,
  29 In-/548 Out-Tokens (CONTRACT §4); Fixtures in
  `tools/live/providers/tests/fixtures/recorded/` (4 Dateien + `MANIFEST.json`).
  Live-Modellverhalten darüber hinaus ist offen (außer STORED-IN-V2-01).

## 3. Ausgewählte Teileabfrage: Ursprung/Ablage

- `select` (`37a268e2`, `tools/agent/selection.py`): read-only, hashgebunden,
  `anastylos-drawing-selection@1` mit exakt einem `event_id`, absolutem
  `source.memory_path`, `memory_sha256` + `guide_sha256`. Memory- und Guide-Bytes
  müssen passen, Guide-Input muss Quelle+Hash binden, Session muss stimmen.
  Nur eine exakte `declared_removal`-ID (auch mit pluralem Operatorlabel);
  keine Observations/Aliase/Gruppen/Composites; supersedierte Teile, Duplikate
  und mehrere aktive Storage-Links werden verweigert.
- Erfolg emittiert `anastylos-drawing-resolution@1` mit getrennten
  `original_location` / `stored_location`, Basis, Phase/Zeit, fehlenden Feldern
  und expliziten Limits. Ursprungstext bleibt wörtlich (inkl. SRC-Prosa);
  fehlender Ursprung/Speicher bleibt null. Evidenzmetadaten aus referenzierter
  Observation sind nicht verifiziert und beweisen keine Bilddatei.
- `selected-ask` (`2144117b`, `tools/agent/selected_guide.py`): Intent nur
  `origin`/`storage`. Default deterministisch L0; `--claims` nutzt Offline-
  FakeProposer (`source: offline_claims`, nie Modellbeleg); `--muse` ist separat
  opt-in, Modi schließen sich aus, kein Budget wird erzeugt/erhöht.
  Kontext `anastylos-selected-part-context@2` enthält nur ID, Label, Memory-Hash,
  typisierte Ursprungs-/Ablagebeschreibungen, allowlistete Record-IDs und Limits;
  keine Pfade, Personennamen, Notizen, Bilder oder Fremdteile.
- Selected-Boundary: nur die angefragte Relation (`removed_from`/`stored_in`)
  oder `unknown`, immer mit exakter kanonischer ID; Ursprungsobjekt muss null
  sein (vollständige lokale Beschreibung wird zitiert); positive Claims brauchen
  nichtleeren Speicherwert; `unknown` braucht null Objekt/Basis +
  `insufficient_evidence`; gemischte/unsupported-Relationen, Referenz-Aliase und
  erfundene Referenzfelder werden abgelehnt.
- Offline-Verifikation 31.10.2026: Agent 89, Guide 51, Provider 33, Integration 12
  PASS, 0 FAIL/ERROR/SKIP; Compliance 0 Errors; E3-B-Task-Bytes 20/20 (Blob).
  S-Rückschale/PCB lösen auf, Replay-PCB mit fehlendem Ursprung enthält sich.
  Kein Realaufruf in dieser Abnahme (STORED-IN-V2-01 separat).

## 4. STORED-IN-V2-01 und Renderertrennung

- Ein autorisierter Realaufruf auf bestehender Fortsetzungskopie, Retries
  deaktiviert: exakte Ablagestelle + Operatorbasis zurück, beide Verifier
  akzeptieren, kein Cache/Fallback. Modell 8,672 s, CLI inkl. Drain 8,734 s
  (ohne Interpreterstart). Budget total 4, benutzt 3→4, Rest 0; keine weiteren
  Aufrufe autorisiert (`tools/agent/README.md:286-292`,
  `storage-contract-proof/verification.json`).
- Begrenzte Provider-Demonstration auf Entwicklungsdaten; keine physische
  Validierung, kein bestätigter Frame-Link.
- `source: Muse + outcome: supported` bedeutet: Modell-Claims akzeptiert, lokaler
  Renderer zitiert die tatsächliche Beschreibung mit Laut-Operator-Qualifizierung
  und lokalen Zitaten. Modellprosa wurde nicht übernommen.

## 5. Latenz, E3-B, offene Bindungen

- Realer L2-Arm (`C:\ANASTYLOS\data\live-dev\TL\2026-10-29-L2-primary`,
  588 Vorschläge, 311 Cache, 277 bezahlt, 0,40 M In-/0,68 M Out-Tokens):
  Median 21,7 s, p95 64 s, Max 114 s. LIVE-01-Delay D leitet sich aus p95 ab
  (Entwurf, ungebunden).
- Provider-Lifecycle: Antwort-Deadline 60 s (Default), danach Fallback fix;
  Drain mit begrenzter Provider-Arbeitsschranke (367 s ab Start + 5 s Cleanup),
  JSON-Budget erst nach Drain; Realtransport 120 s Wall-Deadline je Versuch,
  max. 3 Versuche (2 s + 5 s Backoff) → ≤367 s Transport/Backoff pro Vorschlag.
  Freie Guide-Frage kann bis dahin dauern; OS-Stillstand ohne Garantie.
- E3-B: preregistriert 29.10.2026 vor jeder Aufnahme; Segment A (Protokoll,
  ≥4 Teile + Shift/Arm-Rest/Pose-Push-Kontrollen), Segment B (natürlich,
  Batch-Carry). PASS nur bei A+B ohne incorrect/unsupported und mit
  `inference` auf jedem Autolink. Aktuell: keine Aufnahme, keine Verarbeitung,
  kein Score. Hashstand s. Matrix (20/20 Blob, 19/20 Raw, CRLF-Abweichung,
  nichts normalisiert).
- Offen: Druckskala, Kamera-/Stream-Profilbindung, Bild-/Sidecarbindung + Posezeit,
  unveränderte Objektepoche; `geometry_ready` ist grobe Heuristik (≥2 Posen/≥5 mm),
  keine Flächendeckung; erhabene Kanten nicht still auf z=0 projizieren.

## 6. CAD/Mesh-Freeze, L2/P0

- CAD: isolierter Worker (`validate`/`compile`/`preview`/`evidence` stdlib;
  `builder`/`build`/`reopen`/`run` nur in isolierter Venv). Lizenz-Review offen
  (`casadi` LGPLv3+, OCCT-Bundle, drei leere Lizenzfelder); FreeCAD-Gate
  `PENDING_EVIDENCE`; keine SPDX-/Notice-Änderung ohne Owner.
- Mesh: `mesh-vh-0.1.0`, synthetisch, Manifest `non_normative`; Case A
  0,2375 vs. 0,05 retained; Kavitäten überfüllt (Volumenfehler 1,5527);
  kein reales FULL-01-Modell belegt.
- L2: 0 Siege / 30 Ties / 9 Niederlagen vs. P0; 38/1 vs. PR; Integrität 0,
  Invalid-Action 0 %; Fallback 0 (5 Timeouts mit Retry). Per PREREG §7 rejected.
  Produktauswahl bleibt P0.

## 7. Offene Nachweise (Auswahl)

E3-B-Aufnahme + blinde Verarbeitung + Score; LIVE-01-Bindung + Take; reale
Frontkontur-Abnahme mit Foto-Overlay (s. `CLAUDE-HANDOFF.md`); qualifizierte
Spatial-Sidecars + Druckmetrologie; passende FULL-01-Referenzgeometrie mit
Herkunft/Rechten (nicht nachgewiesen); dritte unabhängige Validierung;
3-Minuten-Video + Einreichungsbestandteile (s. `SUBMISSION-CHECKLIST.md`).
