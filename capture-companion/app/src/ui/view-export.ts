/** Export: pending evidence, validation, local package download.
 *
 * Wording is deliberate: a download is a local file, never "sync".
 * Network synchronization is labeled unavailable until it truly exists.
 */

import { buildPackage, planExport, type ExportPlan } from '../lib/export.js';
import { sha256Hex } from '../lib/sha256.js';
import { storageEstimate } from '../lib/storage.js';
import type { AppContext } from './shell.js';
import { button, clear, downloadBlob, el, on } from './dom.js';
import { formatBytes, formatDateTime, shortId } from './format.js';

export async function renderExportView(outlet: HTMLElement, ctx: AppContext): Promise<void> {
  const root = el('section', { class: 'page page-export', 'aria-label': 'Export' });
  root.append(el('h1', {}, 'Export'));

  // -- storage status ----------------------------------------------------------
  const sessions = await ctx.repo.listSessions();
  const observations = await ctx.repo.listAllObservations();
  let photoCount = 0;
  let photoBytes = 0;
  for (const obs of observations) {
    const images = await ctx.repo.listImages(obs.observation_id);
    photoCount += images.length;
    for (const image of images) photoBytes += image.byte_length;
  }
  const estimate = await storageEstimate();
  const storage = el('section', { class: 'panel export-storage', 'aria-label': 'Storage status' });
  storage.append(el('h2', {}, 'Local storage'));
  storage.append(
    el('dl', { class: 'facts' },
      el('div', {}, el('dt', {}, 'Backend'), el('dd', {}, ctx.repo.backendName === 'indexeddb' ? 'IndexedDB (persistent)' : 'Memory (NOT persistent)')),
      el('div', {}, el('dt', {}, 'Sessions'), el('dd', {}, String(sessions.length))),
      el('div', {}, el('dt', {}, 'Components'), el('dd', {}, String(observations.length))),
      el('div', {}, el('dt', {}, 'Photos'), el('dd', {}, `${photoCount} · ${formatBytes(photoBytes)} originals`)),
      el('div', {}, el('dt', {}, 'Device usage'),
        el('dd', {}, estimate ? `${formatBytes(estimate.usage)} of ${formatBytes(estimate.quota)}` : 'Not measurable in this browser')),
    ),
  );
  const corrupt = ctx.repo.corruptRecords();
  if (corrupt.length > 0) {
    storage.append(
      el('p', { class: 'field-hint' },
        `${corrupt.length} unreadable record(s) are quarantined and excluded from export. Review them under Sessions.`),
    );
  }
  root.append(storage);

  if (observations.length === 0) {
    const empty = el('div', { class: 'empty empty-compact' });
    const capture = button('Capture a component', { kind: 'primary' });
    on(capture, 'click', () => ctx.navigate({ name: 'capture' }));
    empty.append(
      el('h2', {}, 'Nothing to export yet'),
      el('p', { class: 'muted' }, 'Capture a component first; the evidence package builds here.'),
      capture,
    );
    root.append(empty);
    root.append(syncPanel());
    outlet.append(root);
    return;
  }

  // -- selection ------------------------------------------------------------------
  const pending = observations.filter((o) => o.export_history.length === 0);
  const exported = observations.filter((o) => o.export_history.length > 0);
  const checked = new Set(pending.map((o) => o.observation_id));

  const selection = el('section', { class: 'panel export-select', 'aria-label': 'Select evidence' });
  selection.append(el('h2', {}, `Pending (${pending.length})`));
  if (pending.length === 0) {
    selection.append(el('p', { class: 'muted' }, 'Everything is exported. Select below to re-export.'));
  }
  const sessionName = new Map(sessions.map((s) => [s.session_id, s.name]));
  for (const obs of pending) {
    selection.append(
      selectionRow(obs.observation_id, sessionName.get(obs.session_id) ?? '—', obs.captured_at, true),
    );
  }
  if (exported.length > 0) {
    selection.append(el('h3', {}, `Already exported (${exported.length})`));
    for (const obs of exported) {
      const last = obs.export_history[obs.export_history.length - 1]!;
      selection.append(
        selectionRow(
          obs.observation_id,
          `${sessionName.get(obs.session_id) ?? '—'} · last ${shortId(last.package_id)}`,
          obs.captured_at,
          false,
        ),
      );
    }
  }
  root.append(selection);

  function selectionRow(id: string, context: string, capturedAt: string, initial: boolean): HTMLElement {
    const label = el('label', { class: 'check-row' });
    const box = el('input', { type: 'checkbox' }) as HTMLInputElement;
    box.checked = initial;
    on(box, 'change', () => {
      if (box.checked) checked.add(id);
      else checked.delete(id);
      void recheck();
    });
    label.append(box, el('span', {}, `${shortId(id)} · ${context} · ${formatDateTime(capturedAt)}`));
    return label;
  }

  // -- validation + export ------------------------------------------------------------
  const validation = el('section', { class: 'panel export-validation', 'aria-label': 'Evidence validation' });
  validation.append(el('h2', {}, 'Validation'));
  const validationBody = el('div', {});
  validation.append(validationBody);
  const actions = el('div', { class: 'button-row' });
  const checkBtn = button('Check selection', {});
  const exportBtn = button('Export package', { kind: 'primary', disabled: 'true' });
  actions.append(checkBtn, exportBtn);
  validation.append(actions);
  root.append(validation);

  let plan: ExportPlan | null = null;

  async function recheck(): Promise<void> {
    clear(validationBody);
    exportBtn.setAttribute('disabled', 'true');
    plan = null;
    if (checked.size === 0) {
      validationBody.append(el('p', { class: 'muted' }, 'Select at least one component.'));
      return;
    }
    validationBody.append(el('p', { class: 'muted' }, 'Checking evidence (re-hashing originals)…'));
    const next = await planExport(ctx.repo, [...checked]);
    plan = next;
    clear(validationBody);
    if (next.issues.length > 0) {
      validationBody.append(el('p', { class: 'issue-count' }, `${next.issues.length} blocking issue(s). Export is disabled.`));
      const list = el('ul', { class: 'issue-list' });
      for (const issue of next.issues.slice(0, 20)) {
        list.append(el('li', {}, issue.message));
      }
      if (next.issues.length > 20) {
        list.append(el('li', { class: 'muted' }, `…and ${next.issues.length - 20} more.`));
      }
      validationBody.append(list);
      return;
    }
    let photos = 0;
    for (const images of next.imagesByObservation.values()) photos += images.length;
    validationBody.append(
      el('p', {}, `Ready: ${next.observations.length} component(s), ${photos} photo(s) across ${next.sessions.length} session(s). All hashes verified.`),
    );
    exportBtn.removeAttribute('disabled');
  }

  on(checkBtn, 'click', () => void recheck());

  const result = el('section', { class: 'panel export-result', 'aria-label': 'Export result', hidden: 'true' });
  root.append(result);

  on(exportBtn, 'click', async () => {
    if (!plan || plan.issues.length > 0) return;
    exportBtn.setAttribute('disabled', 'true');
    try {
      const built = await buildPackage(ctx.repo, plan);
      const digest = sha256Hex(built.bytes);
      const filename = `anastylos-capture-${built.packageId.slice(0, 8)}.zip`;
      downloadBlob(new Blob([built.bytes as BlobPart], { type: 'application/zip' }), filename);
      await ctx.repo.markExported(built.observationIds, built.packageId, built.manifest.exported_at);
      clear(result);
      result.hidden = false;
      result.append(
        el('h2', {}, 'Export complete'),
        el('p', {}, `Saved ${filename} (${formatBytes(built.totalBytes)}, ${built.fileCount} files).`),
        el('dl', { class: 'facts' },
          el('div', {}, el('dt', {}, 'Package'), el('dd', {}, built.packageId)),
          el('div', {}, el('dt', {}, 'SHA-256'), el('dd', { class: 'mono' }, digest)),
        ),
        el('p', { class: 'field-hint' }, 'This is a local file on this device. It was NOT synchronized anywhere — hand it to ANASTYLOS Core for validation.'),
      );
      ctx.notice(`Exported ${built.observationIds.length} component(s) to ${filename}.`);
      for (const id of built.observationIds) checked.delete(id);
      await recheck();
    } catch (error) {
      ctx.notice(error instanceof Error ? error.message : String(error), 'error');
      exportBtn.removeAttribute('disabled');
    }
  });

  root.append(
    el('p', { class: 'related' },
      'Need a parts list? ',
      el('a', { href: '#/bom' }, 'Open the BOM draft'),
      '.'),
  );
  root.append(syncPanel());
  outlet.append(root);
  await recheck();
}

function syncPanel(): HTMLElement {
  const panel = el('section', { class: 'panel export-sync', 'aria-label': 'Network synchronization' });
  panel.append(
    el('h2', {}, 'Sync'),
    el('p', {}, 'Network synchronization is available. ', el('a', { href: '#/sync' }, 'Open the Sync screen'), ' to pair a workstation and push this same package format over TLS.'),
    el('p', { class: 'muted' },
      'Sync is operator-triggered and optional: this ZIP download stays the offline fallback, and the protocol is documented in SYNC_PROTOCOL.md.'),
  );
  return panel;
}
