/** Typed repository over a StorageBackend.
 *
 * Responsibilities: schema validation on every read and write, chronological
 * ordering, duplicate detection by image hash, explicit export marking, and
 * safe deletion. Corrupt records are quarantined (reported and skipped),
 * never silently repaired or deleted.
 */

import {
  type CaptureSession,
  type ComponentObservation,
  type EvidenceImage,
  createObservation,
  createSession,
  validateImage,
  validateObservation,
  validateSession,
} from './schema.js';
import { IndexedDbBackend, MemoryBackend, type StorageBackend, type StoreName } from './storage.js';
import { nowIso } from './primitives.js';

export interface CorruptRecord {
  store: StoreName;
  key: string;
  issues: string[];
}

export interface RemovalReport {
  observationId: string;
  imagesRemoved: number;
  binariesRemoved: number;
}

const ORIG_PREFIX = 'orig/';
const PREV_PREFIX = 'prev/';

export class Repository {
  private corrupt: CorruptRecord[] = [];

  constructor(private readonly backend: StorageBackend) {}

  get backendName(): StorageBackend['name'] {
    return this.backend.name;
  }

  /** Records that failed validation on read. Reported, skipped, never deleted. */
  corruptRecords(): CorruptRecord[] {
    return [...this.corrupt];
  }

  /** Explicit operator-confirmed purge of one quarantined record. */
  async purgeCorrupt(store: StoreName, key: string): Promise<void> {
    await this.backend.remove(store, key);
    this.corrupt = this.corrupt.filter((c) => !(c.store === store && c.key === key));
  }

  private quarantine(store: StoreName, key: string, issues: string[]): void {
    if (!this.corrupt.some((c) => c.store === store && c.key === key)) {
      this.corrupt.push({ store, key, issues });
    }
  }

  // -- sessions -----------------------------------------------------------

  async listSessions(): Promise<CaptureSession[]> {
    const entries = await this.backend.all<CaptureSession>('sessions');
    const sessions: CaptureSession[] = [];
    for (const { key, value } of entries) {
      const issues = validateSession(value);
      if (issues.length > 0) {
        this.quarantine('sessions', key, issues);
        continue;
      }
      sessions.push(value);
    }
    sessions.sort((a, b) => a.created_at.localeCompare(b.created_at));
    return sessions;
  }

  async getSession(sessionId: string): Promise<CaptureSession | undefined> {
    const value = await this.backend.get<CaptureSession>('sessions', sessionId);
    if (value === undefined) return undefined;
    const issues = validateSession(value);
    if (issues.length > 0) {
      this.quarantine('sessions', sessionId, issues);
      return undefined;
    }
    return value;
  }

  async createSession(name: string): Promise<CaptureSession> {
    const session = createSession(name);
    await this.saveSession(session);
    return session;
  }

  async saveSession(session: CaptureSession): Promise<void> {
    const issues = validateSession({ ...session, updated_at: nowIso() });
    if (issues.length > 0) throw new Error(`Refusing to save invalid session: ${issues.join('; ')}`);
    await this.backend.put('sessions', session.session_id, { ...session, updated_at: nowIso() });
  }

  /** Delete a session. Refuses non-empty sessions unless `deleteObservations`
   *  is explicitly true (the UI confirms this separately). */
  async deleteSession(sessionId: string, opts: { deleteObservations?: boolean } = {}): Promise<RemovalReport[]> {
    const observations = await this.listObservations(sessionId);
    if (observations.length > 0 && !opts.deleteObservations) {
      throw new Error('Session still holds observations; confirm their deletion first.');
    }
    const reports: RemovalReport[] = [];
    for (const obs of observations) {
      reports.push(await this.removeObservation(obs.observation_id));
    }
    await this.backend.remove('sessions', sessionId);
    return reports;
  }

  // -- observations -------------------------------------------------------

  async listObservations(sessionId: string): Promise<ComponentObservation[]> {
    const entries = await this.backend.all<ComponentObservation>('observations');
    const observations: ComponentObservation[] = [];
    for (const { key, value } of entries) {
      const issues = validateObservation(value);
      if (issues.length > 0) {
        this.quarantine('observations', key, issues);
        continue;
      }
      if (value.session_id === sessionId) observations.push(value);
    }
    observations.sort((a, b) => a.captured_at.localeCompare(b.captured_at));
    return observations;
  }

