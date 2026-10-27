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

  const createRow = el('div', { class: 'field-row' });
  const nameInput = el('input', {
    type: 'text',
    placeholder: 'New session name',
    'aria-label': 'New session name',
    maxlength: '120',
  }) as HTMLInputElement;
  const createBtn = button('Create session', { kind: 'primary' });
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
  createRow.append(nameInput, createBtn);
  root.append(createRow);

  const sessions = await ctx.repo.listSessions();
  if (sessions.length === 0) {
    root.append(
      el('p', { class: 'muted' }, 'No sessions yet. Create one to start capturing components.'),
    );
  }
  const list = el('ul', { class: 'session-list' });
  for (const session of sessions) {
    const observations = await ctx.repo.listObservations(session.session_id);
    let photos = 0;
    for (const obs of observations) photos += (await ctx.repo.listImages(obs.observation_id)).length;
    const item = el('li', { class: 'session-item' });
    const link = el('a', { class: 'session-link', href: `#/session/${session.session_id}` });
    link.append(
      el('span', { class: 'session-name' }, session.name),
      el(
        'span',
        { class: 'session-meta muted' },
        `${observations.length} component${observations.length === 1 ? '' : 's'} · ${photos} photo${photos === 1 ? '' : 's'} · updated ${formatDateTime(session.updated_at)}`,
      ),
    );
    if (ctx.selection.sessionId === session.session_id) {
      link.append(el('span', { class: 'session-current' }, 'current'));
    }
    item.append(link);
    list.append(item);
  }
  root.append(list);

  const corrupt = ctx.repo.corruptRecords();
  if (corrupt.length > 0) {
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
    root.append(panel);
  }

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
  const root = el('section', { class: 'page', 'aria-label': 'Session detail' });

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

  const actions = el('div', { class: 'button-row' });
  const useBtn = button('Set current', { kind: ctx.selection.sessionId === session.session_id ? 'quiet' : 'primary' });
  on(useBtn, 'click', async () => {
    await ctx.setSelection({ sessionId: session.session_id });
    ctx.notice(`“${session.name}” is now the current session.`);
    ctx.refresh();
  });
  const captureBtn = button('Capture', {});
  on(captureBtn, 'click', async () => {
    await ctx.setSelection({ sessionId: session.session_id, observationId: null });
    ctx.navigate({ name: 'capture' });
  });
  actions.append(useBtn, captureBtn);
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
    obsSection.append(el('p', { class: 'muted' }, 'Nothing captured yet in this session.'));
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
