/** Evidence package export: plan, validate, build.
 *
 * A package is a ZIP with `manifest.json`, original assets, optional
 * previews and optional annotations. Export re-hashes every original and
 * refuses to ship evidence whose bytes no longer match the recorded hash.
 * Nothing here transmits anything; the caller decides how the bytes leave
 * the device (download, in this iteration).
 */

import { APP_ID, SCHEMA_VERSION, isId, isIsoUtc, nowIso } from './primitives.js';
import {
  appVersion,
  type CaptureSession,
  type ComponentObservation,
  type EvidenceImage,
  validateImage,
  validateObservation,
  validateSession,
} from './schema.js';
import { sha256Hex } from './sha256.js';
import type { Repository } from './repository.js';
import { buildZip } from './zip.js';

export interface ManifestFile {
  path: string;
  kind: 'original' | 'preview';
  observation_id: string;
  image_id: string;
  sha256: string;
  bytes: number;
  mime: string;
}

export interface ExportManifest {
  schema_version: typeof SCHEMA_VERSION;
  package_id: string;
  exported_at: string;
  generator: { app: typeof APP_ID; app_version: string };
  sessions: CaptureSession[];
  observations: Array<ComponentObservation & { images: EvidenceImage[] }>;
  files: ManifestFile[];
}

export interface ExportIssue {
  observationId: string | null;
  imageId: string | null;
  message: string;
}

export interface ExportPlan {
  packageId: string;
  observations: ComponentObservation[];
  imagesByObservation: Map<string, EvidenceImage[]>;
  sessions: CaptureSession[];
  /** Blocking problems. Export must not run while this is non-empty. */
  issues: ExportIssue[];
}

export interface BuiltPackage {
  packageId: string;
  manifest: ExportManifest;
  bytes: Uint8Array;
  fileCount: number;
  totalBytes: number;
  observationIds: string[];
}

export async function blobToBytes(blob: Blob): Promise<Uint8Array> {
  return new Uint8Array(await blob.arrayBuffer());
}

function assetPath(observationId: string, filename: string): string {
  return `assets/${observationId}/${filename}`;
}

function previewPath(observationId: string, imageId: string): string {
  return `previews/${observationId}/${imageId}.jpg`;
}

/** Gather and validate everything an export of `observationIds` needs. */
export async function planExport(
  repo: Repository,
  observationIds: string[],
  packageId: string = crypto.randomUUID(),
): Promise<ExportPlan> {
  const plan: ExportPlan = {
    packageId,
    observations: [],
    imagesByObservation: new Map(),
    sessions: [],
    issues: [],
  };
  if (!isId(packageId)) {
    plan.issues.push({ observationId: null, imageId: null, message: 'package_id is not a valid id' });
    return plan;
  }
  const uniqueIds = [...new Set(observationIds)];
  if (uniqueIds.length === 0) {
    plan.issues.push({ observationId: null, imageId: null, message: 'Nothing selected for export.' });
    return plan;
  }
  const sessions = new Map<string, CaptureSession>();
  for (const id of uniqueIds) {
    const obs = await repo.getObservation(id);
    if (!obs) {
      plan.issues.push({ observationId: id, imageId: null, message: 'Observation is missing or corrupt.' });
      continue;
    }
    plan.observations.push(obs);
    const session = await repo.getSession(obs.session_id);
    if (!session) {
      plan.issues.push({
        observationId: id,
        imageId: null,
        message: 'Parent session is missing or corrupt.',
      });
    } else if (!sessions.has(session.session_id)) {
      sessions.set(session.session_id, session);
    }
    const images = await repo.listImages(id);
    if (images.length === 0) {
      plan.issues.push({
        observationId: id,
        imageId: null,
        message: 'Observation has no photo evidence; capture at least one photo first.',
      });
    }
    plan.imagesByObservation.set(id, images);
    for (const image of images) {
      const original = await repo.getOriginal(image.image_id);
      if (!original) {
        plan.issues.push({
          observationId: id,
          imageId: image.image_id,
          message: `Original bytes for ${image.filename} are missing; refusing to export without evidence.`,
        });
        continue;
      }
      if (original.size !== image.byte_length) {
        plan.issues.push({
          observationId: id,
          imageId: image.image_id,
          message: `Stored bytes for ${image.filename} disagree with the recorded length.`,
        });
        continue;
      }
      const digest = sha256Hex(await blobToBytes(original));
      if (digest !== image.sha256) {
        plan.issues.push({
          observationId: id,
          imageId: image.image_id,
          message: `Stored bytes for ${image.filename} fail the hash check; evidence may be damaged.`,
        });
      }
    }
  }
  plan.observations.sort((a, b) => a.captured_at.localeCompare(b.captured_at));
  plan.sessions = [...sessions.values()].sort((a, b) => a.created_at.localeCompare(b.created_at));
  return plan;
}

