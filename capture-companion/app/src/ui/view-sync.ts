/** Sync: pair a workstation, then push identical export packages over TLS.
 *
 * Operator-triggered only: nothing uploads by itself. Failed transfers mark
 * nothing, so evidence stays pending and retryable. The ZIP export stays
 * untouched as the offline fallback.
 */

import { buildPackage, planExport } from '../lib/export.js';
import { sha256Hex } from '../lib/sha256.js';
import {
  SyncClient,
  SyncError,
  clearPairing,
  loadPairing,
  normalizeBaseUrl,
  partitionSyncState,
  savePairing,
  type AcceptanceReport,
  type PairedWorkstation,
} from '../lib/sync.js';
import type { AppContext } from './shell.js';
import { button, clear, el, on } from './dom.js';
import { formatBytes, formatDateTime, shortId } from './format.js';

interface PendingUpload {
  packageId: string;
  bytes: Uint8Array;
  sha256: string;
  observationIds: string[];
  exportedAt: string;
}

export async function renderSyncView(outlet: HTMLElement, ctx: AppContext): Promise<void> {
  const root = el('section', { class: 'page page-sync', 'aria-label': 'Synchronization' });
  root.append(el('h1', {}, 'Sync'));

  const pairing = await loadPairing(ctx.repo);
  root.append(pairing ? pairedPanel(ctx, pairing) : pairingPanel(ctx));

  const observations = await ctx.repo.listAllObservations();
  const sessions = await ctx.repo.listSessions();
  const sessionName = new Map(sessions.map((s) => [s.session_id, s.name]));
  const state = partitionSyncState(observations);

  root.append(connectionPanel(ctx, pairing));
  root.append(pendingPanel(ctx, pairing, state, sessionName));

  root.append(
    el('p', { class: 'related' },
      'Prefer moving a file by hand? ',
      el('a', { href: '#/export' }, 'Export a ZIP package'),
      ' instead — the format is identical.'),
  );
  outlet.append(root);
}

// ---------------------------------------------------------------------------
// Pairing
// ---------------------------------------------------------------------------

function pairingPanel(ctx: AppContext): HTMLElement {
  const panel = el('section', { class: 'panel sync-pairing', 'aria-label': 'Pair a workstation' });
  panel.append(el('h2', {}, 'Pair a workstation'));

  const address = el('input', {
    type: 'text',
    class: 'text-input',
    placeholder: 'https://192.168.1.20:8443',
    autocomplete: 'off',
    spellcheck: 'false',
  }) as HTMLInputElement;
  const code = el('input', {
    type: 'text',
    class: 'text-input',
    placeholder: 'six-word pairing code',
    autocomplete: 'off',
    spellcheck: 'false',
  }) as HTMLInputElement;
  const fingerprint = el('input', {
    type: 'text',
    class: 'text-input mono',
    placeholder: 'certificate fingerprint (optional reminder)',
    autocomplete: 'off',
    spellcheck: 'false',
  }) as HTMLInputElement;
  const compare = el('input', { type: 'checkbox' }) as HTMLInputElement;
  const compareLabel = el('label', { class: 'check-row' });
  compareLabel.append(
    compare,
    el('span', {}, 'I compared the certificate fingerprint with the workstation screen.'),
  );
  const pairBtn = button('Pair workstation', { kind: 'primary' });
  const hint = el('p', { class: 'field-hint' });

  panel.append(
    el('p', { class: 'muted' },
      'On the workstation, run the receiver and mint a pairing code. The code is single-use and expires after minutes.'),
    el('label', { class: 'field' }, el('span', { class: 'field-label' }, 'Workstation address'), address),
    el('label', { class: 'field' }, el('span', { class: 'field-label' }, 'Pairing code'), code),
    el('label', { class: 'field' },
      el('span', { class: 'field-label' }, 'Certificate fingerprint'),
      fingerprint),
    el('p', { class: 'field-hint' },
      'This browser cannot check certificates for you — compare every character of the fingerprint ' +
      'printed by the receiver before pairing. The value above is only your reminder.'),
    compareLabel,
    el('div', { class: 'button-row' }, pairBtn),
    hint,
  );

  function refreshPairEnabled(): void {
    let normalized;
    try {
      normalized = normalizeBaseUrl(address.value);
    } catch {
      pairBtn.setAttribute('disabled', 'true');
      return;
    }
    const needsCompare = !normalized.loopbackPlaintext;
    const ok = !needsCompare || compare.checked;
    if (ok) pairBtn.removeAttribute('disabled');
    else pairBtn.setAttribute('disabled', 'true');
  }
  on(address, 'input', refreshPairEnabled);
  on(compare, 'change', refreshPairEnabled);
  refreshPairEnabled();

  on(pairBtn, 'click', () => void (async () => {
    clear(hint);
    pairBtn.setAttribute('disabled', 'true');
    try {
      const normalized = normalizeBaseUrl(address.value);
      const client = new SyncClient(normalized.baseUrl, null);
      const info = await client.info();
      if (info.service !== 'anastylos-capture-receiver') {
        throw new SyncError('server', 'That address answered, but it is not a Capture receiver.');
      }
      const result = await client.pair(code.value);
      const stored: PairedWorkstation = {
        workstationId: result.workstationId,
        baseUrl: normalized.baseUrl,
        token: result.token,
        fingerprint: fingerprint.value.trim() ? fingerprint.value.trim() : null,
        pairedAt: new Date().toISOString(),
      };
      await savePairing(ctx.repo, stored);
      ctx.notice('Workstation paired. Evidence still moves only when you tap Sync.');
      ctx.refresh();
    } catch (error) {
      hint.textContent = error instanceof Error ? error.message : String(error);
      hint.classList.add('issue-count');
      pairBtn.removeAttribute('disabled');
    }
  })());

  return panel;
}

