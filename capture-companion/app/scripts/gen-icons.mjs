#!/usr/bin/env node
/** Generate the monochrome PWA icons. Zero dependencies.
 *
 * Mark: a white viewfinder (corner brackets + center dot) on black,
 * echoing the capture reticle. `any` icons use rounded corners with
 * transparency; `maskable` and Apple icons are full-bleed.
 *
 * Run: `npm run icons` from capture-companion/app.
 */

import { deflateSync } from 'node:zlib';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'icons');

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) crc = CRC_TABLE[(crc ^ b) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const header = Buffer.alloc(8);
  header.writeUInt32BE(data.length, 0);
  header.write(type, 4, 'ascii');
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([Buffer.from(type, 'ascii'), data])), 0);
  return Buffer.concat([header, data, crc]);
}

function encodePng(width, height, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  const rows = [];
  for (let y = 0; y < height; y++) {
    rows.push(Buffer.from([0]));
    rows.push(Buffer.from(rgba.subarray(y * width * 4, (y + 1) * width * 4)));
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.concat(rows), { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** Paint the viewfinder mark. `pad` keeps maskable icons inside the safe zone. */
function paint(size, { rounded, pad }) {
  const rgba = new Uint8ClampedArray(size * size * 4);
  const radius = rounded ? Math.round(size * 0.225) : 0;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let alpha = 255;
      if (rounded) {
        const dx = Math.min(x, size - 1 - x);
        const dy = Math.min(y, size - 1 - y);
        if (dx < radius && dy < radius) {
          const dist = Math.hypot(radius - dx, radius - dy);
          if (dist > radius) alpha = 0;
        }
      }
      const i = (y * size + x) * 4;
      rgba[i] = 0x11;
      rgba[i + 1] = 0x11;
      rgba[i + 2] = 0x11;
      rgba[i + 3] = alpha;
    }
  }
  const white = (x, y) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    const i = (y * size + x) * 4;
    rgba[i] = 0xff;
    rgba[i + 1] = 0xff;
    rgba[i + 2] = 0xff;
    rgba[i + 3] = 0xff;
  };
  const bar = Math.max(2, Math.round(size * 0.035));
  const arm = Math.round(size * (0.5 - pad) * 0.42);
  const x0 = Math.round(size * pad);
  const x1 = size - Math.round(size * pad);
  const y0 = Math.round(size * pad);
  const y1 = size - Math.round(size * pad);
  for (let i = 0; i < bar; i++) {
    for (let a = 0; a < arm; a++) {
      white(x0 + a, y0 + i);
      white(x1 - 1 - a, y0 + i);
      white(x0 + a, y1 - 1 - i);
      white(x1 - 1 - a, y1 - 1 - i);
      white(x0 + i, y0 + a);
      white(x1 - 1 - i, y0 + a);
      white(x0 + i, y1 - 1 - a);
      white(x1 - 1 - i, y1 - 1 - a);
    }
  }
  const dotR = Math.max(2, Math.round(size * 0.028));
  const cx = size / 2;
  const cy = size / 2;
  for (let y = Math.floor(cy - dotR); y <= Math.ceil(cy + dotR); y++) {
    for (let x = Math.floor(cx - dotR); x <= Math.ceil(cx + dotR); x++) {
      if (Math.hypot(x + 0.5 - cx, y + 0.5 - cy) <= dotR) white(x, y);
    }
  }
  return rgba;
}

mkdirSync(ROOT, { recursive: true });
const targets = [
  ['icon-192.png', 192, { rounded: true, pad: 0.16 }],
  ['icon-512.png', 512, { rounded: true, pad: 0.16 }],
  ['maskable-192.png', 192, { rounded: false, pad: 0.22 }],
  ['maskable-512.png', 512, { rounded: false, pad: 0.22 }],
  ['icon-180.png', 180, { rounded: false, pad: 0.16 }],
];
for (const [name, size, opts] of targets) {
  const png = encodePng(size, size, paint(size, opts));
  writeFileSync(join(ROOT, name), png);
  console.log(`${name} (${png.length} bytes)`);
}
