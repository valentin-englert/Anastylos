import { describe, expect, it } from 'vitest';
import { intakePhoto, makePreview, uniqueFilename } from '../src/lib/photos.js';
import { MemoryBackend } from '../src/lib/storage.js';
import { Repository } from '../src/lib/repository.js';
import { MAX_PHOTO_BYTES } from '../src/lib/schema.js';

describe('uniqueFilename', () => {
  it('suffixes collisions before the extension', () => {
    expect(uniqueFilename([], 'a.jpg')).toBe('a.jpg');
    expect(uniqueFilename(['a.jpg'], 'a.jpg')).toBe('a-2.jpg');
    expect(uniqueFilename(['a.jpg', 'a-2.jpg'], 'a.jpg')).toBe('a-3.jpg');
    expect(uniqueFilename(['a'], 'a')).toBe('a-2');
  });
});

describe('makePreview', () => {
  it('returns null without a DOM', async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: 'image/jpeg' });
    expect(await makePreview(blob)).toBeNull();
  });
});

describe('intakePhoto', () => {
  async function setup(): Promise<{ repo: Repository; observationId: string }> {
    const repo = new Repository(new MemoryBackend());
    const session = await repo.createSession('S');
    const obs = await repo.createObservation(session.session_id, 'camera');
    return { repo, observationId: obs.observation_id };
  }

  it('stores valid photos and reports duplicates without merging', async () => {
    const { repo, observationId } = await setup();
    const photo = new Blob([new Uint8Array([0xff, 0xd8, 0xff, 1])], { type: 'image/jpeg' });
    const first = await intakePhoto(repo, observationId, photo, 'front', 'a.jpg', { withPreview: false });
    expect(first.duplicates).toEqual([]);
    expect(first.previewError).toBeNull();
    const session2 = await repo.createSession('S2');
    const obs2 = await repo.createObservation(session2.session_id, 'gallery');
    const second = await intakePhoto(repo, obs2.observation_id, photo, 'front', 'a.jpg', { withPreview: false });
    expect(second.duplicates).toHaveLength(1);
    expect((await repo.listImages(observationId))).toHaveLength(1);
    expect((await repo.listImages(obs2.observation_id))).toHaveLength(1);
  });

  it('refuses non-images, empties and oversized files', async () => {
    const { repo, observationId } = await setup();
    await expect(
      intakePhoto(repo, observationId, new Blob(['x'], { type: 'text/plain' }), 'front', 'x.txt', { withPreview: false }),
    ).rejects.toThrow(/Unsupported file type/);
    await expect(
      intakePhoto(repo, observationId, new Blob([], { type: 'image/jpeg' }), 'front', 'e.jpg', { withPreview: false }),
    ).rejects.toThrow(/Empty file/);
    const huge = new Blob([new Uint8Array(10)], { type: 'image/jpeg' });
    Object.defineProperty(huge, 'size', { value: MAX_PHOTO_BYTES + 1 });
    await expect(
      intakePhoto(repo, observationId, huge, 'front', 'h.jpg', { withPreview: false }),
    ).rejects.toThrow(/per-photo cap/);
  });
});
