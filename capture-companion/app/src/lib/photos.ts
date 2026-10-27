/** Photo intake: validate, hash, deduplicate-check, preview, store.
 *
 * Original source bytes are preserved verbatim; only a small derived JPEG
 * preview is generated for thumbnails and export previews. Intake refuses
 * over-large or non-image files with explicit errors.
 */

import {
  IMAGE_MIME_TYPES,
  MAX_PHOTO_BYTES,
  createImage,
  sanitizeFilename,
  type EvidenceImage,
  type PhotoRole,
} from './schema.js';
import { sha256Hex } from './sha256.js';
import { blobToBytes } from './export.js';
import type { Repository } from './repository.js';

export interface IntakeResult {
  image: EvidenceImage;
  /** Existing images with identical bytes (warn, never merge). */
  duplicates: EvidenceImage[];
  previewError: string | null;
}

export function uniqueFilename(existing: string[], wanted: string): string {
  const clean = sanitizeFilename(wanted);
  if (!existing.includes(clean)) return clean;
  const dot = clean.lastIndexOf('.');
  const stem = dot > 0 ? clean.slice(0, dot) : clean;
  const ext = dot > 0 ? clean.slice(dot) : '';
  for (let n = 2; n < 1000; n++) {
    const candidate = `${stem}-${n}${ext}`;
    if (!existing.includes(candidate)) return candidate;
  }
  return `${stem}-${Date.now()}${ext}`;
}

/** Downscaled JPEG preview for thumbnails. Null when canvas is unavailable. */
export async function makePreview(original: Blob, maxDim = 640): Promise<Blob | null> {
  try {
    if (typeof createImageBitmap === 'undefined' || typeof document === 'undefined') return null;
    const bitmap = await createImageBitmap(original);
    const scale = Math.min(1, maxDim / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      bitmap.close();
      return null;
    }
    ctx.drawImage(bitmap, 0, 0, width, height);
    bitmap.close();
    return await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.82));
  } catch {
    return null;
  }
}

export async function intakePhoto(
  repo: Repository,
  observationId: string,
  original: Blob,
  role: PhotoRole,
  sourceName: string,
  opts: { withPreview?: boolean } = {},
): Promise<IntakeResult> {
  if (!(IMAGE_MIME_TYPES as readonly string[]).includes(original.type)) {
    throw new Error(
      `Unsupported file type ${original.type || '(unknown)'}: use JPEG, PNG, WebP or HEIC.`,
    );
  }
  if (original.size === 0) throw new Error('Empty file: nothing to store.');
  if (original.size > MAX_PHOTO_BYTES) {
    throw new Error(
      `Photo is ${(original.size / 1048576).toFixed(1)} MB; the per-photo cap is ${MAX_PHOTO_BYTES / 1048576} MB.`,
    );
  }
  const observation = await repo.getObservation(observationId);
  if (!observation) throw new Error('Observation is missing; it may have been deleted.');
  const bytes = await blobToBytes(original);
  const digest = sha256Hex(bytes);
  const duplicates = await repo.findBySha256(digest);
  const existing = await repo.listImages(observationId);
  const image = createImage(
    observationId,
    role,
    uniqueFilename(
      existing.map((g) => g.filename),
      sourceName || `photo.${original.type === 'image/png' ? 'png' : 'jpg'}`,
    ),
    original.type,
    original.size,
    digest,
  );
  let preview: Blob | null = null;
  let previewError: string | null = null;
  if (opts.withPreview !== false) {
    preview = await makePreview(original);
    if (!preview) previewError = 'Preview unavailable; the original is stored unmodified.';
  }
  await repo.addImageWithBytes({ image, original, preview });
  return { image, duplicates, previewError };
}
