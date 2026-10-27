/** Capture data model, schema version 1.
 *
 * Every record carries `schema_version` so future readers can refuse what
 * they do not understand instead of guessing. Construction goes through the
 * `create*` factories below; validation through the `validate*` functions,
 * which return a list of human-readable issues (empty means valid).
 *
 * Field-state vocabulary, used everywhere — observed, interpreted,
 * suggested, confirmed, unknown:
 *
 * - `observed`:   seen by the operator or the camera (markings, photos).
 * - `interpreted`: derived locally without a model (counts, previews).
 * - `suggested`:   proposed by a future identification provider, unreviewed.
 * - `confirmed`:   explicitly asserted by the operator.
 * - `unknown`:     not known; the value MUST be null, never invented.
 */

import { APP_ID, SCHEMA_VERSION, isId, isIsoUtc, nowIso } from './primitives.js';

export type FieldState = 'observed' | 'interpreted' | 'suggested' | 'confirmed' | 'unknown';

export type CaptureSource = 'camera' | 'file' | 'gallery';

export type PhotoRole = 'front' | 'back' | 'detail' | 'other';

export type ReviewState = 'unreviewed' | 'reviewed' | 'confirmed';

export type MeasurementProvenance = 'operator-measured' | 'operator-estimated' | 'unknown';

export const CAPTURE_SOURCES: readonly CaptureSource[] = ['camera', 'file', 'gallery'];

export const PHOTO_ROLES: readonly PhotoRole[] = ['front', 'back', 'detail', 'other'];

export const REVIEW_STATES: readonly ReviewState[] = ['unreviewed', 'reviewed', 'confirmed'];

/** Operator-confirmable component classes. `other` means "confirmed, not listed". */
export const COMPONENT_CATEGORIES = [
  'resistor',
  'capacitor',
  'inductor',
  'diode',
  'transistor',
  'integrated-circuit',
  'connector',
  'switch',
  'relay',
  'crystal-oscillator',
  'pcb',
  'screw',
  'fastener',
  'cable-wire',
  'housing',
  'other',
] as const;

export type ComponentCategory = (typeof COMPONENT_CATEGORIES)[number];

/** Text the operator read off the component. Always `observed`, never inferred. */
export interface ObservedMarking {
  text: string;
  state: 'observed';
}

/** A value the operator either confirmed or left explicitly unknown. */
export interface ConfirmedField<T> {
  value: T | null;
  state: 'confirmed' | 'unknown';
}

/** A manually entered measurement. Unit and provenance are mandatory. */
export interface Measurement {
  label: string | null;
  value: number;
  unit: string;
  provenance: MeasurementProvenance;
}

export interface CaptureSession {
  schema_version: typeof SCHEMA_VERSION;
  session_id: string;
  name: string;
  created_at: string;
  updated_at: string;
  /** Optional link to a previously known Assembly Memory session id. */
  assembly_memory_session: string | null;
  /** Optional default tray identifier applied to new observations. */
  tray_default: string | null;
}

/** A future provider's guess. `state` is always `suggested`: a candidate is
 *  never a verified identity, and nothing here may be treated as one. */
export interface IdentificationCandidate {
  rank: number;
  label: string;
  confidence: number | null;
  supporting_observations: string[];
  uncertainty: string | null;
  provider: string;
  model: string;
  state: 'suggested';
}

export interface ComponentObservation {
  schema_version: typeof SCHEMA_VERSION;
  observation_id: string;
  session_id: string;
  /** Optional link to an existing component reference (operator-entered). */
  component_reference: string | null;
  /** Manual tray identifier (operator-entered). No auto-linking. */
  tray_reference: string | null;
  captured_at: string;
  capture_source: CaptureSource;
  review_state: ReviewState;
  operator_notes: string | null;
  markings: ObservedMarking[];
  category: ConfirmedField<string>;
  measurements: Measurement[];
  /** Operator-confirmed count. NEVER inferred from the photograph count. */
  quantity: ConfirmedField<number>;
  candidates: IdentificationCandidate[];
  provenance: {
    app: typeof APP_ID;
    app_version: string;
    user_agent: string | null;
  };
  /** Local file exports carry no `via`; network sync marks `via: 'sync'`.
   *  Absent means a legacy/local export — the transport marker is advisory. */
  export_history: Array<{ package_id: string; exported_at: string; via?: 'file' | 'sync' }>;
}

