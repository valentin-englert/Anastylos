/** Workstation sync: pairing, authenticated package upload, retry.
 *
 * UI-free on purpose: the Sync view renders state, this module owns the
 * protocol. Packages are the identical ZIP bytes the file export builds
 * (`planExport` + `buildPackage`); only the transport differs.
 *
 * Security boundaries, enforced here:
 * - Sync URLs must be `https://`, except loopback (`http://localhost`,
 *   `http://127.0.0.1`) for development. Plain HTTP to a LAN host is
 *   refused outright — there is no silent plaintext fallback.
 * - Authentication is a 256-bit bearer token established at pairing time
 *   (single-use code over TLS). No credential is shipped in this code.
 * - The stored certificate fingerprint is a display reminder only. Browser
 *   JavaScript cannot see the server certificate, so programmatic pinning
 *   is impossible; the operator compares fingerprints by hand.
 *
 * Offline behavior: every failure throws `SyncError` and the caller marks
 * nothing, so failed transfers stay pending and retryable. Observations
 * are marked synced only after the workstation confirms validation.
 */

import { isId } from './primitives.js';
import type { ComponentObservation } from './schema.js';
import type { Repository } from './repository.js';

export type SyncErrorKind =
  | 'insecure-url'
  | 'network'
  | 'unauthorized'
  | 'pairing'
  | 'rate-limited'
  | 'invalid-package'
  | 'bad-metadata'
  | 'too-large'
  | 'interrupted'
  | 'server';

export class SyncError extends Error {
  readonly kind: SyncErrorKind;
  /** Acceptance report from the workstation, when it rejected a package. */
  readonly report: AcceptanceReport | null;

  constructor(kind: SyncErrorKind, message: string, report: AcceptanceReport | null = null) {
    super(message);
    this.name = 'SyncError';
    this.kind = kind;
    this.report = report;
  }
}

export interface NormalizedUrl {
  baseUrl: string;
  /** True only for explicit loopback development URLs. */
  loopbackPlaintext: boolean;
}

/** Validate and normalize a workstation base URL. Throws `SyncError`. */
export function normalizeBaseUrl(input: string): NormalizedUrl {
  const trimmed = input.trim().replace(/\/+$/, '');
  if (!trimmed) throw new SyncError('insecure-url', 'Enter the workstation address first.');
  let parsed: URL;
  try {
    parsed = new URL(trimmed.includes('://') ? trimmed : `https://${trimmed}`);
  } catch {
    throw new SyncError('insecure-url', 'That workstation address is not a valid URL.');
  }
  const host = parsed.hostname.toLowerCase();
  const loopback = host === 'localhost' || host === '127.0.0.1' || host === '::1';
  if (parsed.protocol === 'http:' && !loopback) {
    throw new SyncError(
      'insecure-url',
      'Sync needs https:// — plain HTTP to a network host would expose evidence. ' +
        'Serve the receiver with TLS (see the receiver README) instead.',
    );
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') {
    throw new SyncError('insecure-url', 'Workstation addresses must use https:// (or http:// for loopback tests).');
  }
  if (parsed.username || parsed.password) {
    throw new SyncError('insecure-url', 'Credentials do not belong in the workstation address.');
  }
  return { baseUrl: parsed.toString().replace(/\/+$/, ''), loopbackPlaintext: parsed.protocol === 'http:' };
}

// ---------------------------------------------------------------------------
// Pairing storage (repo meta; UI state, never evidence)
// ---------------------------------------------------------------------------

export interface PairedWorkstation {
  workstationId: string;
  baseUrl: string;
  token: string;
  /** Operator-compared fingerprint, display only — never enforced. */
  fingerprint: string | null;
  pairedAt: string;
}

const PAIRING_KEY = 'sync.pairing';

function isPairing(value: unknown): value is PairedWorkstation {
  if (typeof value !== 'object' || value === null) return false;
  const p = value as Record<string, unknown>;
  return (
    typeof p['workstationId'] === 'string' &&
    p['workstationId'].length > 0 &&
    typeof p['baseUrl'] === 'string' &&
    p['baseUrl'].length > 0 &&
    typeof p['token'] === 'string' &&
    p['token'].length >= 32 &&
    (p['fingerprint'] === null || typeof p['fingerprint'] === 'string') &&
    typeof p['pairedAt'] === 'string'
  );
}

