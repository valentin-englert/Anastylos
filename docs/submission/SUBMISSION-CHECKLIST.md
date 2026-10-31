# ANASTYLOS — Einreichungscheckliste (Stand ohne Veröffentlichung)

Basis: `docs/submission-handoff` @ `b522336d`. Keine Sichtbarkeitsänderung,
nichts veröffentlicht, kein Push. Diese Liste prüft nur nachvollziehbare
Unterlagen im Repo + genannte lokale Forschung (read-only).

Offizielle Teilnahmeunterlagen (Regeln, Devpost/Event-Seite, Video-/Formular-
vorgaben) liegen im Repo nicht vor (Suche nach submission/hackathon/Devpost/
deadline/video in Dateinamen + Inhalten: kein Treffer außer Entwicklungsprovenienz
und Forschungsdeadline). Alles ohne Beleg ist unten als **offen** markiert.

## 1. Meta-Nutzung — erfüllt (begrenzt, belegt)

- Modell `muse-spark-1.3-contributor`, Effort `max`, beide konstant;
  `POST https://api.meta.ai/v1/responses`, striktes `json_schema`
  (`tools/live/CONTRACT.md` §4, `tools/live/providers/README.md`).
- Echter Smoke-Run synthetisch: 1 Aufruf/Task, HTTP 200, 6,4 s, 29/548 Tokens;
  Fixtures in `tools/live/providers/tests/fixtures/recorded/` (4 Dateien).
- STORED-IN-V2-01 (31.10.2026): 1 autorisierter Realaufruf, Retries aus,
  exakte Ablage + Basis, beide Verifier ok; 8,672 s / 8,734 s; Budget 4/4.
  Entwicklungsdaten, kein Hardwarenachweis.
- Realer L2-Arm 29.10.2026: 588 Vorschläge (311 Cache, 277 bezahlt),
  0,40 M In / 0,68 M Out, Median 21,7 s / p95 64 s / Max 114 s; rejected
  (0,752 < P0 0,803). Forschung, kein Produkteinsatz.
- E2-Narration (beratend, 28.10.2026): 2/30 Calls, `check.py` ACCEPTED,
  ändert kein Urteil, kein Memory-Schreibzugriff (`docs/PROTO-01-NOUV.md` §11).
- Schutz: Schlüssel nur `MODEL_API_KEY`, nie geloggt; Text-nur minimierte
  Eingaben, keine Bilder; Contributor-Tier `store: true` (Speicherung/Training
  möglich) ist dokumentiert.

Offen: Live-Modellverhalten über obige Nachweise hinaus; keine Freigabe für
weitere Calls (Budget 0 Rest im STORED-IN-Fenster).

## 2. Öffentliche Repo-Zugänglichkeit — offen (nicht geändert)

- `README.md:200-203`: Quelle soll öffentlich einsehbar sein; Wiederverwendung
  unentschieden, keine `LICENSE`, keine Open-Source-Lizenz behauptet.
- `AGENTS.md` + `ARCHITECTURE.md`: Lizenzierung bewusst aufgeschoben, keine
  Root-`LICENSE` anlegen.
- Remote: `origin https://github.com/valentin-englert/Anastylos.git`
  (fetch/push, nur URL gelesen). Sichtbarkeit hier nicht verifiziert und nicht
  geändert.
- SAM-Parser (vendored, `LicenseRef-SAM-License`): Owner-Review 29.10.2026
  genehmigt nur das private Repository; §1.b.ii (SAM-Nennung in Publikationen)
  bleibt Pflicht; COCO-Notice in alter Historie (`3e8cb59c…89aad323`) fehlt
  (forward-only ergänzt, keine History-Rewrites).
- ML Kit + Android-Closure: APK-Weitergabe über Workstation hinaus braucht
  Menschenlese der ML-Kit-Terms + POM-Inventur (2 offene Reviews).

Ergebnis: Einsehbarkeit angestrebt, Freigabe/Rechte ungeklärt. Kein Publish
durch diesen Auftrag.

## 3. Event-Zeitraum / Frist — nur Einzelquelle, unbestätigt

