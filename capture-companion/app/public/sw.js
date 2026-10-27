/* ANASTYLOS Capture service worker: offline-capable app shell.
 *
 * Strategy (no build-time asset list needed):
 * - install: pre-cache the app shell entry points.
 * - navigations: network first, falling back to the cached shell.
 * - same-origin GET assets: cache first, network fallback, cache on success.
 * - everything else (non-GET, cross-origin): untouched passthrough.
 *
 * Bump CACHE_VERSION to invalidate the shell on release.
 */

const CACHE_VERSION = 'anastylos-capture-shell-v1';

const SHELL = ['./', './index.html', './manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_VERSION);
      await cache.addAll(SHELL.map((path) => new URL(path, self.registration.scope).toString()));
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      (async () => {
        try {
          const fresh = await fetch(request);
          if (fresh.ok) {
            const cache = await caches.open(CACHE_VERSION);
            cache.put(request, fresh.clone()).catch(() => undefined);
          }
          return fresh;
        } catch {
          const cache = await caches.open(CACHE_VERSION);
          const shell =
            (await cache.match(request)) ??
            (await cache.match(new URL('./', self.registration.scope).toString()));
          if (shell) return shell;
          return new Response('ANASTYLOS Capture is offline and the app shell is not cached yet.', {
            status: 503,
            headers: { 'content-type': 'text/plain' },
          });
        }
      })(),
    );
    return;
  }

  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE_VERSION);
      const hit = await cache.match(request);
      if (hit) return hit;
      const fresh = await fetch(request);
      if (fresh.ok) cache.put(request, fresh.clone()).catch(() => undefined);
      return fresh;
    })(),
  );
});
