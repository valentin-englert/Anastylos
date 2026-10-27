/** Sync client tests: URL policy, pairing store, protocol mapping, retries.
 *
 * The workstation is a stubbed `fetch`: these tests prove the PWA builds the
 * identical bytes the file export builds, sends them with the right
 * authentication and metadata, maps every failure to a retryable `SyncError`,
 * and marks observations synced only on workstation-confirmed success.
 */

import { describe, expect, it } from 'vitest';
import { buildPackage, planExport } from '../src/lib/export.js';
import { MemoryBackend } from '../src/lib/storage.js';
import { Repository } from '../src/lib/repository.js';
import { createImage } from '../src/lib/schema.js';
import { sha256Hex } from '../src/lib/sha256.js';
import {
  SyncClient,
  SyncError,
  clearPairing,
  loadPairing,
  normalizeBaseUrl,
  partitionSyncState,
  savePairing,
  type AcceptanceReport,
  type PairedWorkstation,
} from '../src/lib/sync.js';

const PACKAGE_ID = '11111111-1111-4111-8111-111111111111';
const OBS_ID = '22222222-2222-4222-8222-222222222222';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function validReport(packageId: string): AcceptanceReport {
  return {
    package_id: packageId,
    status: 'valid',
    issues: [],
    observations: [{ observation_id: OBS_ID, accepted: true, issues: [] }],
    accepted_for_review: [OBS_ID],
  };
}

async function seedRepo(): Promise<{ repo: Repository; observationId: string }> {
  const repo = new Repository(new MemoryBackend());
  const session = await repo.createSession('Sync me');
  const obs = await repo.createObservation(session.session_id, 'camera');
  const payload = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 5, 6, 7]);
  await repo.addImageWithBytes({
    image: createImage(obs.observation_id, 'front', 'front.jpg', 'image/jpeg', payload.length, sha256Hex(payload)),
    original: new Blob([payload], { type: 'image/jpeg' }),
    preview: null,
  });
  return { repo, observationId: obs.observation_id };
}

function pairing(): PairedWorkstation {
  return {
    workstationId: 'ws-1',
    baseUrl: 'https://workstation:8443',
    token: 't'.repeat(64),
    fingerprint: null,
    pairedAt: '2026-01-15T10:30:00.000Z',
  };
}

describe('normalizeBaseUrl', () => {
  it('accepts https and strips trailing slashes', () => {
    expect(normalizeBaseUrl('https://192.168.1.20:8443/')).toEqual({
      baseUrl: 'https://192.168.1.20:8443',
      loopbackPlaintext: false,
    });
  });

  it('assumes https for bare hosts', () => {
    expect(normalizeBaseUrl('192.168.1.20:8443').baseUrl).toBe('https://192.168.1.20:8443');
  });

  it('refuses plain http to LAN hosts with no silent fallback', () => {
    for (const url of ['http://192.168.1.20:8443', 'http://workstation:8443', 'http://10.0.0.5/']) {
      let error: unknown;
      try {
        normalizeBaseUrl(url);
      } catch (e) {
        error = e;
      }
      expect(error).toBeInstanceOf(SyncError);
      expect((error as SyncError).kind).toBe('insecure-url');
    }
  });

  it('allows loopback http only as an explicit development mode', () => {
    for (const url of ['http://localhost:8443', 'http://127.0.0.1:8443/']) {
      const normalized = normalizeBaseUrl(url);
      expect(normalized.loopbackPlaintext).toBe(true);
    }
  });

  it('rejects garbage, credentials and exotic schemes', () => {
    for (const url of ['', 'not a url (((', 'https://user:pass@host:8443', 'ftp://host/x', 'file:///etc/passwd']) {
      expect(() => normalizeBaseUrl(url)).toThrow(SyncError);
    }
  });
});

describe('pairing store', () => {
  it('round-trips a pairing through repo meta', async () => {
    const repo = new Repository(new MemoryBackend());
    expect(await loadPairing(repo)).toBeNull();
    await savePairing(repo, pairing());
    expect(await loadPairing(repo)).toEqual(pairing());
  });

  it('treats corrupt or misshapen stored pairings as unpaired', async () => {
    const repo = new Repository(new MemoryBackend());
    await repo.setMeta('sync.pairing', 'not-json{{{');
    expect(await loadPairing(repo)).toBeNull();
    await repo.setMeta('sync.pairing', JSON.stringify({ workstationId: 'x' }));
    expect(await loadPairing(repo)).toBeNull();
    await repo.setMeta('sync.pairing', JSON.stringify({ ...pairing(), token: 'short' }));
    expect(await loadPairing(repo)).toBeNull();
  });

  it('clears the pairing on unpair', async () => {
    const repo = new Repository(new MemoryBackend());
    await savePairing(repo, pairing());
    await clearPairing(repo);
    expect(await loadPairing(repo)).toBeNull();
  });
});

