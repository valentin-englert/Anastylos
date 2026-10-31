# ANASTYLOS — Claude-Handoff (nächster Versuch)

Basis: `docs/submission-handoff` @ `b522336d`. Nur Doku; kein Code, kein Modellaufruf.
Auswertung 31.10.2026 (read-only, extern). A2-Worktree unangetastet.

## Northstar

Beleggebundene Demontagehilfe: automatische Links nur unter beobachtbarem
Protokoll; sonst ehrliche Fragen im richtigen Moment; identische Teile als
deklarierte Gruppen; Guide erzählt nur Memory; Mensch bestätigt.
Keine Instanz-Re-ID aus Look-alikes, keine verdeckten Schrauben aus Overhead,
keine kalibrierten Konfidenzen ohne Held-out-Takes.

## Aktueller Stand (belegt)

- Deterministischer Kern entscheidet, Muse schlägt nur vor (A2).
  `tools/guide/claims.py` + fixe Templates, fail-closed. Agent 89 / Guide 51 /
  Provider 33 / Integration 12 PASS (31.10.2026).
- Hashgebundene Einzelauswahl (`37a268e2`) + Selected-Guide (`2144117b`) +
  Storage V2 (`04a97968`): Ursprung/Ablage getrennt, read-only, kein
  Frame→Record-Nachweis.
- STORED-IN-V2-01: 1 Realaufruf, exakte Ablage + Operatorbasis, beide Verifier ok,
  8,672 s / 8,734 s, Budget 4/4 verbraucht. Entwicklungsdaten, kein Hardwarenachweis.
- E2 (PROTO-01-NOUV §10): 4/4 korrekt, 0 falsch, STRONG PASS — nur mit
  operatordefiniertem Inventar, ein Take. E1 nicht gelaufen (Schrauben unsichtbar).
- Capture begrenzt: GUIDE-03 (Xiaomi 5/5 + Negative), HARDWARE-03 (Samsung
  hashidentisch), CAL-01/CAMERA-01/02, HARDWARE-01/02. Druckskala ungemessen,
  Kamera→Memory→SVG→Muse `PENDING_EVIDENCE`.
- E3-B preregistriert, nicht aufgenommen. 20/20 Blob-Hashes; 19/20 Raw wegen CRLF
  in `tools/proto-01/e3b_grid_frame.py`. Kein Raw-Byte-PASS, nichts normalisieren.
- L2 rejected (0,752 < P0 0,803); P0 aktiv. SAM verworfen. CAD/Mesh eingefroren,
  kein reales FULL-01-Modell. A2-Zeichnung separat (`918318e7`), nicht integriert.
  M3 integriert auf main (31.10.2026): UI/Report/Gap-Planer gemergt, Suiten grün
  (Agent 119, UI 33, Guide 51, Provider 33, Integration 12), Browserabnahme auf
  FULL-01-Kopien offline — kein Muse-/Hardwarenachweis; Gap-Schließung gesperrt
  (keine technische Bildprüfung).

## Relevante Forschungsquellen (lokal, read-only)

