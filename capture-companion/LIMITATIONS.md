# Known limitations (v0.1.0 prototype)

Honest boundaries of this iteration. Nothing here is presented as working
in the app when it is not.

## Not implemented (labeled as such in the UI)

- **Automatic identification.** No provider exists; the inspector's
  identification section states this and no photo leaves the device. The
  provider-neutral interface is declared in `app/src/lib/identify.ts`.
- **Network sync.** Export produces a local ZIP file only. The Sync panel
  says "unavailable"; the future protocol is designed in
  `SYNC_PROTOCOL.md` with no server code.
- **Ordering, catalogs, suppliers.** The BOM screen is a confirmed-data
  draft with CSV export. No part numbers, ratings, compatibility or
  purchasing — by design.

## Platform and browser constraints

- **Live camera needs a secure context** (HTTPS or `http://localhost`)
  and a browser with `getUserMedia`: current Chrome/Edge, Firefox or
  Safari. Over plain-HTTP LAN hosting the app falls back to file import
  with an explanation; export and hashing keep working because SHA-256 is
  dependency-free (`app/src/lib/sha256.ts`).
- **Tap-to-focus, torch and multi-camera switching are best-effort.**
  They probe `getCapabilities()` and hide or report when unsupported;
  desktop browsers typically lack all three, phones vary by model.
- **HEIC originals** (iOS default) are stored and exported byte-identical,
  but in-app thumbnails depend on the browser's HEIC decoding; where the
  browser cannot decode, the original is still preserved and exported.
- **Installability** needs a standards-serving host (correct MIME types,
  HTTPS outside localhost). `vite preview` and any static host qualify.
- **Storage quotas** are device- and browser-managed. Writes that exceed
  quota fail loudly and nothing is half-saved; very large sessions should
  be exported and trimmed by the operator.

## Data and workflow simplifications

- One operator, one device: no user accounts, no conflict resolution, no
  merge of packages from two phones (the validator does reject duplicate
  ids within a package).
- Sessions, observations and photos are edited on one screen each; there
  is no bulk edit, search, or filtering beyond per-session lists.
- Crop previews are rectangles only (no rotation, no perspective fix).
- The service worker caches the app shell for offline use but performs
  no background sync; an update requires loading the app once while
  online.
- Timestamps use the device clock; skewed clocks produce skewed
  `captured_at` values (validation checks format, not truth).

## Verification gaps

- The vitest suite (66 tests) covers schemas, storage on both backends,
  ZIP round-trips, export planning/building, CSV, photo intake, SHA-256
  vectors, and package structure; the Python leg of the round trip runs
  only where an interpreter exists.
- The Python unittest suite was authored with the validator but could
  not be executed in the capture branch's sandboxed build environment
  (no interpreter available). The later feature-batch integration run
  reports all 22 validator tests passing (see `../ARCHITECTURE.md`);
  re-run it locally before relying on it — see `README.md`.
- Camera, installability and offline-reload behavior were verified by
  code inspection, capability probing and build checks, not on physical
  devices in this session; the manual checklist in `README.md` covers
  what to confirm on a phone.
