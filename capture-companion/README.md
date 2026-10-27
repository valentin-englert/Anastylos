# ANASTYLOS CAPTURE — offline-first evidence companion (prototype v0.1.0)

A mobile-first Progressive Web App for photographing, documenting and
packaging physical components during disassembly, plus a Python
standard-library validator that checks the evidence packages before
ANASTYLOS Core ever sees them.

The whole capture → document → export journey works **without any cloud
connection, account or AI model**. No photo leaves the device except
through an explicit operator export.

- App: `capture-companion/app/` (vanilla TypeScript + Vite, zero runtime
  dependencies — see `app/THIRD_PARTY.md`)
- Validator: `capture-companion/python/anastylos_capture_validate.py`
  (standard library only)
- Data contract: [`SCHEMA.md`](SCHEMA.md)
- Future sync design (not implemented): [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md)
- Honest boundaries: [`LIMITATIONS.md`](LIMITATIONS.md)

Existing ANASTYLOS recognition modules and shared root files are untouched;
everything new lives under `capture-companion/`.

## Quickstart

Prerequisites: Node.js 20+ with npm, and Python 3.12 for the validator.

```powershell
cd capture-companion/app
npm install          # exact-pinned dev toolchain, no runtime deps
npm run dev          # serve with hot reload at http://localhost:5173/
```

Production build and local preview:

```powershell
npm run build        # emits app/dist/ (regenerable, not for review)
npm run preview      # serves dist/ at http://localhost:4173/
```

Open the printed URL in a browser. `http://localhost` counts as a secure
context, so the live camera works there (see below).

## Tests

```powershell
cd capture-companion/app
npm test             # 66 vitest tests: schema, storage, zip, export, csv, intake
npm run typecheck    # tsc --noEmit, must stay clean
```

```powershell
# From the repository root:
py -3.12 -m unittest discover -s capture-companion/python/tests -v
```

The app suite includes a cross-language round trip
(`tests/roundtrip.test.ts`): it builds a real export package and, when a
Python interpreter is on PATH, validates it with the actual validator CLI.

Validate an exported package by hand:

```powershell
py -3.12 capture-companion/python/anastylos_capture_validate.py validate .\anastylos-capture-ab12cd34.zip
py -3.12 capture-companion/python/anastylos_capture_validate.py validate .\pkg.zip --json
py -3.12 capture-companion/python/anastylos_capture_validate.py validate .\pkg.zip --extract .\staged  # empty dir only
```

Exit codes: `0` valid, `1` invalid package, `2` usage/I-O error. The
validator never modifies `memory.jsonl` and never asserts relationships.

## Mobile-device testing

### 1. Serve the app so the phone can reach it

- **Easiest (full camera support):** run `npm run dev` on a PC, then open
  the PC's address on the phone through an HTTPS tunnel, or deploy `dist/`
  to any HTTPS static host. Installed as a PWA ("Add to Home Screen" /
  "Install app"), it then works fully offline.
- **Same-PC browser testing:** `http://localhost:5173/` (dev) or
  `http://localhost:4173/` (preview) — live camera works on localhost.
- **Plain-HTTP LAN** (`http://192.168.x.y:5173/`): the app loads and every
  feature except the *live* camera works; the camera panel explains this
  and offers system-camera/gallery import instead. Hashing, storage and
  export are unaffected (SHA-256 has no secure-context dependency).

### 2. What to confirm on a real phone

1. Install prompt appears (Chrome/Edge Android, Safari "Add to Home
   Screen"); the installed app opens standalone and survives airplane
   mode after one online load.
2. Create a session; photograph front/back/detail of a component; accept,
   retake, add via gallery; finish and review in the inspector.
3. Reload mid-session: sessions, photos and metadata persist (IndexedDB).
4. Add markings, category, a measurement with units, a quantity; advance
   review state; confirm the BOM screen shows states honestly.
5. Export a package; transfer the ZIP to a PC; run the validator — expect
   `status: valid` and both captures accepted for review.
6. Deny camera permission once: the recovery instructions must match the
   actual OS/browser settings path.

### Browser-support requirements

| Capability | Chrome/Edge 90+ | Firefox 90+ | Safari 16+ |
|---|---|---|---|
| App shell, storage, export | Yes | Yes | Yes |
| Live camera (`getUserMedia`) | Yes | Yes | Yes (HTTPS/localhost) |
| Sensor stills (`ImageCapture`) | Yes | Partial (falls back to frame grab) | Partial (falls back to frame grab) |
| Tap-to-focus / torch | Device-dependent | Rarely | Rarely |
| PWA install + offline | Yes | Partial (no install prompt) | Add to Home Screen |

Anything unsupported degrades to an explained fallback — never a silent
dead end. See `LIMITATIONS.md` for details.

## Architecture (10-second tour)

```
app/src/
  lib/          # UI-free core: schema, storage backends, repository,
                # zip writer, export builder, csv, photo intake,
                # provider-neutral identify interface
  ui/           # shell/router + 5 views (capture, sessions, inspector,
                # bom, export) + camera controller + monochrome styles
  main.ts       # boot: open repo, build shell, register service worker
app/public/     # manifest, hand-written service worker, generated icons
app/tests/      # vitest suite (node env) + independent ZIP test oracle
app/scripts/    # zero-dependency PNG icon generator
python/         # stdlib-only validator CLI + unittest suite
```

Storage: IndexedDB (`sessions`, `observations`, `images`, `binaries`,
`meta`), with a memory fallback that warns loudly. Corrupt records are
quarantined and reported, never silently deleted; deletion is always
explicit and confirmed.

## Compliance

Frontend/build attribution lives in `app/THIRD_PARTY.md` (5 pinned
dev-only packages, MIT/Apache-2.0, zero runtime deps). The root checker
and SPDX inventory cover only the Python calibration environment and are
unaffected; shared compliance files were left untouched — the module doc
reports what a future root-inventory update would add. No LICENSE file
was created: licensing remains deferred per repository policy.
