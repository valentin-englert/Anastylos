# Third-party inventory: capture-companion/app

The shipped PWA has **zero runtime dependencies**: the production bundle
(`dist/assets/*.js`) is built solely from first-party TypeScript, and the
ZIP writer, SHA-256, PNG icon generator and service worker are all
hand-written for this module. Photos and evidence never touch third-party
code at runtime.

The table below lists every **development-only** dependency: build, type
checking and tests. All are pinned exact in `package.json` with a
committed `package-lock.json` (lockfile v3, 85 entries including the root
and transitive packages).

| Dependency | Exact version | License (from package metadata) | Role |
|---|---|---|---|
| vite | 8.3.5 | MIT | dev server + production bundler |
| typescript | 7.0.2 | Apache-2.0 | static type checking (`tsc`) |
| vitest | 5.0.5 | MIT | unit test runner |
| fake-indexeddb | 6.2.6 | Apache-2.0 | IndexedDB emulation for storage tests only |
| @types/node | 26.6.5 | MIT | Node type declarations for tests/build scripts |

Transitive dependencies are confined to these five tools' own requirements
(the largest share is Vite/Vitest's build graph plus TypeScript's native
platform package `@typescript/typescript-win32-x64`). The full transitive
closure with resolved versions is recorded in `package-lock.json`; no
transitive package ships in the PWA bundle.

License notes: MIT and Apache-2.0 are permissive licenses requiring
preservation of copyright/license notices, which live with their
distributions in `node_modules/`. No GPL/AGPL code is introduced. No
telemetry, font, icon, CSS or analytics package is used. The Python
validator (`capture-companion/python/`) is standard-library-only and adds
no dependency at all.

Regenerating this table after a version bump:

```powershell
cd capture-companion/app
node -e "for (const d of ['vite','typescript','vitest','fake-indexeddb','@types/node']) { const p = require('./node_modules/' + d + '/package.json'); console.log(d, p.version, '|', p.license); }"
```

Root compliance note: the root SPDX inventory already lists the five
direct dependencies above as `pkg:npm` entries (added during the
feature-batch integration), with one grouped review item for the
transitive closure, and the root `THIRD_PARTY_NOTICES.md` carries the
matching human-readable rows. The root checker compares only
`pkg:pypi` entries, so it stays green across npm bumps — but after
any version bump, update the `pkg:npm` rows in
`tools/compliance/spdx.json` and the frontend table in
`THIRD_PARTY_NOTICES.md` by hand (same versions as `package.json`,
same licenses as the installed `node_modules` metadata).
