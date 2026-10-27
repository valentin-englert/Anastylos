/** App shell: routing, layout, tab bar, persisted selection.
 *
 * Hash routes keep every screen deep-linkable without a server. Views render
 * into the outlet and may return a cleanup function (the capture view stops
 * the camera when the operator navigates away).
 */

import type { Repository } from '../lib/repository.js';
import { appVersion } from '../lib/schema.js';
import { button, clear, el, on } from './dom.js';
import { activeJourneyStop, journeyStops } from './journey.js';

export type Route =
  | { name: 'capture' }
  | { name: 'sessions' }
  | { name: 'session'; id: string }
  | { name: 'inspect'; id: string | null }
  | { name: 'bom' }
  | { name: 'export' }
  | { name: 'sync' };

export interface Selection {
  sessionId: string | null;
  observationId: string | null;
  bomSessionId: string | null;
}

export interface AppContext {
  repo: Repository;
  selection: Selection;
  navigate(route: Route): void;
  refresh(): void;
  setSelection(patch: Partial<Selection>): Promise<void>;
  notice(message: string, kind?: 'info' | 'error'): void;
}

type ViewRenderer = (outlet: HTMLElement, ctx: AppContext) => Promise<(() => void) | void>;

export function routeToHash(route: Route): string {
  switch (route.name) {
    case 'capture':
      return '#/capture';
    case 'sessions':
      return '#/sessions';
    case 'session':
      return `#/session/${route.id}`;
    case 'inspect':
      return route.id ? `#/inspect/${route.id}` : '#/inspect';
    case 'bom':
      return '#/bom';
    case 'export':
      return '#/export';
    case 'sync':
      return '#/sync';
  }
}

export function hashToRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (parts[0] === 'sessions') return { name: 'sessions' };
  if (parts[0] === 'session' && parts[1]) return { name: 'session', id: parts[1] };
  if (parts[0] === 'inspect') return { name: 'inspect', id: parts[1] ?? null };
  if (parts[0] === 'bom') return { name: 'bom' };
  if (parts[0] === 'export') return { name: 'export' };
  if (parts[0] === 'sync') return { name: 'sync' };
  return { name: 'capture' };
}

/** Secondary destinations stay out of the journey bar but keep their routes.
 *  They surface through contextual links and, on wide screens, a quiet rail link. */
const SECONDARY_TABS: Array<{ hash: string; label: string; route: Route['name'] }> = [
  { hash: '#/bom', label: 'BOM draft', route: 'bom' },
  { hash: '#/sync', label: 'Sync', route: 'sync' },
];

export function applyTheme(theme: string): void {
  document.documentElement.dataset['theme'] = theme;
  try {
    localStorage.setItem('ac-theme', theme);
  } catch {
    // Private mode: theme just won't persist.
  }
}

export function loadTheme(): string {
  try {
    return localStorage.getItem('ac-theme') ?? 'auto';
  } catch {
    return 'auto';
  }
}

export async function loadSelection(repo: Repository): Promise<Selection> {
  const [sessionId, observationId, bomSessionId] = await Promise.all([
    repo.getMeta('sel.session'),
    repo.getMeta('sel.observation'),
    repo.getMeta('sel.bomSession'),
  ]);
  return {
    sessionId: sessionId ?? null,
    observationId: observationId ?? null,
    bomSessionId: bomSessionId ?? null,
  };
}

