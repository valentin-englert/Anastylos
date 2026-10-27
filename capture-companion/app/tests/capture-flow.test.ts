import { beforeEach, describe, expect, it } from 'vitest';
import { Repository } from '../src/lib/repository.js';
import { MemoryBackend } from '../src/lib/storage.js';
import { ensureWorkingObservationId } from '../src/ui/journey.js';

describe('capture working observation', () => {
  let repo: Repository;
  let sessionId: string;

  beforeEach(async () => {
    repo = new Repository(new MemoryBackend());
    sessionId = (await repo.createSession('Teardown')).session_id;
  });

  it('creates exactly one observation and reuses it across accepts', async () => {
    const first = await ensureWorkingObservationId(repo, null, sessionId, 'camera');
    expect(first.created).toBe(true);

    const second = await ensureWorkingObservationId(repo, first.observationId, sessionId, 'camera');
    expect(second).toEqual({ observationId: first.observationId, created: false });

    expect((await repo.listObservations(sessionId)).map((o) => o.observation_id)).toEqual([
      first.observationId,
    ]);
  });

  it('starts a fresh observation when the working one is gone', async () => {
    const first = await ensureWorkingObservationId(repo, null, sessionId, 'camera');
    await repo.removeObservation(first.observationId);

    const second = await ensureWorkingObservationId(repo, first.observationId, sessionId, 'camera');
    expect(second.created).toBe(true);
    expect(second.observationId).not.toBe(first.observationId);
    expect(await repo.listAllObservations()).toHaveLength(1);
  });

  it('writes nothing for an unknown session', async () => {
    const unknown = ['00000000', '0000', '0000', '0000', '000000000000'].join('-');
    await expect(ensureWorkingObservationId(repo, null, unknown, 'camera')).rejects.toThrow();
    expect(await repo.listAllObservations()).toHaveLength(0);
  });
});
