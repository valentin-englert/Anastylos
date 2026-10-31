# Integrationsnotiz — Vorschläge für ARCHITECTURE.md / README.md (nicht umgesetzt)

Branch `docs/submission-handoff` @ `b522336d`. Diese Datei sammelt nur Vorschläge;
`ARCHITECTURE.md` und Root-`README.md` wurden auftragsgemäß nicht geändert.

## ARCHITECTURE.md

1. Modulindex `tools/agent/` (Zeile ~189): Tests „57 offline tests“ → „89 offline
   tests (Storage V2 @ `04a97968`, inkl. Auswahl + Selected-Guide + Lifecycle)“;
   Zusatz: „STORED-IN-V2-01: 1 autorisierter Realaufruf, 8,672/8,734 s, Budget 4/4,
   Entwicklungsdaten, kein Hardwarenachweis.“ Keine Validierungsaussage ändern.
2. Modulindex `tools/live/` (Zeile ~190): „providers 27“ → „providers 33 PASS
   (Storage-V2-Anweisungen nur für selektierten Kontext v2, sonst unverändert)“;
   L2-Zeile bleibt („rejected, P0 aktiv“).
3. „Actual integration status“ (M0–M2 nicht auf main): branchgenau fassen —
   main `80eb2030` ohne Agent; Submissionsbranch `b522336d` enthält M0–M2 +
   Stabilisierung (`b7f2d76b`) + Lifecycle (`2efc02c8`) + Auswahl (`37a268e2`) +
   Selected-Muse (`2144117b`) + Storage V2 (`04a97968`), je mit Suite + E3-B-Blob-
   Check. Keine Main-Aussage überschreiben.
4. E3-B-Hashstand: zu §2-Freeze ergänzen „20/20 Git-Blobs; 19/20 rohe Checkout-
   Bytes wegen CRLF in `tools/proto-01/e3b_grid_frame.py` (Git sauber, nicht
   normalisiert, kein Raw-Byte-PASS)“. Quelle: Review 31.10.2026 + Agent-README.
5. CAD/Mesh-Zeilen: explizit „kein belegtes reales FULL-01-Modell“ + Mesh Case A
   „0,2375 vs. 0,05 retained“ beibehalten; Freeze-Begriff aus Review übernehmen.
6. Capture-Gaps: `feat/capture-gap-planning` @ `248f5223` (ein Commit vor `b522336d`,
   6 Dateien unter `tools/agent/capture_gaps/`) als geplant/offen erwähnen, nicht
   als integriert. Session-UI/Report (M3) bleiben geplant.

## Root-README.md

1. „Meta integration status / Muse proposer seam“: einen Satz ergänzen —
   „Bounded demo STORED-IN-V2-01 (one call, exact storage + basis, 8.7 s,
   budget exhausted, development data); live behavior beyond smoke + this demo
   stays pending.“ Keine Live-Validierung behaupten.
2. „M0–M2 agent orchestrator lives on `integration/agent-v1`, not part of main“:
   branchgenau ergänzen — „present on `docs/submission-handoff` @ `b522336d`
   (offline suites + bounded demo), pending main consolidation.“
3. „Source availability“: Wortlaut („intended publicly viewable, reuse undecided,
   no LICENSE“) beibehalten; keinen Lizenz-/Sichtbarkeitswechsel andeuten.
   SAM-Privat-Repo-Vorbehalt bleibt in `THIRD_PARTY_NOTICES.md` (kein README-Duplikat).

Kein Vorschlag ändert Verträge, Koordinaten oder Event-Schemas.