  async listAllObservations(): Promise<ComponentObservation[]> {
    const entries = await this.backend.all<ComponentObservation>('observations');
    const observations: ComponentObservation[] = [];
    for (const { key, value } of entries) {
      const issues = validateObservation(value);
      if (issues.length > 0) {
        this.quarantine('observations', key, issues);
        continue;
      }
      observations.push(value);
    }
    observations.sort((a, b) => a.captured_at.localeCompare(b.captured_at));
    return observations;
  }

  async getObservation(observationId: string): Promise<ComponentObservation | undefined> {
    const value = await this.backend.get<ComponentObservation>('observations', observationId);
    if (value === undefined) return undefined;
    const issues = validateObservation(value);
    if (issues.length > 0) {
      this.quarantine('observations', observationId, issues);
      return undefined;
    }
    return value;
  }

  async createObservation(
    sessionId: string,
    source: ComponentObservation['capture_source'],
    opts: { trayReference?: string | null; componentReference?: string | null } = {},
  ): Promise<ComponentObservation> {
    const session = await this.getSession(sessionId);
    if (!session) throw new Error(`Unknown session ${sessionId}`);
    const observation = createObservation(sessionId, source, {
      trayReference: opts.trayReference ?? session.tray_default,
      componentReference: opts.componentReference ?? null,
    });
    await this.saveObservation(observation);
    return observation;
  }

  async saveObservation(observation: ComponentObservation): Promise<void> {
    const issues = validateObservation(observation);
    if (issues.length > 0) {
      throw new Error(`Refusing to save invalid observation: ${issues.join('; ')}`);
    }
    await this.backend.put('observations', observation.observation_id, observation);
  }

  /** Explicit deletion of an observation with ALL its evidence (metadata,
   *  originals, previews). The caller must confirm with the operator first. */
  async removeObservation(observationId: string): Promise<RemovalReport> {
    const images = await this.listImages(observationId);
    let binariesRemoved = 0;
    for (const image of images) {
      await this.backend.remove('binaries', ORIG_PREFIX + image.image_id);
      binariesRemoved++;
      const preview = await this.backend.get('binaries', PREV_PREFIX + image.image_id);
      if (preview !== undefined) {
        await this.backend.remove('binaries', PREV_PREFIX + image.image_id);
        binariesRemoved++;
      }
      await this.backend.remove('images', image.image_id);
    }
    await this.backend.remove('observations', observationId);
    return { observationId, imagesRemoved: images.length, binariesRemoved };
  }

  async markExported(observationIds: string[], packageId: string, exportedAt: string): Promise<void> {
    for (const id of observationIds) {
      const obs = await this.getObservation(id);
      if (!obs) continue;
      if (!obs.export_history.some((h) => h.package_id === packageId)) {
        obs.export_history.push({ package_id: packageId, exported_at: exportedAt });
        await this.saveObservation(obs);
      }
    }
  }

  /** Record a workstation-confirmed sync. Called only after the receiver
   *  validates and stages the package; a failed upload marks nothing, so
   *  the evidence stays pending and retryable. */
  async markSynced(observationIds: string[], packageId: string, exportedAt: string): Promise<void> {
    for (const id of observationIds) {
      const obs = await this.getObservation(id);
      if (!obs) continue;
      if (!obs.export_history.some((h) => h.package_id === packageId)) {
        obs.export_history.push({ package_id: packageId, exported_at: exportedAt, via: 'sync' });
        await this.saveObservation(obs);
      }
    }
  }

  // -- images -------------------------------------------------------------

  async listImages(observationId: string): Promise<EvidenceImage[]> {
    const entries = await this.backend.all<EvidenceImage>('images');
    const images: EvidenceImage[] = [];
    for (const { key, value } of entries) {
      const issues = validateImage(value);
      if (issues.length > 0) {
        this.quarantine('images', key, issues);
        continue;
      }
      if (value.observation_id === observationId) images.push(value);
    }
    images.sort((a, b) => a.captured_at.localeCompare(b.captured_at));
    return images;
  }