Pass-01: `C:\Users\HP\Documents\Anastylos\random\research\ANASTYLOS-Research-Pass-01\`

- `DECISION-MEMO.md:5` — PROV-01/E3-B/LIVE-01 bauen, Modell-Korrespondenz stoppen,
  Claim-Grenzen. Deadline darin 2026-11-04 21:00 ET (nur diese Quelle, s. Checklist).
- `RESEARCH-REPORT.md:73` — §§3–7: E2 4/4 [R-001], Schrauben unsichtbar [R-004],
  P1-H2-Artefakt [R-003], Provenienz-Lücken [R-005/006/007], Handbesuche [R-011].
- `research-to-code-gap.md:6` — G1–G10; G3/G4/G7 tragen LIVE-01/Capture-Fragen.
- `architecture-critique.md:89` — A5–A8: Epochen-Identität, Reversal, Unsicherheit.
- `literature-matrix.md:33` — Lücken E/D/G: kein Licht-/Tiefen-Paper, keine
  Pose-Evidenz, keine Fragenauswahl-Theorie.
- `evidence-ledger.jsonl` (48 Records) — L-001 RePairAR 26,1 % Relationen (S06 §4.3.2),
  L-005 IndEgo Narration als Ground Truth (S05 S.2), L-007/008 AssembleIt Ordnungen +
  CAD-Grounding (S01 S.12–15), L-009/010 Assembly101 Sync/Geometrie, L-018 VGGT ~30 %
  invalid, X-004/005/008 Look-alikes ungelöst, X-006 KnowNo Fragemoment (nicht
  Ansichtswahl), X-007 VLM Objektschicksal Zufallsniveau.
- `sources.json` — S01/S02/S05/S06/S08 Original-PDFs 5/5 bytegleich (laut Review).
- `external-sources.json` — X-Quellen ohne gespeicherten Volltext (nur frühere
  Recherche, keine erneute Primärprüfung).
- `corpus/S05-indego.txt:85`, `corpus/S01-assembleit.txt:1241`,
  `corpus/S02-assembly101.txt:165`, `corpus/S08-vggt-cad.txt:1003` + `:1147`.

Aktuell: `C:\Users\HP\.codex\visualizations\2026\10\31\01a19287-971e-7c13-8fba-59956957df06\research-solutions-review\`

- `RESEARCH-SOLUTION-REVIEW.md` — Entscheidung 2D-Frontkontur, A2-Fehlerklassen,
  5 Lösungswege (Q/I/D/H), kleinster Versuch (NOT_TESTED), E3-B 20/20 Blob vs.
  19/20 Raw, 42/42 Forschungsbytes.
- `verification.json`, `artifact-sha256.json`, `source-trace.json`,
  `research-before.json` / `research-after.json`.

A2 (separat, nicht mergen): Worktree
`C:\Users\HP\Documents\Anastylos-explosion-sketch` @ `918318e7`
(`assembly-reconstruction/anastylos_reconstruction/schematic.py`,
`contour.py:107/148/160`); Artefakte `C:\tmp\anastylos-explosion-m1\schematic\`
(Paket `cae31223…`, `contour_proof_sheet_v2.jpg`, Masken/Kontur-JSONs,
`frame-hunt` Crops). Framebindung aus `id-reconciliation/P2-source.json` +
`P3-source.json` (Video-SHA, Frame/PTS, Crop-SHA).

## Verworfene Wege (bleiben verworfen)

Modell-Korrespondenz (SAM/Muse/VLM, Tracker, MATCHER-v1, Re-ID); Tiefschrauben aus
Overhead; weiteres FULL-01/SNES-01-Tuning; Camera/CAD/Multi-Kamera vor Deadline.
SAM: 0 verifizierte Korrespondenzen, Teilekonzepte scheitern; GrabCut-Front 77,25 s
ohne Echtzeitversprechen. CAD/Mesh-Freeze bleibt: keine Ausweitung ohne
spezifizierte Geometrie + Rechte + Gates.

## Konkrete Zeichnungsfehler (A2, gelesen, nicht konsolidiert)

Quelle/Maske: Rückschale gehalten (Enden fingerverdeckt), Platine in situ
(Hände/Kabel), Front weitgehend handfrei aber Schatten links + knapper Untenrand.
Frontmaske teils besser, Rückschale nimmt Hand/ROI mit + schließt Enden künstlich,
PCB-Maske großflächig falsch, Kabel fast nur Strich. Skin-HSV nur Current-Frame.
Vereinfachung `contour.py:148`: jeder n-te Randpunkt, keine Fehlerschranke
(Front 28, Platine 12, Rückschale 11). Koordinaten: Crops `raw_phone_frame_px`,
nicht entzerrte Blatt-mm; Paket schematisch ~150, PCB als Rechteck; keine
vollständige Crop→Profil-Transformkette. Orientierung: Innen-Front + Außen-Back +
Platine auf +X ohne bestätigte Blickseite/Schichtordnung; 90° ≠ gemeinsame Seite,
Spiegelung braucht asymmetrische Landmarke. Layout `schematic.py:219`: Inset
12/34/120/95 vs. Haupt 20/38 kollidiert; Faktor-0-Überlagerung ist erwartete
2D-Projektion. Paket-SHA + 3/3 Crop-Hashes belegen Bytes, nicht Geometrie.

## Kleinster nächster Versuch (Frontkontur, NOT_TESTED)

Ein reales Teil, zunächst keine Neuaufnahme: P3 Frame 300 Frontschale (mit
Membranen), nur äußere sichtbare Schalenkontur. Vollframe vs. Tight-Crop prüfen;
reicht der Rand nicht → genau eine handfreie Übersicht, kein automatisches Schließen.
Quell-/Crop-/Maskenhash binden; manuelle Referenz des sichtbaren Außenrands (Schatten
raus) vor Auswertung fixieren; gespeicherte Maske + 28-Punkte-Kontur dagegen;
ggf. klassisch mit wenigen Seeds korrigieren; ohne anisotropen Fit in ein freies
Ansichtsfenster, Foto-Overlay + Seitenansicht, Innenmarken separat schematisch,
alt vs. neu auf gleichem Bild. Schwellen (unvalidiert): 95-%-Rand ≤2 % BBox-Diagonale
auf sichtbarem Rand, keine Finger/Schatten, halbierter Fehler vs. freies Schema,
keine Titelkollision, Operator bestätigt. Stopp bei unklarer Referenz,
abgeschnittenem/verdecktem Rand oder verfehlter Schwelle nach einer Runde.
Unsichtbares bleibt `unknown`; keine mm-/Passungsaussage.

Danach (getrennt, je eigens autorisieren): Kontur-Abnahme/Import; Layout-/Transform-
prüfung (synthetisch, asymmetrische L-Form); begrenzter Capture-Gap-Planer read-only
(z. B. “Place the selected back shell alone … exterior side up …”). Nur via
Bild-/Masken-/Konturhashes + Auswahlbindung übergeben.

## Fragen/Hypothesen vs. belegt

Belegt: E2-Protokoll, bounded Capture, Offline-Suites, STORED-IN-V2-01 (ein Call),
Hashbindungen (mit CRLF-Vorbehalt), partieller A2-Frontgewinn (visuell, ungemessen).
Hypothese: H-001 Protokoll-Beobachtbarkeit, H-002 Live-Fragen billiger,
H-003 Streiflicht; KnowNo begründet Fragezeitpunkt, nicht Kamerawahl; keine
vergleichende GrabCut/Hintergrund/Matting-Studie im Bestand; Capture-Schließloop,
Referenz-CAD-Nutzen und 3D-Pfade unbewiesen. Keine kalibrierte Konfidenz aus
Entwicklungsaufnahmen.
