/** Shared primitives: schema version, identifiers, timestamps.
 *
 * Everything persisted (IndexedDB records, export manifest) carries
 * {@link SCHEMA_VERSION} so readers can refuse what they do not understand.
 */

export const SCHEMA_VERSION = 1 as const;

export const APP_ID = 'anastylos-capture' as const;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const ISO_UTC_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$/;

/** Collision-resistant identifier for sessions, observations, images, packages. */
export function newId(): string {
  return crypto.randomUUID();
}

export function isId(value: unknown): value is string {
  return typeof value === 'string' && UUID_RE.test(value);
}

/** Current time as ISO 8601 UTC (`...Z`), the only timestamp format we store. */
export function nowIso(): string {
  return new Date().toISOString();
}

export function isIsoUtc(value: unknown): value is string {
  if (typeof value !== 'string' || !ISO_UTC_RE.test(value)) return false;
  return !Number.isNaN(Date.parse(value));
}
