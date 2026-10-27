import { describe, expect, it } from 'vitest';
import { buildZip, crc32, validateZipPath } from '../src/lib/zip.js';
import { parseZip } from './zip-reader.js';

const toBytes = (text: string): Uint8Array => new TextEncoder().encode(text);

describe('validateZipPath', () => {
  it('accepts sane relative paths', () => {
    expect(validateZipPath('manifest.json')).toBeNull();
    expect(validateZipPath('assets/abc/photo.jpg')).toBeNull();
  });

  it('rejects escaping and absolute paths', () => {
    expect(validateZipPath('')).not.toBeNull();
    expect(validateZipPath('/etc/passwd')).not.toBeNull();
    expect(validateZipPath('C:/x')).not.toBeNull();
    expect(validateZipPath('../evil')).not.toBeNull();
    expect(validateZipPath('a/../../evil')).not.toBeNull();
    expect(validateZipPath('a\\b')).not.toBeNull();
    expect(validateZipPath('a//b')).not.toBeNull();
  });
});

describe('buildZip', () => {
  it('round-trips entries through the independent reader with verified CRCs', () => {
    const bytes = buildZip([
      { path: 'manifest.json', data: toBytes('{"a":1}') },
      { path: 'assets/id/photo.jpg', data: new Uint8Array([0, 255, 1, 2, 3]) },
      { path: 'empty.txt', data: new Uint8Array(0) },
    ]);
    const entries = parseZip(bytes);
    expect(entries.map((e) => e.path)).toEqual([
      'manifest.json',
      'assets/id/photo.jpg',
      'empty.txt',
    ]);
    expect(entries.every((e) => e.method === 0)).toBe(true);
    expect(new TextDecoder().decode(entries[0]!.data)).toBe('{"a":1}');
    expect([...entries[1]!.data]).toEqual([0, 255, 1, 2, 3]);
    expect(entries[2]!.data.length).toBe(0);
  });

  it('handles UTF-8 filenames and larger payloads', () => {
    const big = new Uint8Array(300_000).map((_, i) => i % 251);
    const bytes = buildZip([
      { path: 'assets/id/München_detail.jpg', data: big },
    ]);
    const entries = parseZip(bytes);
    expect(entries[0]!.path).toBe('assets/id/München_detail.jpg');
    expect(entries[0]!.data).toEqual(big);
  });

  it('is byte-deterministic for fixed mtimes', () => {
    const mtime = new Date('2026-01-15T10:30:00Z');
    const entries = [
      { path: 'b.txt', data: toBytes('b'), mtime },
      { path: 'a.txt', data: toBytes('a'), mtime },
    ];
    expect(buildZip(entries)).toEqual(buildZip(entries));
  });

  it('refuses unsafe and duplicate paths', () => {
    expect(() => buildZip([{ path: '../x', data: toBytes('x') }])).toThrow(/Unsafe ZIP path/);
    expect(() =>
      buildZip([
        { path: 'a.txt', data: toBytes('1') },
        { path: 'a.txt', data: toBytes('2') },
      ]),
    ).toThrow(/Duplicate ZIP path/);
  });

  it('computes CRC-32 per the standard check value', () => {
    // Standard CRC-32 check: "123456789" -> 0xCBF43926.
    expect(crc32(toBytes('123456789')).toString(16)).toBe('cbf43926');
  });
});
