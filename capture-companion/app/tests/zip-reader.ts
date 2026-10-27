/** Independent minimal ZIP reader used ONLY as a test oracle.
 *
 * It parses end-of-central-directory + central directory + local headers
 * and verifies stored data against each entry's CRC-32. It shares no code
 * with src/lib/zip.ts on purpose: agreement between the two is the test.
 */

export interface ParsedEntry {
  path: string;
  method: number;
  data: Uint8Array;
}

const TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function checksum(data: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of data) crc = TABLE[(crc ^ byte) & 0xff]! ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

export function parseZip(bytes: Uint8Array): ParsedEntry[] {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.length);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65558); i--) {
    if (view.getUint32(i, true) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error('No end-of-central-directory found.');
  const count = view.getUint16(eocd + 10, true);
  let offset = view.getUint32(eocd + 16, true);
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const entries: ParsedEntry[] = [];
  for (let n = 0; n < count; n++) {
    if (view.getUint32(offset, true) !== 0x02014b50) throw new Error(`Bad central header ${n}.`);
    const method = view.getUint16(offset + 10, true);
    const crc = view.getUint32(offset + 16, true);
    const compressed = view.getUint32(offset + 20, true);
    const uncompressed = view.getUint32(offset + 24, true);
    const nameLen = view.getUint16(offset + 28, true);
    const extraLen = view.getUint16(offset + 30, true);
    const commentLen = view.getUint16(offset + 32, true);
    const localOffset = view.getUint32(offset + 42, true);
    const path = decoder.decode(bytes.subarray(offset + 46, offset + 46 + nameLen));
    if (view.getUint32(localOffset, true) !== 0x04034b50) {
      throw new Error(`Bad local header for ${path}.`);
    }
    const localNameLen = view.getUint16(localOffset + 26, true);
    const localExtraLen = view.getUint16(localOffset + 28, true);
    const start = localOffset + 30 + localNameLen + localExtraLen;
    const data = bytes.slice(start, start + compressed);
    if (data.length !== compressed || compressed !== uncompressed) {
      throw new Error(`Size mismatch for ${path}.`);
    }
    if (checksum(data) !== crc) throw new Error(`CRC mismatch for ${path}.`);
    entries.push({ path, method, data });
    offset += 46 + nameLen + extraLen + commentLen;
  }
  return entries;
}