function pairedPanel(ctx: AppContext, pairing: PairedWorkstation): HTMLElement {
  const panel = el('section', { class: 'panel sync-paired', 'aria-label': 'Paired workstation' });
  panel.append(el('h2', {}, 'Workstation'));
  panel.append(
    el('dl', { class: 'facts' },
      el('div', {}, el('dt', {}, 'Workstation'), el('dd', { class: 'mono' }, pairing.workstationId)),
      el('div', {}, el('dt', {}, 'Address'), el('dd', { class: 'mono' }, pairing.baseUrl)),
      el('div', {}, el('dt', {}, 'Paired'), el('dd', {}, formatDateTime(pairing.pairedAt))),
      ...(pairing.fingerprint
        ? [el('div', {}, el('dt', {}, 'Fingerprint'), el('dd', { class: 'mono' }, pairing.fingerprint))]
        : []),
    ),
  );
  const unpairBtn = button('Unpair', { kind: 'danger' });
  on(unpairBtn, 'click', () => void (async () => {
    await clearPairing(ctx.repo);
    ctx.notice('Workstation unpaired. Pending evidence stays on this device.');
    ctx.refresh();
  })());
  panel.append(
    el('div', { class: 'button-row' }, unpairBtn),
    el('p', { class: 'field-hint' },
      'Unpairing deletes this device\u2019s keys. Tell the workstation operator to revoke the token there too.'),
  );
  return panel;
}

// ---------------------------------------------------------------------------
// Connection
// ---------------------------------------------------------------------------

