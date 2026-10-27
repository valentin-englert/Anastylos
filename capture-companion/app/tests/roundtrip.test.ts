/** Cross-language round trip: app export -> Python validator.
 *
 * The structural assertions always run (independent ZIP parse + manifest
 * validation). The Python validator leg runs when a Python interpreter is
 * available and skips honestly otherwise.
 */

import { execFile } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { describe, expect, it } from 'vitest';
import { buildPackage, planExport, validateManifest } from '../src/lib/export.js';
import { MemoryBackend } from '../src/lib/storage.js';
import { Repository } from '../src/lib/repository.js';
import { createImage } from '../src/lib/schema.js';
import { sha256Hex } from '../src/lib/sha256.js';
import { parseZip } from './zip-reader.js';

const execFileAsync = promisify(execFile);

const VALIDATOR = fileURLToPath(new URL('../../python/anastylos_capture_validate.py', import.meta.url));

async function findPython(): Promise<string | null> {
  for (const candidate of ['python3', 'python', 'py']) {
    try {
      const args = candidate === 'py' ? ['-3.12', '--version'] : ['--version'];
      await execFileAsync(candidate, args, { timeout: 10000 });
      return candidate;
    } catch {
      // Try the next candidate.
    }
  }
  return null;
}

async function buildFixtureZip(): Promise<{ path: string; packageId: string }> {
  const repo = new Repository(new MemoryBackend());
  const session = await repo.createSession('Round trip');
  await repo.saveSession({ ...session, assembly_memory_session: 'day1', tray_default: 'T-1' });
  const obs = await repo.createObservation(session.session_id, 'camera');
  await repo.saveObservation({
    ...obs,
    operator_notes: 'Round-trip probe.',
    markings: [{ text: 'R100', state: 'observed' }],
    category: { value: 'resistor', state: 'confirmed' },
    quantity: { value: 2, state: 'confirmed' },
    review_state: 'confirmed',
  });
  for (const [role, seed] of [['front', 1], ['detail', 2]] as const) {
    const payload = new Uint8Array([0xff, 0xd8, 0xff, seed, 9, 9, 9]);
    await repo.addImageWithBytes({
      image: createImage(obs.observation_id, role, `${role}.jpg`, 'image/jpeg', payload.length, sha256Hex(payload)),
      original: new Blob([payload], { type: 'image/jpeg' }),
      preview: role === 'front' ? new Blob([new Uint8Array([1])], { type: 'image/jpeg' }) : null,
    });
  }
  const plan = await planExport(repo, [obs.observation_id]);
  expect(plan.issues).toEqual([]);
  const built = await buildPackage(repo, plan);
  expect(validateManifest(built.manifest)).toEqual([]);
  const dir = fixtureDir();
  const path = join(dir, 'roundtrip.zip');
  writeFileSync(path, built.bytes);
  return { path, packageId: built.packageId };
}

/** Writable scratch dir: OS temp first, project cache fallback (gitignored). */
function fixtureDir(): string {
  try {
    return mkdtempSync(join(tmpdir(), 'anastylos-roundtrip-'));
  } catch {
    const fallback = join(dirname(VALIDATOR), '..', 'app', 'node_modules', '.cache', 'roundtrip');
    mkdirSync(fallback, { recursive: true });
    return mkdtempSync(join(fallback, 'run-'));
  }
}

describe('export/import round trip', () => {
  it('produces a structurally sound package', async () => {
    const { path } = await buildFixtureZip();
    const { readFileSync } = await import('node:fs');
    const entries = parseZip(new Uint8Array(readFileSync(path)));
    const manifestEntry = entries.find((e) => e.path === 'manifest.json')!;
    expect(manifestEntry).toBeTruthy();
    const manifest = JSON.parse(new TextDecoder().decode(manifestEntry.data));
    expect(validateManifest(manifest)).toEqual([]);
    expect(manifest.observations).toHaveLength(1);
    expect(manifest.observations[0].images).toHaveLength(2);
    // Every referenced file exists in the archive with matching bytes + hash.
    for (const file of manifest.files) {
      const entry = entries.find((e) => e.path === file.path);
      expect(entry, `missing ${file.path}`).toBeTruthy();
      expect(entry!.data.length).toBe(file.bytes);
      expect(sha256Hex(entry!.data)).toBe(file.sha256);
    }
  });

  it('passes the Python validator when an interpreter exists', async () => {
    const python = await findPython();
    if (!python) {
      console.warn('No Python interpreter found; skipping validator leg of the round trip.');
      return;
    }
    const { path, packageId } = await buildFixtureZip();
    const args =
      python === 'py'
        ? ['-3.12', VALIDATOR, 'validate', path, '--json']
        : [VALIDATOR, 'validate', path, '--json'];
    const { stdout } = await execFileAsync(python, args, { timeout: 60000 });
    const report = JSON.parse(stdout);
    expect(report.package_id).toBe(packageId);
    expect(report.status).toBe('valid');
    expect(report.accepted_for_review).toHaveLength(1);
  }, 90000);
});
