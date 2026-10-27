/** BOM draft CSV: operator-confirmed rows only.
 *
 * A row is exported only when its observation reached review_state
 * `confirmed`. Photo counts are reported as photo counts, never as
 * quantities; quantities come solely from the operator-confirmed field.
 */

import type { ComponentObservation, EvidenceImage } from './schema.js';
import type { Repository } from './repository.js';

export interface BomRow {
  sessionName: string;
  sessionId: string;
  observationId: string;
  capturedAt: string;
  componentReference: string;
  category: string;
  categoryState: string;
  quantity: string;
  quantityState: string;
  markings: string;
  trayReference: string;
  reviewState: string;
  photoCount: number;
  photoSha256: string;
}

export const BOM_COLUMNS = [
  'session_name',
  'session_id',
  'observation_id',
  'captured_at',
  'component_reference',
  'category',
  'category_state',
  'quantity',
  'quantity_state',
  'markings',
  'tray_reference',
  'review_state',
  'photo_count',
  'photo_sha256',
] as const;

function escapeCell(value: string | number): string {
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function bomRowsToCsv(rows: BomRow[]): string {
  const lines = [BOM_COLUMNS.join(',')];
  for (const row of rows) {
    lines.push(
      [
        row.sessionName,
        row.sessionId,
        row.observationId,
        row.capturedAt,
        row.componentReference,
        row.category,
        row.categoryState,
        row.quantity,
        row.quantityState,
        row.markings,
        row.trayReference,
        row.reviewState,
        row.photoCount,
        row.photoSha256,
      ]
        .map(escapeCell)
        .join(','),
    );
  }
  return lines.join('\r\n') + '\r\n';
}

export async function bomRowsForSession(
  repo: Repository,
  sessionId: string,
  opts: { confirmedOnly?: boolean } = {},
): Promise<{ sessionName: string; rows: BomRow[]; skippedUnconfirmed: number }> {
  const session = await repo.getSession(sessionId);
  const sessionName = session?.name ?? '(missing session)';
  const observations = session ? await repo.listObservations(sessionId) : [];
  const rows: BomRow[] = [];
  let skippedUnconfirmed = 0;
  for (const obs of observations) {
    if (opts.confirmedOnly && obs.review_state !== 'confirmed') {
      skippedUnconfirmed++;
      continue;
    }
    rows.push(await toBomRow(repo, sessionName, obs));
  }
  return { sessionName, rows, skippedUnconfirmed };
}

async function toBomRow(
  repo: Repository,
  sessionName: string,
  obs: ComponentObservation,
): Promise<BomRow> {
  const images: EvidenceImage[] = await repo.listImages(obs.observation_id);
  return {
    sessionName,
    sessionId: obs.session_id,
    observationId: obs.observation_id,
    capturedAt: obs.captured_at,
    componentReference: obs.component_reference ?? '',
    category: obs.category.value ?? '',
    categoryState: obs.category.state,
    quantity: obs.quantity.value === null ? '' : String(obs.quantity.value),
    quantityState: obs.quantity.state,
    markings: obs.markings.map((m) => m.text).join(' | '),
    trayReference: obs.tray_reference ?? '',
    reviewState: obs.review_state,
    photoCount: images.length,
    photoSha256: images.map((g) => g.sha256).join(';'),
  };
}