export async function loadPairing(repo: Repository): Promise<PairedWorkstation | null> {
  const raw = await repo.getMeta(PAIRING_KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return isPairing(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export async function savePairing(repo: Repository, pairing: PairedWorkstation): Promise<void> {
  await repo.setMeta(PAIRING_KEY, JSON.stringify(pairing));
}

export async function clearPairing(repo: Repository): Promise<void> {
  await repo.setMeta(PAIRING_KEY, '');
}

// ---------------------------------------------------------------------------
// Sync state derived from observations (same pending rule as export)
// ---------------------------------------------------------------------------

export interface SyncState {
  pending: ComponentObservation[];
  synced: ComponentObservation[];
  /** Exported as local files (legacy or ZIP), not via sync. */
  fileExported: ComponentObservation[];
}

function syncedVia(pairing: { via?: string } | undefined): boolean {
  return pairing?.via === 'sync';
}

export function partitionSyncState(observations: ComponentObservation[]): SyncState {
  const pending: ComponentObservation[] = [];
  const synced: ComponentObservation[] = [];
  const fileExported: ComponentObservation[] = [];
  for (const obs of observations) {
    if (obs.export_history.length === 0) pending.push(obs);
    else if (obs.export_history.some(syncedVia)) synced.push(obs);
    else fileExported.push(obs);
  }
  return { pending, synced, fileExported };
}

// ---------------------------------------------------------------------------
// Receiver API client (fetch is injectable for tests)
// ---------------------------------------------------------------------------

export interface ObservationVerdict {
  observation_id: string;
  accepted: boolean;
  issues: string[];
}

export interface AcceptanceReport {
  package_id: string | null;
  status: 'valid' | 'invalid';
  issues: string[];
  observations: ObservationVerdict[];
  accepted_for_review: string[];
}

export interface UploadResult {
  duplicate: boolean;
  report: AcceptanceReport;
}

export interface WorkstationStatus {
  workstation_id: string;
  staged_packages: number;
  server_time: string;
}

export interface StagedSummary {
  package_id: string;
  staged_at: string;
  zip_sha256: string;
  status: string;
  accepted_for_review: string[];
  duplicate_deliveries: number;
}

type FetchFn = typeof fetch;

function asAcceptanceReport(value: unknown): AcceptanceReport | null {
  if (typeof value !== 'object' || value === null) return null;
  const r = value as Record<string, unknown>;
  if (r['status'] !== 'valid' && r['status'] !== 'invalid') return null;
  if (!Array.isArray(r['issues']) || !Array.isArray(r['observations']) || !Array.isArray(r['accepted_for_review'])) {
    return null;
  }
  return {
    package_id: typeof r['package_id'] === 'string' ? r['package_id'] : null,
    status: r['status'],
    issues: r['issues'].map(String),
    observations: (r['observations'] as Array<Record<string, unknown>>).map((o) => ({
      observation_id: String(o['observation_id'] ?? ''),
      accepted: o['accepted'] === true,
      issues: Array.isArray(o['issues']) ? o['issues'].map(String) : [],
    })),
    accepted_for_review: (r['accepted_for_review'] as unknown[]).map(String),
  };
}

export class SyncClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string | null,
    private readonly fetchFn: FetchFn = fetch,
  ) {}

  withToken(token: string): SyncClient {
    return new SyncClient(this.baseUrl, token, this.fetchFn);
  }

  private async request(path: string, init: RequestInit, authenticated: boolean): Promise<Response> {
    const headers = new Headers(init.headers);
    if (authenticated) {
      if (!this.token) throw new SyncError('unauthorized', 'This workstation is not paired.');
      headers.set('Authorization', `Bearer ${this.token}`);
    }
    let response: Response;
    try {
      response = await this.fetchFn(`${this.baseUrl}${path}`, { ...init, headers });
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new SyncError('interrupted', 'The transfer was interrupted; retry the whole package.');
      }
      throw new SyncError(
        'network',
        'The workstation is unreachable. Evidence stays on this device — check the address and retry.',
      );
    }
    return response;
  }

  private async readError(response: Response): Promise<string> {
    try {
      const body: unknown = await response.json();
      const message = (body as { error?: { message?: unknown } })?.error?.message;
      if (typeof message === 'string' && message.length > 0) return message;
    } catch {
      // Fall through to the status-based message.
    }
    return `The workstation answered ${response.status}.`;
  }

  /** Unauthenticated reachability probe. Proves nothing about pairing. */
  async info(): Promise<{ service: string; version: number }> {
    const response = await this.request('/v1/info', { method: 'GET' }, false);
    if (!response.ok) throw new SyncError('server', await this.readError(response));
    const body: unknown = await response.json().catch(() => null);
    if (typeof body !== 'object' || body === null) throw new SyncError('server', 'The workstation answered nonsense.');
    const typed = body as Record<string, unknown>;
    return { service: String(typed['service'] ?? ''), version: Number(typed['version'] ?? 0) };
  }

  async pair(code: string): Promise<{ workstationId: string; token: string }> {
    const normalized = code.trim();
    if (!normalized) throw new SyncError('pairing', 'Enter the pairing code shown on the workstation.');
    const response = await this.request(
      '/v1/pair',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pairing_code: normalized }),
      },
      false,
    );
    if (response.status === 429) {
      throw new SyncError('rate-limited', 'Too many pairing attempts — the code is locked for a while.');
    }
    if (response.status === 403 || response.status === 400) {
      throw new SyncError('pairing', 'Pairing failed. Check the code (single-use, expires after minutes) and retry.');
    }
    if (!response.ok) throw new SyncError('server', await this.readError(response));
    const body: unknown = await response.json().catch(() => null);
    const typed = (typeof body === 'object' && body !== null ? body : {}) as Record<string, unknown>;
    if (typeof typed['workstationId'] !== 'string' && typeof typed['workstation_id'] !== 'string') {
      throw new SyncError('server', 'The workstation answered without an identity.');
    }
    if (typeof typed['token'] !== 'string' || typed['token'].length < 32) {
      throw new SyncError('server', 'The workstation answered without a usable token.');
    }
    return {
      workstationId: String(typed['workstationId'] ?? typed['workstation_id']),
      token: typed['token'],
    };
  }

  async status(): Promise<WorkstationStatus> {
    const response = await this.request('/v1/status', { method: 'GET' }, true);
    if (response.status === 401) {
      throw new SyncError('unauthorized', 'The workstation rejected this device. Pair again (or re-issue a code).');
    }
    if (!response.ok) throw new SyncError('server', await this.readError(response));
    const body: unknown = await response.json().catch(() => null);
    const typed = (typeof body === 'object' && body !== null ? body : {}) as Record<string, unknown>;
    return {
      workstation_id: String(typed['workstation_id'] ?? ''),
      staged_packages: Number(typed['staged_packages'] ?? 0),
      server_time: String(typed['server_time'] ?? ''),
    };
  }

  async uploadPackage(args: { packageId: string; bytes: Uint8Array; sha256: string }): Promise<UploadResult> {
    if (!isId(args.packageId)) throw new SyncError('bad-metadata', 'Refusing to upload a package without a valid id.');
    if (!/^[0-9a-f]{64}$/.test(args.sha256)) {
      throw new SyncError('bad-metadata', 'Refusing to upload a package without a valid SHA-256.');
    }
    if (args.bytes.length === 0) throw new SyncError('bad-metadata', 'Refusing to upload an empty package.');
    const response = await this.request(
      '/v1/packages',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/zip',
          'X-Package-Id': args.packageId,
          'X-Package-Sha256': args.sha256,
        },
        // A fresh copy: fetch must not retain the caller's buffer.
        body: args.bytes.slice(),
      },
      true,
    );
    if (response.status === 401) {
      throw new SyncError('unauthorized', 'The workstation rejected this device. Pair again (or re-issue a code).');
    }
    if (response.status === 413) {
      throw new SyncError('too-large', 'The package exceeds the workstation size cap.');
    }
    if (response.status === 400) {
      throw new SyncError('bad-metadata', await this.readError(response));
    }
    if (response.status === 422) {
      const body: unknown = await response.json().catch(() => null);
      const report = asAcceptanceReport((body as Record<string, unknown> | null)?.['report']);
      throw new SyncError(
        'invalid-package',
        'The workstation refused the evidence: validation failed. Nothing was staged.',
        report,
      );
    }
    if (response.status === 200 || response.status === 201) {
      const body: unknown = await response.json().catch(() => null);
      const typed = (typeof body === 'object' && body !== null ? body : {}) as Record<string, unknown>;
      const report = asAcceptanceReport(typed['report']);
      if (!report) throw new SyncError('server', 'The workstation answered without a validation report.');
      if (report.package_id !== args.packageId) {
        throw new SyncError('server', 'The workstation report references a different package. Treating as failed.');
      }
      return { duplicate: typed['duplicate'] === true, report };
    }
    throw new SyncError('server', await this.readError(response));
  }

  async listPackages(): Promise<StagedSummary[]> {
    const response = await this.request('/v1/packages', { method: 'GET' }, true);
    if (response.status === 401) {
      throw new SyncError('unauthorized', 'The workstation rejected this device. Pair again (or re-issue a code).');
    }
    if (!response.ok) throw new SyncError('server', await this.readError(response));
    const body: unknown = await response.json().catch(() => null);
    const list = (body as Record<string, unknown> | null)?.['packages'];
    if (!Array.isArray(list)) throw new SyncError('server', 'The workstation answered without a package list.');
    return list.map((entry) => {
      const typed = (typeof entry === 'object' && entry !== null ? entry : {}) as Record<string, unknown>;
      return {
        package_id: String(typed['package_id'] ?? ''),
        staged_at: String(typed['staged_at'] ?? ''),
        zip_sha256: String(typed['zip_sha256'] ?? ''),
        status: String(typed['status'] ?? ''),
        accepted_for_review: Array.isArray(typed['accepted_for_review'])
          ? typed['accepted_for_review'].map(String)
          : [],
        duplicate_deliveries: Number(typed['duplicate_deliveries'] ?? 0),
      };
    });
  }
}