function connectionPanel(ctx: AppContext, pairing: PairedWorkstation | null): HTMLElement {
  const panel = el('section', { class: 'panel sync-connection', 'aria-label': 'Connection status' });
  panel.append(el('h2', {}, 'Connection'));
  const body = el('div', {});
  panel.append(body);
  const checkBtn = button('Check connection', {});
  if (!pairing) {
    body.append(el('p', { class: 'muted' }, 'Pair a workstation first; then check the connection here.'));
    checkBtn.setAttribute('disabled', 'true');
  } else {
    body.append(el('p', { class: 'muted' }, 'Not checked yet in this session.'));
  }
  panel.append(el('div', { class: 'button-row' }, checkBtn));

  on(checkBtn, 'click', () => void (async () => {
    if (!pairing) return;
    checkBtn.setAttribute('disabled', 'true');
    clear(body);
    body.append(el('p', { class: 'muted' }, 'Contacting workstation…'));
    try {
      const client = new SyncClient(pairing.baseUrl, pairing.token);
      const status = await client.status();
      clear(body);
      if (status.workstation_id !== pairing.workstationId) {
        body.append(
          el('p', { class: 'issue-count' },
            'Warning: the workstation answered with a different identity than the paired one. ' +
            'Unpair and re-pair after verifying the address — do not sync.'),
        );
        return;
      }
      body.append(
        el('dl', { class: 'facts' },
          el('div', {}, el('dt', {}, 'Reachable'), el('dd', {}, 'Yes (authenticated)')),
          el('div', {}, el('dt', {}, 'Staged packages'), el('dd', {}, String(status.staged_packages))),
          el('div', {}, el('dt', {}, 'Server time'), el('dd', {}, formatDateTime(status.server_time))),
        ),
      );
    } catch (error) {
      clear(body);
      body.append(el('p', { class: 'issue-count' }, error instanceof Error ? error.message : String(error)));
      if (error instanceof SyncError && error.kind === 'unauthorized') {
        const repair = button('Unpair and re-pair', { kind: 'danger' });
        on(repair, 'click', () => void (async () => {
          await clearPairing(ctx.repo);
          ctx.refresh();
        })());
        body.append(el('div', { class: 'button-row' }, repair));
      }
    } finally {
      checkBtn.removeAttribute('disabled');
    }
  })());
  return panel;
}

// ---------------------------------------------------------------------------
// Pending evidence + manual sync
// ---------------------------------------------------------------------------