export interface EvidenceImage {
  schema_version: typeof SCHEMA_VERSION;
  image_id: string;
  observation_id: string;
  role: PhotoRole;
  /** Sanitized original filename, unique within its observation. */
  filename: string;
  mime: string;
  byte_length: number;
  /** Lowercase hex SHA-256 over the ORIGINAL bytes. */
  sha256: string;
  captured_at: string;
  /** Non-destructive crop preview as fractions of width/height (0..1). */
  crop: { x: number; y: number; width: number; height: number } | null;
}

/** MIME types accepted as photo evidence. Originals are preserved verbatim. */
export const IMAGE_MIME_TYPES = [
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/heic',
  'image/heif',
] as const;

/** Hard per-photo cap: larger files are refused with an explicit error. */
export const MAX_PHOTO_BYTES = 25 * 1024 * 1024;

const HEX64_RE = /^[0-9a-f]{64}$/;

declare const __APP_VERSION__: string;

export function appVersion(): string {
  try {
    return typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : 'dev';
  } catch {
    return 'dev';
  }
}

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------

export function createSession(name: string): CaptureSession {
  const now = nowIso();
  return {
    schema_version: SCHEMA_VERSION,
    session_id: crypto.randomUUID(),
    name: name.trim(),
    created_at: now,
    updated_at: now,
    assembly_memory_session: null,
    tray_default: null,
  };
}

export function createObservation(
  sessionId: string,
  source: CaptureSource,
  opts: { trayReference?: string | null; componentReference?: string | null } = {},
): ComponentObservation {
  return {
    schema_version: SCHEMA_VERSION,
    observation_id: crypto.randomUUID(),
    session_id: sessionId,
    component_reference: opts.componentReference ?? null,
    tray_reference: opts.trayReference ?? null,
    captured_at: nowIso(),
    capture_source: source,
    review_state: 'unreviewed',
    operator_notes: null,
    markings: [],
    category: { value: null, state: 'unknown' },
    measurements: [],
    quantity: { value: null, state: 'unknown' },
    candidates: [],
    provenance: {
      app: APP_ID,
      app_version: appVersion(),
      user_agent: typeof navigator === 'undefined' ? null : navigator.userAgent,
    },
    export_history: [],
  };
}

export function createImage(
  observationId: string,
  role: PhotoRole,
  filename: string,
  mime: string,
  byteLength: number,
  sha256: string,
): EvidenceImage {
  return {
    schema_version: SCHEMA_VERSION,
    image_id: crypto.randomUUID(),
    observation_id: observationId,
    role,
    filename: sanitizeFilename(filename),
    mime,
    byte_length: byteLength,
    sha256,
    captured_at: nowIso(),
    crop: null,
  };
}

/** Make a filename safe for storage and ZIP export. Never returns ''. */
export function sanitizeFilename(name: string): string {
  const base = name.split(/[\\/]/).pop() ?? '';
  const cleaned = base.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^\.+/, '').slice(0, 120);
  return cleaned.length > 0 ? cleaned : 'photo.jpg';
}

// ---------------------------------------------------------------------------
// Validation: each returns issues; [] is valid.
// ---------------------------------------------------------------------------

function checkSchemaVersion(record: { schema_version?: unknown }, issues: string[]): void {
  if (record.schema_version !== SCHEMA_VERSION) {
    issues.push(`unsupported schema_version ${JSON.stringify(record.schema_version)}`);
  }
}

export function validateSession(session: unknown): string[] {
  const issues: string[] = [];
  if (typeof session !== 'object' || session === null) return ['session is not an object'];
  const s = session as Record<string, unknown>;
  checkSchemaVersion(s, issues);
  if (!isId(s['session_id'])) issues.push('session_id is not a valid id');
  if (typeof s['name'] !== 'string' || s['name'].trim().length === 0) {
    issues.push('name must be a non-empty string');
  }
  if (!isIsoUtc(s['created_at'])) issues.push('created_at is not ISO 8601 UTC');
  if (!isIsoUtc(s['updated_at'])) issues.push('updated_at is not ISO 8601 UTC');
  for (const key of ['assembly_memory_session', 'tray_default'] as const) {
    const v = s[key];
    if (v !== null && (typeof v !== 'string' || v.trim().length === 0)) {
      issues.push(`${key} must be a non-empty string or null`);
    }
  }
  return issues;
}

