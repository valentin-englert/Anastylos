/** Application entry: open storage, build the shell, register the worker. */

import { openRepository } from './lib/repository.js';
import { createShell } from './ui/shell.js';
import { renderCaptureView } from './ui/view-capture.js';
import { renderSessionsView, renderSessionDetailView } from './ui/view-sessions.js';
import { renderInspectorView } from './ui/view-inspector.js';
import { renderBomView } from './ui/view-bom.js';
import { renderExportView } from './ui/view-export.js';
import { el } from './ui/dom.js';
import './styles.css';

function fatal(root: HTMLElement, message: string): void {
  root.replaceChildren(
    el('section', { class: 'panel fatal', role: 'alert' },
      el('h1', {}, 'Capture cannot start'),
      el('p', {}, message),
      el('p', { class: 'muted' }, 'Reload the page. If this persists, try a current Chrome, Edge, Firefox or Safari.')),
  );
}

async function main(): Promise<void> {
  const root = document.getElementById('app');
  if (!root) return;
  let repo;
  try {
    repo = openRepository();
    // Prove the database opens before rendering anything.
    await repo.listSessions();
  } catch (error) {
    fatal(root, error instanceof Error ? error.message : String(error));
    return;
  }

  createShell(root, repo, {
    capture: renderCaptureView,
    sessions: async (outlet, ctx) => renderSessionsView(outlet, ctx),
    session: async (outlet, ctx) => renderSessionDetailView(outlet, ctx),
    inspect: renderInspectorView,
    bom: renderBomView,
    export: renderExportView,
  });

  if ('serviceWorker' in navigator && window.location.protocol.startsWith('http')) {
    try {
      await navigator.serviceWorker.register('./sw.js');
    } catch (error) {
      console.warn('Service worker registration failed; offline shell unavailable.', error);
    }
  }
}

void main();