  async getImage(imageId: string): Promise<EvidenceImage | undefined> {
    const value = await this.backend.get<EvidenceImage>('images', imageId);
    if (value === undefined) return undefined;
    const issues = validateImage(value);
    if (issues.length > 0) {
      this.quarantine('images', imageId, issues);
      return undefined;
    }
    return value;
  }

  /** Store image metadata with its original bytes (+ optional preview).
   *  If a binary write fails, the metadata write is rolled back so no
   *  metadata ever points at missing evidence. */
  async addImageWithBytes(args: {
    image: EvidenceImage;
    original: Blob;
    preview?: Blob | null;
  }): Promise<void> {
    const issues = validateImage(args.image);
    if (issues.length > 0) {
      throw new Error(`Refusing to save invalid image: ${issues.join('; ')}`);
    }
    if (args.original.size !== args.image.byte_length) {
      throw new Error('Original bytes do not match the recorded byte_length.');
    }
    await this.backend.put('binaries', ORIG_PREFIX + args.image.image_id, args.original);
    try {
      if (args.preview) {
        await this.backend.put('binaries', PREV_PREFIX + args.image.image_id, args.preview);
      }
      await this.backend.put('images', args.image.image_id, args.image);
    } catch (error) {
      await this.backend.remove('binaries', ORIG_PREFIX + args.image.image_id);
      await this.backend.remove('binaries', PREV_PREFIX + args.image.image_id);
      throw error;
    }
  }

  async updateImage(image: EvidenceImage): Promise<void> {
    const issues = validateImage(image);
    if (issues.length > 0) {
      throw new Error(`Refusing to save invalid image: ${issues.join('; ')}`);
    }
    const existing = await this.getImage(image.image_id);
    if (!existing) throw new Error('Image is missing; it may have been deleted.');
    if (existing.sha256 !== image.sha256 || existing.byte_length !== image.byte_length) {
      throw new Error('Refusing to rewrite evidence identity; original bytes are immutable.');
    }
    await this.backend.put('images', image.image_id, image);
  }

  /** Explicit removal of one photo with its binaries. Caller confirms first. */
  async removeImage(imageId: string): Promise<void> {
    await this.backend.remove('binaries', ORIG_PREFIX + imageId);
    await this.backend.remove('binaries', PREV_PREFIX + imageId);
    await this.backend.remove('images', imageId);
  }

  async getOriginal(imageId: string): Promise<Blob | undefined> {
    return this.backend.get<Blob>('binaries', ORIG_PREFIX + imageId);
  }

  async getPreview(imageId: string): Promise<Blob | undefined> {
    return this.backend.get<Blob>('binaries', PREV_PREFIX + imageId);
  }

  /** Images (across all sessions) whose original bytes hash to `sha256`.
   *  Used to warn about duplicate imports, never to merge silently. */
  async findBySha256(sha256: string): Promise<EvidenceImage[]> {
    const entries = await this.backend.all<EvidenceImage>('images');
    const matches: EvidenceImage[] = [];
    for (const { key, value } of entries) {
      if (validateImage(value).length > 0) {
        this.quarantine('images', key, ['failed validation during duplicate scan']);
        continue;
      }
      if (value.sha256 === sha256) matches.push(value);
    }
    return matches;
  }

  // -- ui preferences (tiny key-value state, never evidence) --------------

  async getMeta(key: string): Promise<string | undefined> {
    return this.backend.get<string>('meta', key);
  }

  async setMeta(key: string, value: string): Promise<void> {
    await this.backend.put('meta', key, value);
  }

  close(): void {
    this.backend.close();
  }
}

/** Open the app repository: IndexedDB in browsers, memory elsewhere. */
export function openRepository(backend?: StorageBackend): Repository {
  if (backend) return new Repository(backend);
  const hasIndexedDb = typeof indexedDB !== 'undefined';
  return new Repository(hasIndexedDb ? new IndexedDbBackend() : new MemoryBackend());
}