/** Build the ZIP bytes for a clean plan. Throws when issues are present. */
export async function buildPackage(
  repo: Repository,
  plan: ExportPlan,
  opts: { exportedAt?: string; mtime?: Date } = {},
): Promise<BuiltPackage> {
  if (plan.issues.length > 0) {
    throw new Error(`Export blocked: ${plan.issues[0]!.message}`);
  }
  const exportedAt = opts.exportedAt ?? nowIso();
  const mtime = opts.mtime;

  const manifest: ExportManifest = {
    schema_version: SCHEMA_VERSION,
    package_id: plan.packageId,
    exported_at: exportedAt,
    generator: { app: APP_ID, app_version: appVersion() },
    sessions: plan.sessions,
    observations: [],
    files: [],
  };

  const entries: Array<{ path: string; data: Uint8Array; mtime?: Date | undefined }> = [];
  const annotations: Record<string, { crop: NonNullable<EvidenceImage['crop']> }> = {};

  for (const obs of plan.observations) {
    const images = plan.imagesByObservation.get(obs.observation_id) ?? [];
    manifest.observations.push({ ...obs, images });
    for (const image of images) {
      const original = (await repo.getOriginal(image.image_id))!;
      const data = await blobToBytes(original);
      const path = assetPath(obs.observation_id, image.filename);
      entries.push({ path, data, mtime });
      manifest.files.push({
        path,
        kind: 'original',
        observation_id: obs.observation_id,
        image_id: image.image_id,
        sha256: image.sha256,
        bytes: image.byte_length,
        mime: image.mime,
      });
      const preview = await repo.getPreview(image.image_id);
      if (preview) {
        const previewBytes = await blobToBytes(preview);
        const previewEntry = previewPath(obs.observation_id, image.image_id);
        entries.push({ path: previewEntry, data: previewBytes, mtime });
        manifest.files.push({
          path: previewEntry,
          kind: 'preview',
          observation_id: obs.observation_id,
          image_id: image.image_id,
          sha256: sha256Hex(previewBytes),
          bytes: previewBytes.length,
          mime: preview.type || 'image/jpeg',
        });
      }
      if (image.crop) annotations[image.image_id] = { crop: image.crop };
    }
  }

  // Deterministic order: manifest first, then assets, previews, annotations.
  manifest.files.sort((a, b) => a.path.localeCompare(b.path));
  entries.sort((a, b) => a.path.localeCompare(b.path));
  if (Object.keys(annotations).length > 0) {
    entries.push({
      path: 'annotations.json',
      data: new TextEncoder().encode(JSON.stringify({ crops: annotations }, null, 2) + '\n'),
      mtime,
    });
  }
  const manifestBytes = new TextEncoder().encode(JSON.stringify(manifest, null, 2) + '\n');
  const bytes = buildZip([{ path: 'manifest.json', data: manifestBytes, mtime }, ...entries]);
  return {
    packageId: plan.packageId,
    manifest,
    bytes,
    fileCount: entries.length + 1,
    totalBytes: bytes.length,
    observationIds: plan.observations.map((o) => o.observation_id),
  };
}

/** Validate a parsed manifest the same way the Python side does. [] is valid. */
export function validateManifest(manifest: unknown): string[] {
  const issues: string[] = [];
  if (typeof manifest !== 'object' || manifest === null) return ['manifest is not an object'];
  const m = manifest as Record<string, unknown>;
  if (m['schema_version'] !== SCHEMA_VERSION) {
    issues.push(`unsupported schema_version ${JSON.stringify(m['schema_version'])}`);
  }
  if (!isId(m['package_id'])) issues.push('package_id is not a valid id');
  if (!isIsoUtc(m['exported_at'])) issues.push('exported_at is not ISO 8601 UTC');
  const generator = m['generator'] as Record<string, unknown> | undefined;
  if (typeof generator !== 'object' || generator === null || generator['app'] !== APP_ID) {
    issues.push('generator.app must be anastylos-capture');
  }
  if (!Array.isArray(m['sessions']) || !Array.isArray(m['observations']) || !Array.isArray(m['files'])) {
    issues.push('manifest needs sessions, observations and files arrays');
    return issues;
  }
  const sessions = m['sessions'] as unknown[];
  const observations = m['observations'] as unknown[];
  const files = m['files'] as unknown[];
  const sessionIds = new Set<string>();
  sessions.forEach((s, i) => {
    for (const issue of validateSession(s)) issues.push(`sessions[${i}]: ${issue}`);
    const id = (s as { session_id?: unknown })?.session_id;
    if (typeof id === 'string') {
      if (sessionIds.has(id)) issues.push(`duplicate session_id ${id}`);
      sessionIds.add(id);
    }
  });
  const observationIds = new Set<string>();
  observations.forEach((o, i) => {
    const record = o as Record<string, unknown>;
    const images = record['images'];
    if (!Array.isArray(images)) {
      issues.push(`observations[${i}] carries no images array`);
      return;
    }
    for (const issue of validateObservation(o)) issues.push(`observations[${i}]: ${issue}`);
    const id = record['observation_id'];
    if (typeof id === 'string') {
      if (observationIds.has(id)) issues.push(`duplicate observation_id ${id}`);
      observationIds.add(id);
    }
    if (typeof record['session_id'] === 'string' && !sessionIds.has(record['session_id'])) {
      issues.push(`observations[${i}] references unknown session ${record['session_id']}`);
    }
    images.forEach((g: unknown, j: number) => {
      for (const issue of validateImage(g)) issues.push(`observations[${i}].images[${j}]: ${issue}`);
    });
  });
  const paths = new Set<string>();
  files.forEach((f: unknown, i: number) => {
    const file = f as Record<string, unknown>;
    if (typeof file !== 'object' || file === null) {
      issues.push(`files[${i}] is not an object`);
      return;
    }
    const path = file['path'];
    if (typeof path !== 'string' || path.length === 0) issues.push(`files[${i}] has no path`);
    else if (paths.has(path)) issues.push(`duplicate file path ${path}`);
    else paths.add(path);
    if (file['kind'] !== 'original' && file['kind'] !== 'preview') {
      issues.push(`files[${i}] has unknown kind`);
    }
    if (!isId(file['observation_id']) || !isId(file['image_id'])) {
      issues.push(`files[${i}] needs observation and image ids`);
    }
  });
  return issues;
}