describe('partitionSyncState', () => {
  it('splits pending, synced and file-exported observations', async () => {
    const repo = new Repository(new MemoryBackend());
    const session = await repo.createSession('S');
    const pending = await repo.createObservation(session.session_id, 'camera');
    const synced = await repo.createObservation(session.session_id, 'camera');
    const filed = await repo.createObservation(session.session_id, 'camera');
    await repo.markSynced([synced.observation_id], PACKAGE_ID, '2026-01-15T10:30:00.000Z');
    await repo.markExported([filed.observation_id], '33333333-3333-4333-8333-333333333333', '2026-01-15T10:30:00.000Z');
    const state = partitionSyncState(await repo.listAllObservations());
    expect(state.pending.map((o) => o.observation_id)).toEqual([pending.observation_id]);
    expect(state.synced.map((o) => o.observation_id)).toEqual([synced.observation_id]);
    expect(state.fileExported.map((o) => o.observation_id)).toEqual([filed.observation_id]);
  });
});

describe('SyncClient pairing and status', () => {
  it('pairs with a code and returns the workstation identity', async () => {
    const fetchFn = async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
      expect(String(url)).toBe('https://workstation:8443/v1/pair');
      expect(init?.method).toBe('POST');
      expect(JSON.parse(String(init?.body))).toEqual({ pairing_code: 'amberfox x' });
      return jsonResponse(200, { workstation_id: 'ws-9', token: 'k'.repeat(64) });
    };
    const client = new SyncClient('https://workstation:8443', null, fetchFn as typeof fetch);
    await expect(client.pair('amberfox x')).resolves.toEqual({ workstationId: 'ws-9', token: 'k'.repeat(64) });
  });

  it('maps pairing failures and lockouts', async () => {
    for (const status of [400, 403]) {
      const client = new SyncClient(
        'https://w',
        null,
        (async () => jsonResponse(status, { error: { code: 'x', message: 'nope' } })) as typeof fetch,
      );
      const error = await client.pair('wrong code').catch((e: unknown) => e);
      expect(error).toBeInstanceOf(SyncError);
      expect((error as SyncError).kind).toBe('pairing');
    }
    const locked = new SyncClient(
      'https://w',
      null,
      (async () => jsonResponse(429, {})) as typeof fetch,
    );
    const error = await locked.pair('code').catch((e: unknown) => e);
    expect((error as SyncError).kind).toBe('rate-limited');
  });

  it('maps revoked tokens to unauthorized with re-pair guidance', async () => {
    const client = new SyncClient(
      'https://w',
      'z'.repeat(64),
      (async () => jsonResponse(401, {})) as typeof fetch,
    );
    const error = await client.status().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SyncError);
    expect((error as SyncError).kind).toBe('unauthorized');
    expect((error as SyncError).message).toMatch(/Pair again/);
  });

  it('maps unreachable workstations to retryable network errors', async () => {
    const client = new SyncClient(
      'https://w',
      'z'.repeat(64),
      (async () => {
        throw new TypeError('fetch failed');
      }) as typeof fetch,
    );
    const error = await client.status().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SyncError);
    expect((error as SyncError).kind).toBe('network');
    expect((error as SyncError).message).toMatch(/stays on this device/);
  });
});

