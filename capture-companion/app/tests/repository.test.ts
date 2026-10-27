import { beforeEach, describe, expect, it } from 'vitest';
import 'fake-indexeddb/auto';
import { Repository } from '../src/lib/repository.js';
import {
  IndexedDbBackend,
  MemoryBackend,
  type StorageBackend,
  type StoreName,
} from '../src/lib/storage.js';
import { createImage } from '../src/lib/schema.js';
import { sha256Hex } from '../src/lib/sha256.js';

const STORES: StoreName[] = ['sessions', 'observations', 'images', 'binaries', 'meta'];

const jpeg = (seed: number): Blob =>
  new Blob([new Uint8Array([0xff, 0xd8, 0xff, seed, 1, 2, 3, 4])], { type: 'image/jpeg' });

function testRepository(label: string, makeBackend: () => StorageBackend): void {
  describe(`Repository over ${label}`, () => {
    let backend: StorageBackend;
    let repo: Repository;

    beforeEach(async () => {
      backend = makeBackend();
      for (const store of STORES) await backend.clear(store);
      repo = new Repository(backend);
    });

    it('creates, orders and renames sessions', async () => {
      const first = await repo.createSession('B session');
      const second = await repo.createSession('A session');
      const listed = await repo.listSessions();
      // Chronological by creation, not alphabetical.
      expect(listed.map((s) => s.session_id)).toEqual([first.session_id, second.session_id]);
      await repo.saveSession({ ...second, name: 'Renamed' });
      expect((await repo.getSession(second.session_id))!.name).toBe('Renamed');
    });

    it('refuses to save invalid sessions', async () => {
      const session = await repo.createSession('S');
      await expect(repo.saveSession({ ...session, name: '  ' })).rejects.toThrow(/invalid session/);
    });

    it('creates observations only inside known sessions', async () => {
      await expect(repo.createObservation('00000000-0000-0000-0000-000000000000', 'camera')).rejects.toThrow(
        /Unknown session/,
      );
      const session = await repo.createSession('S');
      const obs = await repo.createObservation(session.session_id, 'camera');
      expect((await repo.listObservations(session.session_id)).map((o) => o.observation_id)).toEqual([
        obs.observation_id,
      ]);
    });

    it('applies the session tray default to new observations', async () => {
      const session = await repo.createSession('S');
      await repo.saveSession({ ...session, tray_default: 'T-3' });
      const obs = await repo.createObservation(session.session_id, 'file');
      expect(obs.tray_reference).toBe('T-3');
    });

    it('stores images with originals and previews', async () => {
      const session = await repo.createSession('S');
      const obs = await repo.createObservation(session.session_id, 'camera');
      const original = jpeg(7);
      const bytes = new Uint8Array(await original.arrayBuffer());
      const image = createImage(obs.observation_id, 'front', 'a.jpg', 'image/jpeg', bytes.length, sha256Hex(bytes));
      const preview = new Blob([new Uint8Array([9, 9])], { type: 'image/jpeg' });
      await repo.addImageWithBytes({ image, original, preview });
      expect((await repo.getOriginal(image.image_id))?.size).toBe(original.size);
      expect((await repo.getPreview(image.image_id))?.size).toBe(2);
      expect(await repo.listImages(obs.observation_id)).toHaveLength(1);
    });

    it('rolls back binaries when the metadata write fails', async () => {
      const failing = new (class extends MemoryBackend {
        override async put(store: StoreName, key: string, value: unknown): Promise<void> {
          if (store === 'images') throw new Error('simulated metadata failure');
          return super.put(store, key, value);
        }
      })();
      const fragile = new Repository(failing);
      const session = await fragile.createSession('S');
      const obs = await fragile.createObservation(session.session_id, 'camera');
      const original = jpeg(1);
      const bytes = new Uint8Array(await original.arrayBuffer());
      const image = createImage(obs.observation_id, 'front', 'a.jpg', 'image/jpeg', bytes.length, sha256Hex(bytes));
      await expect(fragile.addImageWithBytes({ image, original })).rejects.toThrow(/simulated/);
      expect(await failing.all('binaries')).toEqual([]);
      expect(await failing.all('images')).toEqual([]);
    });

    it('finds duplicates by hash without merging them', async () => {
      const session = await repo.createSession('S');
      const first = await repo.createObservation(session.session_id, 'camera');
      const second = await repo.createObservation(session.session_id, 'gallery');
      for (const obs of [first, second]) {
        const original = jpeg(42);
        const bytes = new Uint8Array(await original.arrayBuffer());
        await repo.addImageWithBytes({
          image: createImage(obs.observation_id, 'front', `${obs.observation_id}.jpg`, 'image/jpeg', bytes.length, sha256Hex(bytes)),
          original,
        });
      }
      const digest = sha256Hex(new Uint8Array(await jpeg(42).arrayBuffer()));
      const matches = await repo.findBySha256(digest);
      expect(matches).toHaveLength(2);
      // Both originals survive; nothing merged.
      expect(await repo.getOriginal(matches[0]!.image_id)).toBeTruthy();
      expect(await repo.getOriginal(matches[1]!.image_id)).toBeTruthy();
    });

    it('quarantines corrupt records and purges them only explicitly', async () => {
      await backend.put('sessions', 'broken', { schema_version: 99, name: '' });
      expect(await repo.listSessions()).toEqual([]);
      expect(repo.corruptRecords()).toHaveLength(1);
      expect(repo.corruptRecords()[0]!.store).toBe('sessions');
      // Still on disk until explicitly purged.
      expect(await backend.get('sessions', 'broken')).toBeTruthy();
      await repo.purgeCorrupt('sessions', 'broken');
      expect(await backend.get('sessions', 'broken')).toBeUndefined();
      expect(repo.corruptRecords()).toHaveLength(0);
    });

    it('removes an observation with every image and binary', async () => {
      const session = await repo.createSession('S');
      const obs = await repo.createObservation(session.session_id, 'camera');
      for (const seed of [1, 2]) {
        const original = jpeg(seed);
        const bytes = new Uint8Array(await original.arrayBuffer());
        await repo.addImageWithBytes({
          image: createImage(obs.observation_id, 'front', `p${seed}.jpg`, 'image/jpeg', bytes.length, sha256Hex(bytes)),
          original,
          preview: new Blob([new Uint8Array([seed])], { type: 'image/jpeg' }),
        });
      }
      const report = await repo.removeObservation(obs.observation_id);
      expect(report).toEqual({ observationId: obs.observation_id, imagesRemoved: 2, binariesRemoved: 4 });
      expect(await backend.all('observations')).toEqual([]);
      expect(await backend.all('images')).toEqual([]);
      expect(await backend.all('binaries')).toEqual([]);
    });

    it('refuses to delete non-empty sessions without an explicit flag', async () => {
      const session = await repo.createSession('S');
      await repo.createObservation(session.session_id, 'camera');
      await expect(repo.deleteSession(session.session_id)).rejects.toThrow(/still holds observations/);
      expect(await repo.getSession(session.session_id)).toBeTruthy();
      await repo.deleteSession(session.session_id, { deleteObservations: true });
      expect(await repo.getSession(session.session_id)).toBeUndefined();
    });

    it('marks exports idempotently', async () => {
      const session = await repo.createSession('S');
      const obs = await repo.createObservation(session.session_id, 'camera');
      await repo.markExported([obs.observation_id], 'pkg-1', '2026-01-01T00:00:00Z');
      await repo.markExported([obs.observation_id], 'pkg-1', '2026-01-01T00:00:00Z');
      expect((await repo.getObservation(obs.observation_id))!.export_history).toHaveLength(1);
    });

    it('updates crops but never rewrites evidence identity', async () => {
      const session = await repo.createSession('S');
      const obs = await repo.createObservation(session.session_id, 'camera');
      const original = jpeg(3);
      const bytes = new Uint8Array(await original.arrayBuffer());
      const image = createImage(obs.observation_id, 'detail', 'd.jpg', 'image/jpeg', bytes.length, sha256Hex(bytes));
      await repo.addImageWithBytes({ image, original });
      await repo.updateImage({ ...image, crop: { x: 0.1, y: 0.1, width: 0.5, height: 0.5 } });
      expect((await repo.getImage(image.image_id))!.crop).not.toBeNull();
      await expect(repo.updateImage({ ...image, sha256: 'b'.repeat(64) })).rejects.toThrow(/immutable/);
    });

    it('round-trips tiny UI state through meta', async () => {
      expect(await repo.getMeta('sel.session')).toBeUndefined();
      await repo.setMeta('sel.session', 'abc');
      expect(await repo.getMeta('sel.session')).toBe('abc');
    });
  });
}

testRepository('MemoryBackend', () => new MemoryBackend());
testRepository('IndexedDbBackend', () => new IndexedDbBackend());
