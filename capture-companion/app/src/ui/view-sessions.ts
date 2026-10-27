/** Sessions: list, create, rename, link, and chronological observations. */

import { hashToRoute, type AppContext } from './shell.js';
import { button, el, on } from './dom.js';
import { formatDateTime, shortId } from './format.js';

function thumbUrl(ctx: AppContext, imageId: string, urls: string[]): Promise<string | null> {
  return ctx.repo
    .getPreview(imageId)
    .then(async (preview) => preview ?? (await ctx.repo.getOriginal(imageId)))
    .then((blob) => {
      if (!blob) return null;
      const url = URL.createObjectURL(blob);
      urls.push(url);
      return url;
    });
}

export async function renderSessionsView(
  outlet: HTMLElement,
  ctx: AppContext,
): Promise<(() => void) | void> {
  const urls: string[] = [];
  const root = el('section', { class: 'page', 'aria-label': 'Sessions' });
  root.append(el('h1', {}, 'Sessions'));

  const sessions = await ctx.repo.listSessions();
  if (sessions.length === 0) {
    root.append(buildEmptySessions());
    const onlyCorrupt = buildCorruptPanel();
    if (onlyCorrupt) root.append(onlyCorrupt);
    outlet.append(root);
    return () => {
      for (const url of urls) URL.revokeObjectURL(url);
    };
  }

  root.append(buildCreateRow());

  const list = el('ul', { class: 'session-list' });
  for (const session of sessions) {
    const observations = await ctx.repo.listObservations(session.session_id);
    let photos = 0;
    let thumb: string | null = null;
    for (const obs of observations) {
      const images = await ctx.repo.listImages(obs.observation_id);
      photos += images.length;
      if (!thumb && images[0]) thumb = await thumbUrl(ctx, images[0].image_id, urls);
    }
    const current = ctx.selection.sessionId === session.session_id;
    const item = el('li', { class: `session-card${current ? ' session-card-current' : ''}` });
    const link = el('a', { class: 'session-link', href: `#/session/${session.session_id}` });
    if (thumb) link.append(el('img', { class: 'session-thumb', src: thumb, alt: '' }));
    else link.append(el('span', { class: 'session-thumb session-thumb-empty', 'aria-hidden': 'true' }, '—'));
    const text = el('span', { class: 'session-text' });
    text.append(
      el('span', { class: 'session-name' }, session.name),
      el(
        'span',
        { class: 'session-meta muted' },
        `${observations.length} component${observations.length === 1 ? '' : 's'} · ${photos} photo${photos === 1 ? '' : 's'} · updated ${formatDateTime(session.updated_at)}`,
      ),
    );
    link.append(text);
    if (current) link.append(el('span', { class: 'session-current' }, 'Current'));
    item.append(link);
    const actions = el('div', { class: 'session-actions' });
    if (current) {
      actions.append(el('a', { class: 'btn btn-primary', href: '#/capture' }, 'Resume capture'));
    } else {
      const setCurrent = button('Set current', { kind: 'quiet' });
      on(setCurrent, 'click', async () => {
        await ctx.setSelection({ sessionId: session.session_id });
        ctx.refresh();
      });
      actions.append(setCurrent);
    }
    item.append(actions);
    list.append(item);
  }
  root.append(list);

  function buildCreateRow(): HTMLElement {
    const row = el('div', { class: 'field-row' });
    const nameInput = el('input', {
      type: 'text',
      placeholder: 'New session name',
      'aria-label': 'New session name',
      maxlength: '120',
    }) as HTMLInputElement;
    const createBtn = button('Create session', { kind: 'primary' });
    wireCreate(nameInput, createBtn);
    row.append(nameInput, createBtn);
    return row;
  }

  function buildEmptySessions(): HTMLElement {
    const empty = el('section', { class: 'empty', 'aria-label': 'No sessions' });
    empty.append(
      el('h2', {}, 'No sessions yet'),
      el('p', { class: 'lede' }, 'A session groups one teardown: every part you photograph, mark and measure. Name your first session to begin.'),
      buildCreateRow(),
    );
    return empty;
  }

  function wireCreate(nameInput: HTMLInputElement, createBtn: HTMLButtonElement): void {
    on(createBtn, 'click', async () => {
      const name = nameInput.value.trim();
      if (!name) {
        ctx.notice('Name the session first.', 'error');
        return;
      }
      try {
        const created = await ctx.repo.createSession(name);
        await ctx.setSelection({ sessionId: created.session_id });
        ctx.navigate({ name: 'session', id: created.session_id });
      } catch (error) {
        ctx.notice(error instanceof Error ? error.message : String(error), 'error');
      }
    });
  }

  function buildCorruptPanel(): HTMLElement | null {
    const corrupt = ctx.repo.corruptRecords();
    if (corrupt.length === 0) return null;
    const panel = el('section', { class: 'panel panel-warn', 'aria-label': 'Unreadable records' });
    panel.append(
      el('h2', {}, `Unreadable records (${corrupt.length})`),
      el(
        'p',
        { class: 'muted' },
        'These records failed validation and are skipped everywhere. Nothing was deleted; purge them explicitly once reviewed.',
      ),
    );
    for (const record of corrupt) {
      const row = el('div', { class: 'corrupt-row' });
      row.append(el('span', {}, `${record.store} · ${shortId(record.key)} · ${record.issues[0] ?? 'invalid'}`));
      const purge = button('Purge', { kind: 'danger' });
      on(purge, 'click', async () => {
        if (!window.confirm(`Permanently purge this unreadable ${record.store} record? This cannot be undone.`)) return;
        await ctx.repo.purgeCorrupt(record.store, record.key);
        ctx.refresh();
      });
      row.append(purge);
      panel.append(row);
    }
    return panel;
  }

  const corruptPanel = buildCorruptPanel();
  if (corruptPanel) root.append(corruptPanel);

  outlet.append(root);
  return () => {
    for (const url of urls) URL.revokeObjectURL(url);
  };
}

