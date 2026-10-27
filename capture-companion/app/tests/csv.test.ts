import { describe, expect, it } from 'vitest';
import { bomRowsForSession, bomRowsToCsv, BOM_COLUMNS } from '../src/lib/csv.js';
import { MemoryBackend } from '../src/lib/storage.js';
import { Repository } from '../src/lib/repository.js';
import { createImage } from '../src/lib/schema.js';
import { sha256Hex } from '../src/lib/sha256.js';

async function seed(): Promise<{ repo: Repository; sessionId: string }> {
  const repo = new Repository(new MemoryBackend());
  const session = await repo.createSession('Teardown, day "1"');
  const confirmed = await repo.createObservation(session.session_id, 'camera');
  await repo.saveObservation({
    ...confirmed,
    markings: [{ text: '10k, 1%', state: 'observed' }],
    category: { value: 'resistor', state: 'confirmed' },
    quantity: { value: 12, state: 'confirmed' },
    component_reference: 'R"7"',
    review_state: 'confirmed',
  });
  const payload = new Uint8Array([1, 2, 3]);
  await repo.addImageWithBytes({
    image: createImage(confirmed.observation_id, 'front', 'a.jpg', 'image/jpeg', 3, sha256Hex(payload)),
    original: new Blob([payload], { type: 'image/jpeg' }),
  });
  // Second and third photos do NOT change the quantity.
  const payload2 = new Uint8Array([4, 5, 6]);
  await repo.addImageWithBytes({
    image: createImage(confirmed.observation_id, 'back', 'b.jpg', 'image/jpeg', 3, sha256Hex(payload2)),
    original: new Blob([payload2], { type: 'image/jpeg' }),
  });
  const draft = await repo.createObservation(session.session_id, 'file');
  await repo.saveObservation({ ...draft, review_state: 'reviewed' });
  return { repo, sessionId: session.session_id };
}

describe('bomRowsForSession', () => {
  it('reports photo counts separately from quantities', async () => {
    const { repo, sessionId } = await seed();
    const { rows } = await bomRowsForSession(repo, sessionId, {});
    expect(rows).toHaveLength(2);
    expect(rows[0]!.quantity).toBe('12');
    expect(rows[0]!.photoCount).toBe(2);
    expect(rows[1]!.quantity).toBe('');
    expect(rows[1]!.quantityState).toBe('unknown');
  });

  it('confirmedOnly excludes drafts and reports the skip count', async () => {
    const { repo, sessionId } = await seed();
    const { rows, skippedUnconfirmed } = await bomRowsForSession(repo, sessionId, { confirmedOnly: true });
    expect(rows).toHaveLength(1);
    expect(rows[0]!.reviewState).toBe('confirmed');
    expect(skippedUnconfirmed).toBe(1);
  });
});

describe('bomRowsToCsv', () => {
  it('escapes commas, quotes and newlines per RFC 4180', () => {
    const csv = bomRowsToCsv([
      {
        sessionName: 'Teardown, day "1"',
        sessionId: 's',
        observationId: 'o',
        capturedAt: '2026-01-01T00:00:00.000Z',
        componentReference: 'R"7"',
        category: 'resistor',
        categoryState: 'confirmed',
        quantity: '12',
        quantityState: 'confirmed',
        markings: 'line1\nline2',
        trayReference: '',
        reviewState: 'confirmed',
        photoCount: 2,
        photoSha256: 'ab;cd',
      },
    ]);
    const lines = csv.split('\r\n');
    expect(lines[0]).toBe(BOM_COLUMNS.join(','));
    expect(lines[1]).toContain('"Teardown, day ""1"""');
    expect(lines[1]).toContain('"R""7"""');
    expect(csv).toContain('"line1\nline2"');
    expect(csv.endsWith('\r\n')).toBe(true);
  });
});