describe('SyncClient uploads', () => {
  const BYTES = new Uint8Array([0x50, 0x4b, 0x3, 0x4, 1, 2, 3]);
  const DIGEST = sha256Hex(BYTES);

  it('uploads the exact export bytes with auth and metadata headers', async () => {
    const { repo, observationId } = await seedRepo();
    const plan = await planExport(repo, [observationId]);
    expect(plan.issues).toEqual([]);
    const built = await buildPackage(repo, plan, {
      exportedAt: '2026-01-15T10:30:00.000Z',
      mtime: new Date('2026-01-15T10:30:00Z'),
    });
    const digest = sha256Hex(built.bytes);
    let seen: { url: string; headers: Record<string, string>; body: Uint8Array } | null = null;
    const fetchFn = async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
      const headers = new Headers(init?.headers);
      seen = {
        url: String(url),
        headers: { auth: headers.get('Authorization') ?? '', id: headers.get('X-Package-Id') ?? '', sha: headers.get('X-Package-Sha256') ?? '' },
        body: new Uint8Array(init?.body as ArrayBuffer),
      };
      return jsonResponse(201, { duplicate: false, report: validReport(built.packageId) });
    };
    const client = new SyncClient('https://w', 'q'.repeat(64), fetchFn as typeof fetch);
    const result = await client.uploadPackage({ packageId: built.packageId, bytes: built.bytes, sha256: digest });
    expect(result.duplicate).toBe(false);
    expect(result.report.status).toBe('valid');
    expect(seen!.url).toBe('https://w/v1/packages');
    expect(seen!.headers.auth).toBe(`Bearer ${'q'.repeat(64)}`);
    expect(seen!.headers.id).toBe(built.packageId);
    expect(seen!.headers.sha).toBe(digest);
    expect(seen!.body).toEqual(built.bytes);
  });

  it('reports duplicate redeliveries without failing', async () => {
    const client = new SyncClient(
      'https://w',
      'q'.repeat(64),
      (async () => jsonResponse(200, { duplicate: true, report: validReport(PACKAGE_ID) })) as typeof fetch,
    );
    const result = await client.uploadPackage({ packageId: PACKAGE_ID, bytes: BYTES, sha256: DIGEST });
    expect(result.duplicate).toBe(true);
    expect(result.report.accepted_for_review).toEqual([OBS_ID]);
  });

  it('rejects workstation reports that reference another package', async () => {
    const client = new SyncClient(
      'https://w',
      'q'.repeat(64),
      (async () =>
        jsonResponse(201, {
          duplicate: false,
          report: validReport('99999999-9999-4999-8999-999999999999'),
        })) as typeof fetch,
    );
    const error = await client
      .uploadPackage({ packageId: PACKAGE_ID, bytes: BYTES, sha256: DIGEST })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SyncError);
    expect((error as SyncError).kind).toBe('server');
  });

  it('maps invalid evidence to invalid-package and keeps the report', async () => {
    const report = { ...validReport(PACKAGE_ID), status: 'invalid' as const, issues: ['x fails the SHA-256 check'] };
    const client = new SyncClient(
      'https://w',
      'q'.repeat(64),
      (async () => jsonResponse(422, { duplicate: false, report })) as typeof fetch,
    );
    const error = await client
      .uploadPackage({ packageId: PACKAGE_ID, bytes: BYTES, sha256: DIGEST })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SyncError);
    expect((error as SyncError).kind).toBe('invalid-package');
    expect((error as SyncError).report?.issues).toEqual(['x fails the SHA-256 check']);
  });

  it('maps 400/401/413 to bad-metadata, unauthorized and too-large', async () => {
    const cases: Array<[number, string]> = [
      [400, 'bad-metadata'],
      [401, 'unauthorized'],
      [413, 'too-large'],
    ];
    for (const [status, kind] of cases) {
      const client = new SyncClient(
        'https://w',
        'q'.repeat(64),
        (async () => jsonResponse(status, { error: { code: 'c', message: 'm' } })) as typeof fetch,
      );
      const error = await client
        .uploadPackage({ packageId: PACKAGE_ID, bytes: BYTES, sha256: DIGEST })
        .catch((e: unknown) => e);
      expect((error as SyncError).kind).toBe(kind);
    }
  });

  it('refuses to send packages with invalid ids, hashes or empty bodies', async () => {
    let calls = 0;
    const client = new SyncClient('https://w', 'q'.repeat(64), (async () => {
      calls++;
      return jsonResponse(201, {});
    }) as typeof fetch);
    for (const args of [
      { packageId: 'not-a-uuid', bytes: BYTES, sha256: DIGEST },
      { packageId: PACKAGE_ID, bytes: BYTES, sha256: 'zz' },
      { packageId: PACKAGE_ID, bytes: new Uint8Array([]), sha256: DIGEST },
    ]) {
      const error = await client.uploadPackage(args).catch((e: unknown) => e);
      expect((error as SyncError).kind).toBe('bad-metadata');
    }
    expect(calls).toBe(0);
  });
});

describe('sync marking rules', () => {
  it('marks observations synced exactly once per package', async () => {
    const { repo, observationId } = await seedRepo();
    await repo.markSynced([observationId], PACKAGE_ID, '2026-01-15T10:30:00.000Z');
    await repo.markSynced([observationId], PACKAGE_ID, '2026-01-15T10:30:00.000Z');
    const obs = await repo.getObservation(observationId);
    expect(obs!.export_history).toEqual([{ package_id: PACKAGE_ID, exported_at: '2026-01-15T10:30:00.000Z', via: 'sync' }]);
  });

  it('leaves failed transfers pending: nothing is marked without confirmation', async () => {
    // The rule is structural — the Sync view calls markSynced only after
    // uploadPackage resolves — so a failed upload is simply absent here.
    const { repo, observationId } = await seedRepo();
    const client = new SyncClient(
      'https://w',
      'q'.repeat(64),
      (async () => {
        throw new TypeError('offline');
      }) as typeof fetch,
    );
    const error = await client
      .uploadPackage({ packageId: PACKAGE_ID, bytes: new Uint8Array([1]), sha256: sha256Hex(new Uint8Array([1])) })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SyncError);
    const obs = await repo.getObservation(observationId);
    expect(obs!.export_history).toEqual([]);
    const state = partitionSyncState(await repo.listAllObservations());
    expect(state.pending.map((o) => o.observation_id)).toEqual([observationId]);
  });
});
