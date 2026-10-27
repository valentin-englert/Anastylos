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

Root compliance note (for the repository owner, not actioned here): the
root checker (`tools/compliance/check.py`) currently inventories only the
Python calibration environment from `calibration/requirements.txt`, so it
is unaffected by these npm dev-dependencies. If the project later wants
npm packages in the root SPDX inventory, add a `pkg:npm` entry per direct
dependency above to `tools/compliance/spdx.json` plus a human-readable row
in `THIRD_PARTY_NOTICES.md`. Shared compliance files were deliberately
left untouched in this worktree.
