/** BOM preview: observations by session, confirmed-only CSV export. */

import { bomRowsForSession, bomRowsToCsv } from '../lib/csv.js';
import type { AppContext } from './shell.js';
import { button, downloadBlob, el, on } from './dom.js';
import { formatDateTime, shortId } from './format.js';

export async function renderBomView(outlet: HTMLElement, ctx: AppContext): Promise<void> {
  const root = el('section', { class: 'page', 'aria-label': 'BOM draft' });
  root.append(el('h1', {}, 'BOM draft'));

  const sessions = await ctx.repo.listSessions();
  if (sessions.length === 0) {
    const empty = el('div', { class: 'empty empty-compact' });
    const capture = button('Capture components', { kind: 'primary' });
    on(capture, 'click', () => ctx.navigate({ name: 'capture' }));
    empty.append(
      el('h2', {}, 'No sessions yet'),
      el('p', { class: 'muted' }, 'Capture components first; confirmed rows will draft the parts list.'),
      capture,
    );
    root.append(empty);
    outlet.append(root);
    return;
  }

  const selectedId =
    ctx.selection.bomSessionId ?? ctx.selection.sessionId ?? sessions[0]!.session_id;
  const picker = el('select', { 'aria-label': 'Session for BOM preview' }) as HTMLSelectElement;
  for (const session of sessions) {
    const option = el('option', { value: session.session_id }, session.name);
    if (session.session_id === selectedId) option.selected = true;
    picker.append(option);
  }
  on(picker, 'change', async () => {
    await ctx.setSelection({ bomSessionId: picker.value });
    ctx.refresh();
  });
  root.append(el('label', { class: 'field' }, el('span', { class: 'field-label' }, 'Session'), picker));

  const { rows, skippedUnconfirmed } = await bomRowsForSession(ctx.repo, selectedId, {});
  const confirmed = rows.filter((row) => row.reviewState === 'confirmed');

  root.append(
    el('p', { class: 'muted' },
      `${rows.length} observation${rows.length === 1 ? '' : 's'} · ${confirmed.length} confirmed. Observations are not quantities: only confirmed rows export.`),
  );

  if (rows.length === 0) {
    root.append(el('p', { class: 'muted' }, 'Nothing captured in this session yet.'));
  } else {
    const wrap = el('div', { class: 'table-wrap' });
    const table = el('table', { class: 'bom' });
    const head = el('tr', {});
    for (const label of ['Component', 'Class', 'Qty', 'Markings', 'Tray', 'Evidence', 'Captured']) {
      head.append(el('th', { scope: 'col' }, label));
    }
    table.append(el('thead', {}, head));
    const body = el('tbody', {});
    for (const row of rows) {
      const link = el('a', { href: `#/inspect/${row.observationId}` }, shortId(row.observationId));
      body.append(
        el('tr', {},
          el('td', {}, link),
          el('td', {},
            row.category ? el('span', {}, row.category + ' ') : '— ',
            el('span', { class: `state state-${row.categoryState}` }, row.categoryState)),
          el('td', {},
            row.quantity ? el('span', {}, row.quantity + ' ') : '— ',
            el('span', { class: `state state-${row.quantityState}` }, row.quantityState)),
          el('td', {}, row.markings || '—'),
          el('td', {}, row.trayReference || '—'),
          el('td', {}, `${row.photoCount} photo${row.photoCount === 1 ? '' : 's'} · ${row.reviewState}`),
          el('td', {}, formatDateTime(row.capturedAt))),
      );
    }
    table.append(body);
    wrap.append(table);
    root.append(wrap);
  }

  const exportBtn = button(
    confirmed.length > 0 ? `Export ${confirmed.length} confirmed row(s) as CSV` : 'Export confirmed rows as CSV',
    { kind: 'primary', disabled: confirmed.length === 0 ? 'true' : undefined },
  );
  on(exportBtn, 'click', async () => {
    const fresh = await bomRowsForSession(ctx.repo, selectedId, { confirmedOnly: true });
    if (fresh.rows.length === 0) {
      ctx.notice('No confirmed rows to export. Review and confirm components first.', 'error');
      return;
    }
    const csv = bomRowsToCsv(fresh.rows);
    const stamp = new Date().toISOString().slice(0, 10);
    downloadBlob(new Blob([csv], { type: 'text/csv' }), `anastylos-bom-${stamp}.csv`);
    ctx.notice(
      `BOM draft saved: ${fresh.rows.length} confirmed row(s). ${fresh.skippedUnconfirmed} unconfirmed observation(s) excluded.`,
    );
  });
  root.append(exportBtn);
  if (skippedUnconfirmed === 0) {
    root.append(
      el('p', { class: 'field-hint' }, 'Quantities come only from the operator-confirmed field — never from photo counts. No part numbers, ratings or suppliers are invented.'),
    );
  }

  outlet.append(root);
}