export function createShell(
  root: HTMLElement,
  repo: Repository,
  views: Record<Route['name'], ViewRenderer>,
): AppContext {
  const header = el('header', { class: 'topbar' });
  const wordmark = el('a', { class: 'wordmark', href: '#/capture' });
  wordmark.append(
    el('span', { class: 'wordmark-main' }, 'ANASTYLOS'),
    el('span', { class: 'wordmark-sub' }, `CAPTURE · ${appVersion()}`),
  );
  const themeButton = button('Theme: auto', { kind: 'quiet', 'aria-label': 'Cycle color theme' });
  const cycleTheme = () => {
    const order = ['auto', 'light', 'dark'];
    const next = order[(order.indexOf(loadTheme()) + 1) % order.length]!;
    applyTheme(next);
    themeButton.textContent = `Theme: ${next}`;
  };
  on(themeButton, 'click', cycleTheme);
  themeButton.textContent = `Theme: ${loadTheme()}`;
  applyTheme(loadTheme());
  header.append(
    el('a', { class: 'skip', href: '#main' }, 'Skip to content'),
    wordmark,
    el('div', { class: 'topbar-right' }, themeButton),
  );

  const memoryBanner =
    repo.backendName === 'memory'
      ? el(
          'p',
          { class: 'banner banner-warn', role: 'alert' },
          'Persistent storage is unavailable in this browser: captures will NOT survive a reload. Export before leaving.',
        )
      : null;

  const noticeRegion = el('div', { class: 'notices', 'aria-live': 'assertive' });
  const main = el('main', { class: 'outlet', id: 'main', tabindex: '-1' });
  const tabbar = el('nav', { class: 'tabbar', 'aria-label': 'Primary' });

  root.append(header);
  if (memoryBanner) root.append(memoryBanner);
  root.append(noticeRegion, main, tabbar);

  let cleanup: (() => void) | void;

  const ctx: AppContext = {
    repo,
    selection: { sessionId: null, observationId: null, bomSessionId: null },
    navigate(route: Route) {
      const hash = routeToHash(route);
      if (window.location.hash === hash) {
        void render(hashToRoute(hash));
      } else {
        window.location.hash = hash;
      }
    },
    refresh() {
      void render(hashToRoute(window.location.hash));
    },
    async setSelection(patch: Partial<Selection>) {
      Object.assign(ctx.selection, patch);
      if (patch.sessionId !== undefined) {
        if (patch.sessionId) await repo.setMeta('sel.session', patch.sessionId);
        else await repo.setMeta('sel.session', '');
      }
      if (patch.observationId !== undefined) {
        if (patch.observationId) await repo.setMeta('sel.observation', patch.observationId);
        else await repo.setMeta('sel.observation', '');
      }
      if (patch.bomSessionId !== undefined) {
        if (patch.bomSessionId) await repo.setMeta('sel.bomSession', patch.bomSessionId);
        else await repo.setMeta('sel.bomSession', '');
      }
      // Normalize empty strings back to null.
      for (const key of ['sessionId', 'observationId', 'bomSessionId'] as const) {
        if (ctx.selection[key] === '') ctx.selection[key] = null;
      }
    },
    notice(message: string, kind: 'info' | 'error' = 'info') {
      const note = el(
        'p',
        { class: `notice notice-${kind}`, role: kind === 'error' ? 'alert' : 'status' },
        message,
      );
      const dismiss = button('Dismiss', { kind: 'quiet', 'aria-label': 'Dismiss notice' });
      on(dismiss, 'click', () => note.remove());
      note.append(dismiss);
      clear(noticeRegion);
      noticeRegion.append(note);
      if (kind === 'info') window.setTimeout(() => note.remove(), 6000);
    },
  };

  void loadSelection(repo).then((selection) => {
    // Drop empty-string sentinels left by clears.
    for (const key of ['sessionId', 'observationId', 'bomSessionId'] as const) {
      if (selection[key] === '') selection[key] = null;
    }
    ctx.selection = selection;
    void render(hashToRoute(window.location.hash));
  });

  async function render(route: Route): Promise<void> {
    if (cleanup) {
      try {
        cleanup();
      } catch {
        // View cleanup must never break navigation.
      }
      cleanup = undefined;
    }
    clear(main);
    main.append(el('p', { class: 'loading' }, 'Loading…'));
    renderTabs(route);
    try {
      const renderView = views[route.name];
      clear(main);
      cleanup = await renderView(main, ctx);
    } catch (error) {
      clear(main);
      main.append(
        el('section', { class: 'panel' },
          el('h1', {}, 'Something went wrong'),
          el('p', {}, error instanceof Error ? error.message : String(error)),
          (() => {
            const retry = button('Retry', { kind: 'primary' });
            on(retry, 'click', () => ctx.refresh());
            return retry;
          })()),
      );
    }
  }

  function renderTabs(route: Route): void {
    clear(tabbar);
    const active = activeJourneyStop(route.name);
    for (const stop of journeyStops(ctx.selection.observationId)) {
      const current = stop.id === active;
      const link = el(
        'a',
        {
          class: `tab${stop.id === 'capture' ? ' tab-primary' : ''}${current ? ' tab-active' : ''}`,
          href: stop.hash,
          ...(current ? { 'aria-current': 'page' } : {}),
        },
        stop.label,
      );
      tabbar.append(link);
    }
    for (const secondary of SECONDARY_TABS) {
      const current = secondary.route === route.name;
      tabbar.append(
        el(
          'a',
          {
            class: `tab tab-secondary${current ? ' tab-active' : ''}`,
            href: secondary.hash,
            ...(current ? { 'aria-current': 'page' } : {}),
          },
          secondary.label,
        ),
      );
    }
    void updateExportBadge();
  }

  async function updateExportBadge(): Promise<void> {
    try {
      const observations = await repo.listAllObservations();
      const pending = observations.filter((o) => o.export_history.length === 0).length;
      const exportTab = tabbar.querySelector('a[href="#/export"]');
      if (exportTab && pending > 0) {
        exportTab.append(el('span', { class: 'tab-badge', 'aria-label': `${pending} pending` }, String(pending)));
      }
    } catch {
      // Badge is a nicety; the Export screen carries the real numbers.
    }
  }

  window.addEventListener('hashchange', () => {
    void render(hashToRoute(window.location.hash));
  });

  return ctx;
}