export async function renderSessionDetailView(
  outlet: HTMLElement,
  ctx: AppContext,
): Promise<(() => void) | void> {
  const urls: string[] = [];
  const route = hashToRoute(window.location.hash);
  const sessionId = route.name === 'session' ? route.id : ctx.selection.sessionId;
  const root = el('section', { class: 'page page-detail', 'aria-label': 'Session detail' });

  if (!sessionId) {
    root.append(el('h1', {}, 'No session selected'), el('p', { class: 'muted' }, 'Choose a session first.'));
    const back = button('Sessions', { kind: 'primary' });
    on(back, 'click', () => ctx.navigate({ name: 'sessions' }));
    root.append(back);
    outlet.append(root);
    return;
  }
  const session = await ctx.repo.getSession(sessionId);
  if (!session) {
    root.append(
      el('h1', {}, 'Session not found'),
      el('p', { class: 'muted' }, 'It may have been deleted or failed validation.'),
    );
    const back = button('Sessions', { kind: 'primary' });
    on(back, 'click', () => ctx.navigate({ name: 'sessions' }));
    root.append(back);
    outlet.append(root);
    return;
  }

  root.append(el('h1', {}, session.name));
  root.append(el('p', { class: 'muted' }, `Created ${formatDateTime(session.created_at)} · id ${shortId(session.session_id)}`));
  if (ctx.selection.sessionId === session.session_id) {
    root.append(el('p', {}, el('span', { class: 'session-current' }, 'Current session')));
  }

  const actions = el('div', { class: 'button-row' });
  const useBtn = button('Set current', { kind: ctx.selection.sessionId === session.session_id ? 'quiet' : 'primary' });
  on(useBtn, 'click', async () => {
    await ctx.setSelection({ sessionId: session.session_id });
    ctx.notice(`“${session.name}” is now the current session.`);
    ctx.refresh();
  });
  const goCapture = async (): Promise<void> => {
    await ctx.setSelection({ sessionId: session.session_id, observationId: null });
    ctx.navigate({ name: 'capture' });
  };
  const captureBtn = button('Capture', {});
  on(captureBtn, 'click', () => void goCapture());
  const bomBtn = button('BOM draft', {});
  on(bomBtn, 'click', async () => {
    await ctx.setSelection({ bomSessionId: session.session_id });
    ctx.navigate({ name: 'bom' });
  });
  actions.append(useBtn, captureBtn, bomBtn);
  root.append(actions);

  // -- settings -----------------------------------------------------------
  const settings = el('section', { class: 'panel', 'aria-label': 'Session settings' });
  settings.append(el('h2', {}, 'Settings'));
  const renameInput = el('input', { type: 'text', value: session.name, maxlength: '120', 'aria-label': 'Session name' }) as HTMLInputElement;
  const linkInput = el('input', {
    type: 'text',
    value: session.assembly_memory_session ?? '',
    placeholder: 'Assembly Memory session id (optional)',
    'aria-label': 'Assembly Memory session id',
    maxlength: '200',
  }) as HTMLInputElement;
  const trayInput = el('input', {
    type: 'text',
    value: session.tray_default ?? '',
    placeholder: 'Default tray identifier (optional)',
    'aria-label': 'Default tray identifier',
    maxlength: '80',
  }) as HTMLInputElement;
  const saveBtn = button('Save settings', { kind: 'primary' });
  on(saveBtn, 'click', async () => {
    const name = renameInput.value.trim();
    if (!name) {
      ctx.notice('The session name cannot be empty.', 'error');
      return;
    }
    try {
      await ctx.repo.saveSession({
        ...session,
        name,
        assembly_memory_session: linkInput.value.trim() || null,
        tray_default: trayInput.value.trim() || null,
      });
      ctx.notice('Session saved.');
      ctx.refresh();
    } catch (error) {
      ctx.notice(error instanceof Error ? error.message : String(error), 'error');
    }
  });
  const field = (label: string, input: HTMLElement): HTMLElement =>
    el('label', { class: 'field' }, el('span', { class: 'field-label' }, label), input);
  settings.append(
    field('Name', renameInput),
    field('Assembly Memory session', linkInput),
    el('p', { class: 'field-hint' }, 'Optional link to a previously known session id in ANASTYLOS Core. Stored as text; nothing is fetched or created.'),
    field('Default tray', trayInput),
    el('p', { class: 'field-hint' }, 'Applied to new observations in this session. You can change it per component.'),
    saveBtn,
  );
  root.append(settings);

  // -- observations --------------------------------------------------------
  const observations = await ctx.repo.listObservations(session.session_id);
  const obsSection = el('section', { 'aria-label': 'Captured components' });
  obsSection.append(el('h2', {}, `Components (${observations.length})`));
  if (observations.length === 0) {
    const empty = el('div', { class: 'empty empty-compact' });
    const captureFirst = button('Capture first component', { kind: 'primary' });
    on(captureFirst, 'click', () => void goCapture());
    empty.append(
      el('h3', {}, 'No components yet'),
      el('p', { class: 'muted' }, 'Photograph the first part of this teardown.'),
      captureFirst,
    );
    obsSection.append(empty);
  }
  const list = el('ul', { class: 'obs-list' });
  for (const obs of observations) {
    const images = await ctx.repo.listImages(obs.observation_id);
    const item = el('li', { class: 'obs-item' });
    const link = el('a', { class: 'obs-link', href: `#/inspect/${obs.observation_id}` });
    if (images[0]) {
      const url = await thumbUrl(ctx, images[0].image_id, urls);
      if (url) link.append(el('img', { class: 'obs-thumb', src: url, alt: '' }));
    } else {
      link.append(el('span', { class: 'obs-thumb obs-thumb-empty', 'aria-hidden': 'true' }, '—'));
    }
    const summary = obs.markings.length > 0 ? obs.markings.map((m) => m.text).join(' · ') : 'Unmarked';
    link.append(
      el('span', { class: 'obs-text' },
        el('span', { class: 'obs-title' }, summary),
        el(
          'span',
          { class: 'obs-meta muted' },
          `${formatDateTime(obs.captured_at)} · ${images.length} photo${images.length === 1 ? '' : 's'} · ${obs.review_state}${obs.tray_reference ? ` · tray ${obs.tray_reference}` : ''}`,
        )),
    );
    item.append(link);
    list.append(item);
  }
  obsSection.append(list);
  root.append(obsSection);

  // -- danger zone ----------------------------------------------------------
  const danger = el('section', { class: 'panel panel-danger', 'aria-label': 'Delete session' });
  danger.append(el('h2', {}, 'Delete session'));
  const deleteBtn = button(
    observations.length > 0
      ? `Delete session and ${observations.length} component(s)`
      : 'Delete session',
    { kind: 'danger' },
  );
  on(deleteBtn, 'click', async () => {
    const confirmed = window.confirm(
      observations.length > 0
        ? `Delete “${session.name}” with ALL ${observations.length} component(s) and their photos? This cannot be undone.`
        : `Delete “${session.name}”? This cannot be undone.`,
    );
    if (!confirmed) return;
    try {
      await ctx.repo.deleteSession(session.session_id, { deleteObservations: true });
      if (ctx.selection.sessionId === session.session_id) {
        await ctx.setSelection({ sessionId: null, observationId: null });
      }
      ctx.navigate({ name: 'sessions' });
    } catch (error) {
      ctx.notice(error instanceof Error ? error.message : String(error), 'error');
    }
  });
  danger.append(
    el('p', { class: 'muted' }, 'Deletion is permanent and explicit. Export first if the evidence matters.'),
    deleteBtn,
  );
  root.append(danger);

  outlet.append(root);
  return () => {
    for (const url of urls) URL.revokeObjectURL(url);
  };
}
