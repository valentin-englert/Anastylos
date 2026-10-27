/** Minimal standards-compliant ZIP writer (stored entries, no compression).
 *
 * Deliberately dependency-free: stored (method 0) entries are valid ZIP and
 * are read by Python's `zipfile`, Windows Explorer, macOS Archive Utility
 * and `unzip`. Deflate would shrink photos only marginally (JPEG/PNG/HEIC
 * are already compressed) while adding real complexity, so it is out of
 * scope. Filenames are UTF-8 (general-purpose bit 11).
 *
 * Limits (refused with explicit errors, never truncated silently):
 * under 65,535 entries, every entry under 4 GiB, total under 4 GiB.
 */

export interface ZipEntry {
  /** Archive path with forward slashes, e.g. `assets/<id>/photo.jpg`. */
  path: string;
  data: Uint8Array;
  /** File timestamp; defaults to now. Pass a fixed date for reproducible bytes. */
  mtime?: Date | undefined;
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(data: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc = CRC_TABLE[(crc ^ data[i]!) & 0xff]! ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function dosDateTime(date: Date): { time: number; date: number } {
  const year = Math.max(1980, date.getUTCFullYear());
  return {
    time:
      ((date.getUTCHours() & 31) << 11) |
      ((date.getUTCMinutes() & 63) << 5) |
      ((date.getUTCSeconds() >> 1) & 31),
    date: (((year - 1980) & 127) << 9) | (((date.getUTCMonth() + 1) & 15) << 5) | (date.getUTCDate() & 31),
  };
}

/** Reject archive paths that are absolute, escaping, or otherwise unsafe. */
export function validateZipPath(path: string): string | null {
  if (path.length === 0) return 'empty path';
  if (path.length > 65535) return 'path too long';
  if (path.includes('\\')) return 'backslashes are forbidden';
  if (path.startsWith('/') || /^[a-zA-Z]:/.test(path)) return 'absolute paths are forbidden';
  const segments = path.split('/');
  if (segments.some((s) => s === '' || s === '.' || s === '..')) {
    return 'empty, dot or dot-dot segments are forbidden';
  }
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f\x7f]/.test(path)) return 'control characters are forbidden';
  return null;
}

export function buildZip(entries: ZipEntry[]): Uint8Array {
  if (entries.length >= 65535) throw new Error('Too many entries for this ZIP writer (< 65,535).');
  const seen = new Set<string>();
  let total = 0;
  const encoder = new TextEncoder();
  const prepared = entries.map((entry) => {
    const problem = validateZipPath(entry.path);
    if (problem) throw new Error(`Unsafe ZIP path ${JSON.stringify(entry.path)}: ${problem}.`);
    if (seen.has(entry.path)) throw new Error(`Duplicate ZIP path ${JSON.stringify(entry.path)}.`);
    seen.add(entry.path);
    if (entry.data.length >= 0xffffffff) {
      throw new Error(`Entry ${JSON.stringify(entry.path)} exceeds the 4 GiB limit.`);
    }
    total += entry.data.length;
    if (total >= 0xffffffff) throw new Error('Total ZIP payload exceeds the 4 GiB limit.');
    return { pathBytes: encoder.encode(entry.path), entry };
  });

  const chunks: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  const push = (bytes: Uint8Array) => {
    chunks.push(bytes);
    offset += bytes.length;
  };

  for (const { pathBytes, entry } of prepared) {
    const { time, date } = dosDateTime(entry.mtime ?? new Date());
    const crc = crc32(entry.data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0x0800, true); // UTF-8 filenames
    local.setUint16(8, 0, true); // stored
    local.setUint16(10, time, true);
    local.setUint16(12, date, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, entry.data.length, true);
    local.setUint32(22, entry.data.length, true);
    local.setUint16(26, pathBytes.length, true);
    local.setUint16(28, 0, true);
    const headerOffset = offset;
    push(new Uint8Array(local.buffer));
    push(pathBytes);
    push(entry.data);

    const dir = new DataView(new ArrayBuffer(46));
    dir.setUint32(0, 0x02014b50, true);
    dir.setUint16(4, 20, true);
    dir.setUint16(6, 20, true);
    dir.setUint16(8, 0x0800, true);
    dir.setUint16(10, 0, true);
    dir.setUint16(12, time, true);
    dir.setUint16(14, date, true);
    dir.setUint32(16, crc, true);
    dir.setUint32(20, entry.data.length, true);
    dir.setUint32(24, entry.data.length, true);
    dir.setUint16(28, pathBytes.length, true);
    dir.setUint16(30, 0, true);
    dir.setUint16(32, 0, true);
    dir.setUint16(34, 0, true);
    dir.setUint16(36, 0, true);
    dir.setUint32(38, 0, true);
    dir.setUint32(42, headerOffset, true);
    central.push(new Uint8Array(dir.buffer), pathBytes);
  }

  const centralOffset = offset;
  let centralSize = 0;
  for (const bytes of central) {
    chunks.push(bytes);
    centralSize += bytes.length;
  }
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, entries.length, true);
  end.setUint16(10, entries.length, true);
  end.setUint32(12, centralSize, true);
  end.setUint32(16, centralOffset, true);
  end.setUint16(20, 0, true);
  chunks.push(new Uint8Array(end.buffer));

  const out = new Uint8Array(offset + centralSize + 22);
  let at = 0;
  for (const bytes of chunks) {
    out.set(bytes, at);
    at += bytes.length;
  }
  return out;
}
