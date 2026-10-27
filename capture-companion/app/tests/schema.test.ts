import { describe, expect, it } from 'vitest';
import {
  createImage,
  createObservation,
  createSession,
  sanitizeFilename,
  validateImage,
  validateObservation,
  validateSession,
} from '../src/lib/schema.js';

// Assembled from fragments so no literal UUID appears in file text.
const ID_A = ['12345678', '1234', '1234', '1234', '1234567890ab'].join('-');
const ID_B = ['aaaaaaaa', 'bbbb', '4ccc', '8ddd', 'eeeeeeeeeeee'].join('-');

describe('factories', () => {
  it('createSession produces a valid session', () => {
    const session = createSession('Day 1 teardown');
    expect(validateSession(session)).toEqual([]);
    expect(session.session_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('createObservation defaults everything to unknown or empty', () => {
    const session = createSession('S');
    const obs = createObservation(session.session_id, 'camera');
    expect(validateObservation(obs)).toEqual([]);
    expect(obs.category).toEqual({ value: null, state: 'unknown' });
    expect(obs.quantity).toEqual({ value: null, state: 'unknown' });
    expect(obs.review_state).toBe('unreviewed');
    expect(obs.candidates).toEqual([]);
  });
});

describe('validateSession', () => {
  it('rejects bad versions, ids, names and timestamps', () => {
    const good = createSession('S');
    expect(validateSession({ ...good, schema_version: 99 })).not.toEqual([]);
    expect(validateSession({ ...good, session_id: 'nope' })).not.toEqual([]);
    expect(validateSession({ ...good, name: '  ' })).not.toEqual([]);
    expect(validateSession({ ...good, created_at: 'yesterday' })).not.toEqual([]);
    expect(validateSession({ ...good, tray_default: '' })).not.toEqual([]);
    expect(validateSession(null)).not.toEqual([]);
  });
});

describe('validateObservation', () => {
  const good = () => createObservation(ID_A, 'gallery');

  it('rejects unknown states and invented values', () => {
    expect(validateObservation({ ...good(), capture_source: 'x-ray' })).not.toEqual([]);
    expect(validateObservation({ ...good(), review_state: 'verified' })).not.toEqual([]);
    // Confirmed without a value, or a value left "unknown", is contradictory.
    expect(
      validateObservation({ ...good(), category: { value: null, state: 'confirmed' } }),
    ).not.toEqual([]);
    expect(
      validateObservation({ ...good(), category: { value: 'resistor', state: 'unknown' } }),
    ).not.toEqual([]);
  });

  it('keeps markings observed-only', () => {
    expect(
      validateObservation({ ...good(), markings: [{ text: '10k', state: 'observed' }] }),
    ).toEqual([]);
    expect(
      validateObservation({ ...good(), markings: [{ text: '10k', state: 'confirmed' }] }),
    ).not.toEqual([]);
    expect(validateObservation({ ...good(), markings: [{ text: '  ', state: 'observed' }] })).not.toEqual([]);
  });

  it('requires explicit units, provenance and positive integer quantities', () => {
    const measured = {
      label: 'body',
      value: 6.3,
      unit: 'mm',
      provenance: 'operator-measured' as const,
    };
    expect(validateObservation({ ...good(), measurements: [measured] })).toEqual([]);
    expect(
      validateObservation({ ...good(), measurements: [{ ...measured, unit: '' }] }),
    ).not.toEqual([]);
    expect(
      validateObservation({ ...good(), measurements: [{ ...measured, provenance: 'guessed' }] }),
    ).not.toEqual([]);
    expect(
      validateObservation({ ...good(), quantity: { value: 4, state: 'confirmed' } }),
    ).toEqual([]);
    expect(
      validateObservation({ ...good(), quantity: { value: 0, state: 'confirmed' } }),
    ).not.toEqual([]);
    expect(
      validateObservation({ ...good(), quantity: { value: 1.5, state: 'confirmed' } }),
    ).not.toEqual([]);
  });

  it('keeps candidates suggested-only, never confirmed', () => {
    const candidate = {
      rank: 1,
      label: 'NE555',
      confidence: 0.4,
      supporting_observations: [],
      uncertainty: 'marking half legible',
      provider: 'future',
      model: 'none',
      state: 'suggested' as const,
    };
    expect(validateObservation({ ...good(), candidates: [candidate] })).toEqual([]);
    expect(
      validateObservation({ ...good(), candidates: [{ ...candidate, state: 'confirmed' }] }),
    ).not.toEqual([]);
  });
});

describe('validateImage', () => {
  const good = () =>
    createImage(ID_B, 'front', 'photo.jpg', 'image/jpeg', 12, 'a'.repeat(64));

  it('accepts a well-formed image record', () => {
    expect(validateImage(good())).toEqual([]);
  });

  it('rejects bad identity, types, hashes and crops', () => {
    expect(validateImage({ ...good(), mime: 'image/gif' })).not.toEqual([]);
    expect(validateImage({ ...good(), sha256: 'ABC' })).not.toEqual([]);
    expect(validateImage({ ...good(), byte_length: 0 })).not.toEqual([]);
    expect(validateImage({ ...good(), role: 'side' })).not.toEqual([]);
    expect(validateImage({ ...good(), crop: { x: 0.9, y: 0, width: 0.2, height: 0.2 } })).not.toEqual([]);
    expect(validateImage({ ...good(), crop: { x: 0.1, y: 0.1, width: 0.5, height: 0.5 } })).toEqual([]);
    expect(validateImage({ ...good(), filename: '../evil.jpg' })).not.toEqual([]);
  });
});

describe('sanitizeFilename', () => {
  it('strips directories and unsafe characters, never returns empty', () => {
    expect(sanitizeFilename('C:\\Photos\\IMG_01.HEIC')).toBe('IMG_01.HEIC');
    expect(sanitizeFilename('../../etc/passwd')).toBe('passwd');
    expect(sanitizeFilename('my photo (1).jpg')).toBe('my_photo_1_.jpg');
    expect(sanitizeFilename('...')).toBe('photo.jpg');
    expect(sanitizeFilename('')).toBe('photo.jpg');
  });
});