function pendingPanel(
  ctx: AppContext,
  pairing: PairedWorkstation | null,
  state: ReturnType<typeof partitionSyncState>,
  sessionName: Map<string, string>,
): HTMLElement {
  const panel = el('section', { class: 'panel sync-pending', 'aria-label': 'Pending evidence' });
  panel.append(el('h2', {}, `Pending (${state.pending.length})`));

  if (state.pending.length === 0) {
    panel.append(el('p', { class: 'muted' }, 'Nothing is waiting for sync.'));
  }
  const checked = new Set(state.pending.map((o) => o.observation_id));
  for (const obs of state.pending) {
    const label = el('label', { class: 'check-row' });
    const box = el('input', { type: 'checkbox' }) as HTMLInputElement;
    box.checked = true;
    on(box, 'change', () => {
      if (box.checked) checked.add(obs.observation_id);
      else checked.delete(obs.observation_id);
    });
    label.append(
      box,
      el('span', {}, `${shortId(obs.observation_id)} · ${sessionName.get(obs.session_id) ?? '—'} · ${formatDateTime(obs.captured_at)}`),
    );
    panel.append(label);
  }

  if (state.synced.length > 0 || state.fileExported.length > 0) {
    const lines: string[] = [];
    if (state.synced.length > 0) lines.push(`${state.synced.length} synced to the workstation`);
    if (state.fileExported.length > 0) lines.push(`${state.fileExported.length} exported as local files`);
    panel.append(el('p', { class: 'field-hint' }, `Already moved: ${lines.join(' · ')}.`));
  }

  const result = el('section', { class: 'panel sync-result', 'aria-label': 'Transfer result', hidden: 'true' });
  const syncBtn = button('Sync selected', { kind: 'primary' });
  const retryBtn = button('Retry same package', {});
  retryBtn.setAttribute('disabled', 'true');
  if (!pairing) {
    syncBtn.setAttribute('disabled', 'true');
    panel.append(el('p', { class: 'field-hint' }, 'Pair a workstation above to enable sync.'));
  }
  panel.append(el('div', { class: 'button-row' }, syncBtn, retryBtn));
  panel.append(result);

  let lastUpload: PendingUpload | null = null;

  function showResult(entries: Array<{ title: string; body: HTMLElement }>): void {
    clear(result);
    result.hidden = false;
    for (const entry of entries) {
      result.append(el('h3', {}, entry.title), entry.body);
    }
  }

  function resultBodyFor(report: AcceptanceReport, duplicate: boolean, upload: PendingUpload): HTMLElement {
    const wrap = el('div', {});
    wrap.append(
      el('dl', { class: 'facts' },
        el('div', {}, el('dt', {}, 'Package'), el('dd', { class: 'mono' }, upload.packageId)),
        el('div', {}, el('dt', {}, 'SHA-256'), el('dd', { class: 'mono' }, upload.sha256)),
        el('div', {}, el('dt', {}, 'Size'), el('dd', {}, formatBytes(upload.bytes.length))),
      ),
    );
    if (duplicate) {
      wrap.append(
        el('p', {}, 'The workstation already held this package — it staged nothing twice (idempotent redelivery).'),
      );
    }
    const accepted = report.observations.filter((o) => o.accepted);
    wrap.append(el('p', {}, `Accepted for review: ${accepted.length} of ${report.observations.length}.`));
    for (const verdict of report.observations) {
      const line = el('p', { class: verdict.accepted ? '' : 'issue-count' },
        `${verdict.accepted ? 'Accepted' : 'Held'} ${shortId(verdict.observation_id)}` +
        (verdict.accepted ? '' : `: ${verdict.issues.join('; ') || 'see workstation report'}`));
      wrap.append(line);
    }
    if (report.status === 'invalid') {
      wrap.append(el('p', { class: 'issue-count' }, `Workstation issues: ${report.issues.join('; ') || '—'}`));
    }
    return wrap;
  }

  async function runUpload(upload: PendingUpload, isRetry: boolean): Promise<void> {
    if (!pairing) return;
    syncBtn.setAttribute('disabled', 'true');
    retryBtn.setAttribute('disabled', 'true');
    clear(result);
    result.hidden = false;
    result.append(el('p', { class: 'muted' }, isRetry ? 'Retrying the same package…' : 'Building and uploading…'));
    try {
      const client = new SyncClient(pairing.baseUrl, pairing.token);
      const outcome = await client.uploadPackage(upload);
      await ctx.repo.markSynced(upload.observationIds, upload.packageId, upload.exportedAt);
      lastUpload = null;
      retryBtn.setAttribute('disabled', 'true');
      showResult([{ title: 'Sync complete', body: resultBodyFor(outcome.report, outcome.duplicate, upload) }]);
      ctx.notice(
        outcome.duplicate
          ? 'Workstation already held that package; marked synced without staging twice.'
          : `Synced ${upload.observationIds.length} component(s); the workstation staged them for review.`,
      );
      for (const id of upload.observationIds) checked.delete(id);
    } catch (error) {
      lastUpload = upload;
      retryBtn.removeAttribute('disabled');
      const wrap = el('div', {});
      wrap.append(el('p', { class: 'issue-count' }, error instanceof Error ? error.message : String(error)));
      if (error instanceof SyncError && error.report) {
        wrap.append(resultBodyFor(error.report, false, upload));
      } else {
        wrap.append(
          el('p', { class: 'field-hint' }, 'Nothing was marked synced — the evidence stays on this device. Retry when ready.'),
        );
      }
      showResult([{ title: 'Sync failed', body: wrap }]);
    } finally {
      syncBtn.removeAttribute('disabled');
    }
  }

  on(syncBtn, 'click', () => void (async () => {
    if (checked.size === 0) {
      showResult([{
        title: 'Nothing selected',
        body: el('p', { class: 'muted' }, 'Select at least one pending component.'),
      }]);
      return;
    }
    syncBtn.setAttribute('disabled', 'true');
    try {
      const plan = await planExport(ctx.repo, [...checked]);
      if (plan.issues.length > 0) {
        const wrap = el('div', {});
        wrap.append(el('p', { class: 'issue-count' }, `${plan.issues.length} blocking issue(s). Sync is disabled.`));
        const list = el('ul', { class: 'issue-list' });
        for (const issue of plan.issues.slice(0, 20)) list.append(el('li', {}, issue.message));
        wrap.append(list);
        showResult([{ title: 'Evidence blocked', body: wrap }]);
        return;
      }
      const built = await buildPackage(ctx.repo, plan);
      await runUpload(
        {
          packageId: built.packageId,
          bytes: built.bytes,
          sha256: sha256Hex(built.bytes),
          observationIds: built.observationIds,
          exportedAt: built.manifest.exported_at,
        },
        false,
      );
    } catch (error) {
      showResult([{
        title: 'Sync failed',
        body: el('p', { class: 'issue-count' }, error instanceof Error ? error.message : String(error)),
      }]);
    } finally {
      syncBtn.removeAttribute('disabled');
    }
  })());

  on(retryBtn, 'click', () => {
    if (lastUpload) void runUpload(lastUpload, true);
  });

  return panel;
}
