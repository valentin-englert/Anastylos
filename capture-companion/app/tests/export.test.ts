import { describe, expect, it } from 'vitest';
import { buildPackage, planExport, validateManifest } from '../src/lib/export.js';
import { MemoryBackend } from '../src/lib/storage.js';
import { Repository } from '../src/lib/repository.js';
import { createImage } from '../src/lib/schema.js';
import { sha256Hex } from '../src/lib/sha256.js';
import { parseZip } from './zip-reader.js';

async function seed(): Promise<{ repo: Repository; observationId: string; packageReady: boolean }> {
  const repo = new Repository(new MemoryBackend());
  const session = await repo.createSession('Export me');
  const obs = await repo.createObservation(session.session_id, 'camera');
  const payload = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4, 5]);
  const original = new Blob([payload], { type: 'image/jpeg' });
  await repo.addImageWithBytes({
    image: createImage(obs.observation_id, 'front', 'front.jpg', 'image/jpeg', payload.length, sha256Hex(payload)),
    original,
    preview: new Blob([new Uint8Array([7, 8])], { type: 'image/jpeg' }),
  });
  return { repo, observationId: obs.observation_id, packageReady: true };
}

describe('planExport', () => {
  it('blocks empty selections and missing observations', async () => {
    const { repo } = await seed();
    expect((await planExport(repo, [])).issues.length).toBeGreaterThan(0);
    const missing = await planExport(repo, ['00000000-0000-0000-0000-000000000000']);
    expect(missing.issues[0]!.message).toMatch(/missing or corrupt/);
  });

  it('blocks observations without photo evidence', async () => {
    const { repo } = await seed();
    const session = (await repo.listSessions())[0]!;
    const bare = await repo.createObservation(session.session_id, 'camera');
    const plan = await planExport(repo, [bare.observation_id]);
    expect(plan.issues[0]!.message).toMatch(/no photo evidence/);
  });

  it('blocks tampered evidence whose hash no longer matches', async () => {
    const { repo, observationId } = await seed();
    const image = (await repo.listImages(observationId))[0]!;
    // Simulate storage damage by overwriting the binary key directly.
    const repoAny = repo as unknown as { backend: MemoryBackend };
    await repoAny.backend.put(
      'binaries',
      `orig/${image.image_id}`,
      new Blob([new Uint8Array([9, 9, 9, 9, 9, 9, 9, 9, 9])], { type: 'image/jpeg' }),
    );
    const plan = await planExport(repo, [observationId]);
    expect(plan.issues.length).toBe(1);
    expect(plan.issues[0]!.message).toMatch(/hash check|recorded length/);
  });

  it('accepts a clean selection', async () => {
    const { repo, observationId } = await seed();
    const plan = await planExport(repo, [observationId]);
    expect(plan.issues).toEqual([]);
    expect(plan.observations).toHaveLength(1);
    expect(plan.sessions).toHaveLength(1);
  });
});

describe('buildPackage', () => {
  it('builds a verifiable ZIP with manifest, assets and previews', async () => {
    const { repo, observationId } = await seed();
    const plan = await planExport(repo, [observationId]);
    const built = await buildPackage(repo, plan);
    expect(validateManifest(built.manifest)).toEqual([]);
    expect(built.fileCount).toBe(3); // manifest + original + preview

    const entries = parseZip(built.bytes);
    expect(entries.map((e) => e.path)).toEqual([
      'manifest.json',
      `assets/${observationId}/front.jpg`,
      `previews/${observationId}/${(await repo.listImages(observationId))[0]!.image_id}.jpg`,
    ]);
    const manifest = JSON.parse(new TextDecoder().decode(entries[0]!.data));
    expect(manifest.package_id).toBe(built.packageId);
    expect(manifest.observations[0].images).toHaveLength(1);
    // Every manifest file entry matches ZIP bytes + hash.
    for (const file of manifest.files) {
      const entry = entries.find((e) => e.path === file.path)!;
      expect(sha256Hex(entry.data)).toBe(file.sha256);
      expect(entry.data.length).toBe(file.bytes);
    }
  });

  it('emits annotations only when crops exist', async () => {
    const { repo, observationId } = await seed();
    const image = (await repo.listImages(observationId))[0]!;
    await repo.updateImage({ ...image, crop: { x: 0, y: 0, width: 0.5, height: 0.5 } });
    const plan = await planExport(repo, [observationId]);
    const built = await buildPackage(repo, plan);
    expect(parseZip(built.bytes).map((e) => e.path)).toContain('annotations.json');
  });

  it('is byte-deterministic for a fixed export time', async () => {
    const { repo, observationId } = await seed();
    const plan = await planExport(repo, [observationId], '11111111-1111-4111-8111-111111111111');
    const opts = { exportedAt: '2026-01-15T10:30:00.000Z', mtime: new Date('2026-01-15T10:30:00Z') };
    const first = await buildPackage(repo, plan, opts);
    const second = await buildPackage(repo, plan, opts);
    expect(first.bytes).toEqual(second.bytes);
  });

  it('throws while issues are present', async () => {
    const { repo } = await seed();
    const plan = await planExport(repo, []);
    await expect(buildPackage(repo, plan)).rejects.toThrow(/Export blocked/);
  });
});

describe('validateManifest', () => {
  it('rejects wrong versions, duplicates and dangling references', async () => {
    const { repo, observationId } = await seed();
    const plan = await planExport(repo, [observationId]);
    const built = await buildPackage(repo, plan);
    expect(validateManifest({ ...built.manifest, schema_version: 2 }).length).toBeGreaterThan(0);
    expect(
      validateManifest({
        ...built.manifest,
        observations: [...built.manifest.observations, ...built.manifest.observations],
      }).length,
    ).toBeGreaterThan(0);
    expect(
      validateManifest({
        ...built.manifest,
        observations: [{ ...built.manifest.observations[0], session_id: '22222222-2222-4222-8222-222222222222' }],
      }).length,
    ).toBeGreaterThan(0);
    expect(validateManifest(null).length).toBeGreaterThan(0);
  });
});
