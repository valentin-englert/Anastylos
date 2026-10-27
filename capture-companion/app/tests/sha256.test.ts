import { createHash, randomBytes } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { sha256Hex } from '../src/lib/sha256.js';

const vectors: Array<[string, string]> = [
  ['', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'],
  ['abc', 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'],
  [
    'abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq',
    '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
  ],
  ['a'.repeat(1000), '41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3'],
];

describe('sha256Hex', () => {
  for (const [input, digest] of vectors) {
    it(`matches the NIST vector for ${input.length} bytes`, () => {
      expect(sha256Hex(new TextEncoder().encode(input))).toBe(digest);
    });
  }

  it('agrees with node:crypto on binary data across block boundaries', () => {
    for (const length of [0, 1, 55, 56, 57, 63, 64, 65, 119, 1000, 4096]) {
      const data = new Uint8Array(randomBytes(length));
      const expected = createHash('sha256').update(data).digest('hex');
      expect(sha256Hex(data)).toBe(expected);
    }
  });
});