- Einzige Fristquelle im geprüften Bestand:
  `ANASTYLOS-Research-Pass-01/DECISION-MEMO.md:5` und
  `research-to-code-gap.md:6`: **2026-11-04 21:00 ET**.
- Keine zweite Quelle im Repo (kein Regelwerk, keine Event-Seite, kein
  Formular). Jahr/Zone nur aus dieser Quelle übernommen.
- Fehlend/offen: Eventname, Start, Einreichfenster, Zeitzonen-Bestätigung durch
  Veranstalter, offizielle Deadline-Bestätigung. Keine widersprüchliche zweite
  Datumsangabe gefunden; fehlende Bestätigung ausdrücklich vermerkt.

## 4. Rechte an Assets — teils belegt, teils offen

- Eigene Rechte: aufgeschoben, keine Lizenz, keine Wiederverwendungszusage über
  Ansicht hinaus.
- Compliance: `py -3.12 tools/compliance/check.py --json` → 0 Errors, 1 Warning
  (kein Worktree-`.venv`), 18 offene Reviews (FFmpeg-DLL LGPL-2.1 Build-Flags,
  Pillow-Mapping + statische Libs, Pi-Pakete, Tesseract-DLLs, Cairo-nativ,
  npm-transitiv, ML Kit, Android-Closure). Inventare nicht beschönigt.
- Verpflichtungen bei Redistribution gebauter Umgebungen (nicht Quellteilung):
  Apache-2.0 NOTICE, LGPL-3.0 (CairoSVG) / LGPL-2.1 (FFmpeg-DLL) Texte + Relink,
  BSD/MIT/PSF-Texte, ML-Kit-/Android-/npm-Prüfungen (s. `THIRD_PARTY_NOTICES.md`).
- CAD-Worker-Closure (45 Pins, ~1,2 GB): `casadi` LGPLv3+, OCCT-Bundle
  (LGPL-2.1-mit-Ausnahme prüfen), 3 leere Lizenzfelder — Owner-Review offen,
  keine SPDX-Änderung.
- PWA-Bundle: nur eigener TypeScript-Code, keine Runtime-Deps, keine Telemetrie;
  Android-APK enthält Runtime-Libs (Terms gelten dort).

Offen: alle 18 Reviews + CAD-Lizenzfragen vor Redistribution/Publikation;
keine neuen Rechte eingeholt.

## 5. Dreiminütiges Video — offen (Drehbuch liegt vor)

- Kein Video im Repo gefunden. `docs/submission/DEMO-SCRIPT.md` (englisch,
  ≤180 s, 5 Szenen mit Zeit, Artefakten, Fallback) ist die Vorlage.
- Anforderungen an Format/Upload/Untertitel/Länge sind ohne offizielle
  Unterlagen offen. Keine geskriptete Offlineantwort als Live-Leistung
  verwenden (Labelpflicht im Drehbuch).

## 6. Einreichungsbestandteile — offen (keine offizielle Liste)

Vorhanden (ohne Gewähr auf Vollständigkeit):

- Code @ `b522336d` (sauber, E3-B-Blobs 20/20, Raw 19/20 mit CRLF-Vorbehalt).
- `docs/submission/STATUS-AND-EVIDENCE.md`, `DEMO-SCRIPT.md`,
  `CLAUDE-HANDOFF.md`, diese Checkliste, `INTEGRATION-NOTES.md`.
- Belegdocs: `docs/PROTO-01-NOUV.md` §9–§12, `docs/PROTO-01-E3B.md`,
  `docs/integration/GUIDE-03.md` + `HARDWARE-03.md`, `INTEGRATION-STATUS.md`,
  `THIRD_PARTY_NOTICES.md` + `tools/compliance/spdx.json`.

Offen: offizielles Formular, Repo-Link/Visibility-Nachweis, Video-Upload,
Asset-Rechteerklärungen, Modellnutzungs-Nachweisformat, zusätzliche
Kurzbeschreibung/README-Anpassungen (diese nicht hier geändert, s. Integrationsnotiz).