function isConfirmedField(value: unknown): value is ConfirmedField<unknown> {
  if (typeof value !== 'object' || value === null) return false;
  const f = value as Record<string, unknown>;
  return (
    (f['state'] === 'confirmed' && f['value'] !== null && f['value'] !== undefined) ||
    (f['state'] === 'unknown' && f['value'] === null)
  );
}

export function validateObservation(obs: unknown): string[] {
  const issues: string[] = [];
  if (typeof obs !== 'object' || obs === null) return ['observation is not an object'];
  const o = obs as Record<string, unknown>;
  checkSchemaVersion(o, issues);
  if (!isId(o['observation_id'])) issues.push('observation_id is not a valid id');
  if (!isId(o['session_id'])) issues.push('session_id is not a valid id');
  for (const key of ['component_reference', 'tray_reference', 'operator_notes'] as const) {
    const v = o[key];
    if (v !== null && (typeof v !== 'string' || v.trim().length === 0)) {
      issues.push(`${key} must be a non-empty string or null`);
    }
  }
  if (!isIsoUtc(o['captured_at'])) issues.push('captured_at is not ISO 8601 UTC');
  if (!CAPTURE_SOURCES.includes(o['capture_source'] as CaptureSource)) {
    issues.push('capture_source must be camera, file or gallery');
  }
  if (!REVIEW_STATES.includes(o['review_state'] as ReviewState)) {
    issues.push('review_state must be unreviewed, reviewed or confirmed');
  }
  if (!Array.isArray(o['markings'])) {
    issues.push('markings must be an array');
  } else {
    o['markings'].forEach((m: unknown, i: number) => {
      const marking = m as Record<string, unknown>;
      if (typeof marking !== 'object' || marking === null) {
        issues.push(`markings[${i}] is not an object`);
      } else if (typeof marking['text'] !== 'string' || marking['text'].trim().length === 0) {
        issues.push(`markings[${i}].text must be a non-empty string`);
      } else if (marking['state'] !== 'observed') {
        issues.push(`markings[${i}].state must be 'observed'`);
      }
    });
  }
  const category = o['category'] as ConfirmedField<string> | undefined;
  if (!isConfirmedField(category)) {
    issues.push('category must be {value, state} with state confirmed|unknown');
  } else if (
    category.state === 'confirmed' &&
    (typeof category.value !== 'string' || category.value.trim().length === 0)
  ) {
    issues.push('confirmed category needs a non-empty value');
  }
  if (!Array.isArray(o['measurements'])) {
    issues.push('measurements must be an array');
  } else {
    o['measurements'].forEach((m: unknown, i: number) => {
      const meas = m as Record<string, unknown>;
      if (typeof meas !== 'object' || meas === null) {
        issues.push(`measurements[${i}] is not an object`);
        return;
      }
      if (typeof meas['value'] !== 'number' || !Number.isFinite(meas['value'])) {
        issues.push(`measurements[${i}].value must be a finite number`);
      }
      if (typeof meas['unit'] !== 'string' || meas['unit'].trim().length === 0) {
        issues.push(`measurements[${i}] needs an explicit unit`);
      }
      if (!['operator-measured', 'operator-estimated', 'unknown'].includes(meas['provenance'] as string)) {
        issues.push(`measurements[${i}] needs explicit provenance`);
      }
      if (meas['label'] !== null && meas['label'] !== undefined && typeof meas['label'] !== 'string') {
        issues.push(`measurements[${i}].label must be a string or null`);
      }
    });
  }
  const quantity = o['quantity'] as ConfirmedField<number> | undefined;
  if (!isConfirmedField(quantity)) {
    issues.push('quantity must be {value, state} with state confirmed|unknown');
  } else if (
    quantity.state === 'confirmed' &&
    (typeof quantity.value !== 'number' || !Number.isInteger(quantity.value) || quantity.value < 1)
  ) {
    issues.push('confirmed quantity must be a positive integer');
  }
  if (!Array.isArray(o['candidates'])) {
    issues.push('candidates must be an array');
  } else {
    o['candidates'].forEach((c: unknown, i: number) => {
      const cand = c as Record<string, unknown>;
      if (typeof cand !== 'object' || cand === null) {
        issues.push(`candidates[${i}] is not an object`);
        return;
      }
      if (cand['state'] !== 'suggested') {
        issues.push(`candidates[${i}].state must be 'suggested', never confirmed`);
      }
      if (typeof cand['label'] !== 'string' || cand['label'].trim().length === 0) {
        issues.push(`candidates[${i}].label must be a non-empty string`);
      }
      if (typeof cand['provider'] !== 'string' || typeof cand['model'] !== 'string') {
        issues.push(`candidates[${i}] needs provider and model identifiers`);
      }
    });
  }
  const prov = o['provenance'] as Record<string, unknown> | undefined;
  if (typeof prov !== 'object' || prov === null || prov['app'] !== APP_ID) {
    issues.push('provenance.app must be anastylos-capture');
  } else {
    if (typeof prov['app_version'] !== 'string' || prov['app_version'].length === 0) {
      issues.push('provenance.app_version must be a non-empty string');
    }
    if (prov['user_agent'] !== null && typeof prov['user_agent'] !== 'string') {
      issues.push('provenance.user_agent must be a string or null');
    }
  }
  if (!Array.isArray(o['export_history'])) {
    issues.push('export_history must be an array');
  }
  return issues;
}

export function validateImage(image: unknown): string[] {
  const issues: string[] = [];
  if (typeof image !== 'object' || image === null) return ['image is not an object'];
  const g = image as Record<string, unknown>;
  checkSchemaVersion(g, issues);
  if (!isId(g['image_id'])) issues.push('image_id is not a valid id');
  if (!isId(g['observation_id'])) issues.push('observation_id is not a valid id');
  if (!PHOTO_ROLES.includes(g['role'] as PhotoRole)) {
    issues.push('role must be front, back, detail or other');
  }
  if (typeof g['filename'] !== 'string' || g['filename'].length === 0 || g['filename'] !== sanitizeFilename(g['filename'])) {
    issues.push('filename is empty or unsafe');
  }
  if (!(IMAGE_MIME_TYPES as readonly string[]).includes(g['mime'] as string)) {
    issues.push(`mime ${JSON.stringify(g['mime'])} is not an accepted image type`);
  }
  if (typeof g['byte_length'] !== 'number' || !Number.isInteger(g['byte_length']) || g['byte_length'] <= 0) {
    issues.push('byte_length must be a positive integer');
  } else if (g['byte_length'] > MAX_PHOTO_BYTES) {
    issues.push(`byte_length exceeds the ${MAX_PHOTO_BYTES}-byte photo cap`);
  }
  if (typeof g['sha256'] !== 'string' || !HEX64_RE.test(g['sha256'])) {
    issues.push('sha256 must be lowercase hex over the original bytes');
  }
  if (!isIsoUtc(g['captured_at'])) issues.push('captured_at is not ISO 8601 UTC');
  const crop = g['crop'] as Record<string, unknown> | null;
  if (crop !== null) {
    if (typeof crop !== 'object') {
      issues.push('crop must be an object or null');
    } else {
      for (const key of ['x', 'y', 'width', 'height'] as const) {
        const v = crop[key];
        if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 1) {
          issues.push(`crop.${key} must be a fraction in 0..1`);
        }
      }
      if (
        typeof crop['x'] === 'number' &&
        typeof crop['width'] === 'number' &&
        typeof crop['y'] === 'number' &&
        typeof crop['height'] === 'number'
      ) {
        if (crop['x'] + crop['width'] > 1 || crop['y'] + crop['height'] > 1) {
          issues.push('crop rectangle must stay inside the image');
        }
        if (crop['width'] <= 0 || crop['height'] <= 0) {
          issues.push('crop rectangle must have positive size');
        }
      }
    }
  }
  return issues;
}
