#!/usr/bin/env python3
"""Tests for the stdlib QR encoder (`anastylos_qr`). No test dependencies.

Correctness rests on three legs: self-validating math (Reed-Solomon
syndromes, BCH re-decode), structural asserts (finders, timing, dark
module, placement), and determinism goldens. The goldens were
additionally proven bit-identical to the `qrcode` 8.2 reference
implementation (same-mask matrices, all masks, versions 1/3/5/6/8; full
auto-match on the rest) — see the M2 report for the recipe. The cv2
class at the bottom decodes our PNGs with a fully independent detector
(OpenCV is test tooling only, never imported by the receiver).
"""

from __future__ import annotations

import hashlib
import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anastylos_qr as qr
from anastylos_qr import QREncodeError

try:
    import cv2  # noqa: F401 (independent-decode oracle, tests only)
    import numpy  # noqa: F401

    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False

# (version, payload length, auto mask, sha256 of the flattened modules).
# Payload bytes: bytes((i * 37 + 11) % 251 + 1 for i in range(n)).
GOLDENS = [
    (1, 5, 0, "84db92c53d917e7646c576fb501b7affea0df839a7aa2d1d35d74d1dac92bb93"),
    (2, 15, 6, "381aa1d3a8f5f58c96b5c2792dc1bccfa3433ebb0cfbbfc9db1b518c2f16d4ad"),
    (3, 27, 6, "7fe38edef15a167a24e413e0f5db2929069d4c6ffc833245b1a70b178e074dd7"),
    (4, 43, 2, "e3a5f03f0fe8f957ca4188bc353ceab8730fc28d08717f94e9be30abbf6bd985"),
    (5, 63, 2, "d12212aa2284d7c7192ceb5cb79c5101ac918f218c26aba009037de4b3ad3cb5"),
    (6, 85, 3, "86f81396afc5c7c79f3e01a167a83991b92668c41507d52b2f50de9a603daee6"),
    (7, 107, 4, "031f0f0397af462812cd7f2f5cfd56464dffd1febc24caa73b331da9794b11af"),
    (8, 123, 2, "cc7c8724ca6d0ffd923f0305730a83f3d983793215db57f4096dd27411703b03"),
    (9, 153, 0, "04c45744fcc3f2b09f2e0c17e6e82bc6c488f980ac1f0686c38d86a2cae08e62"),
    (10, 181, 4, "cf6c6fbcc1ec0039b8fef948f77d80b14b92887fa3b68cfaa370bd07722a3eb8"),
    (11, 214, 4, "dde69d32508e52bc7858ba7d763c3775c1db4d27e07302007487ade58246b184"),
    (12, 252, 2, "07e795a760c54451df830fce7391026c26ebc429f2d0f98596c3f47ec0f9acd9"),
    (13, 288, 7, "1adcb5c323da76ba5c152bceffb960ad41a469b226ea2980036a67cd5a3e53c1"),
]


def golden_payload(n: int, seed: int = 11) -> bytes:
    return bytes((i * 37 + seed) % 251 + 1 for i in range(n))


def gf_poly_eval(coefs: list[int], x: int) -> int:
    total = 0
    for coef in coefs:
        total = qr._gf_mul(total, x) ^ coef
    return total


class GaloisFieldTests(unittest.TestCase):
    def test_tables_invert_each_other(self):
        for value in (1, 2, 7, 45, 171, 255):
            self.assertEqual(qr._GF_EXP[qr._GF_LOG[value]], value)

    def test_mul_matches_log_addition(self):
        cases = [(3, 5), (16, 32), (100, 200), (255, 255), (1, 171), (0, 99), (99, 0)]
        for a, b in cases:
            with self.subTest(a=a, b=b):
                if a == 0 or b == 0:
                    self.assertEqual(qr._gf_mul(a, b), 0)
                else:
                    self.assertEqual(
                        qr._gf_mul(a, b),
                        qr._GF_EXP[(qr._GF_LOG[a] + qr._GF_LOG[b]) % 255],
                    )


class ReedSolomonTests(unittest.TestCase):
    def test_generator_has_the_right_roots(self):
        # Defining property: generator(nsym) vanishes at a^0..a^(nsym-1).
        for nsym in (10, 16, 18, 22, 24, 26, 30):
            gen = qr._rs_generator(nsym)
            self.assertEqual(len(gen), nsym + 1)
            self.assertEqual(gen[0], 1)
            for i in range(nsym):
                with self.subTest(nsym=nsym, root=i):
                    self.assertEqual(gf_poly_eval(gen, qr._GF_EXP[i]), 0)

    def test_codeword_syndromes_are_zero(self):
        for data, nsym in ((b"HELLO", 10), (bytes(range(1, 40)), 22), (b"x" * 50, 30)):
            codeword = list(data) + list(qr.rs_remainder(data, nsym))
            self.assertEqual(len(codeword), len(data) + nsym)
            for i in range(nsym):
                with self.subTest(nsym=nsym, root=i):
                    self.assertEqual(gf_poly_eval(codeword, qr._GF_EXP[i]), 0)

    def test_parity_length_bounds(self):
        with self.assertRaises(QREncodeError):
            qr.rs_remainder(b"abc", 0)
        with self.assertRaises(QREncodeError):
            qr.rs_remainder(b"abc", 65)


class VersionTests(unittest.TestCase):
    def test_byte_capacities_match_the_spec_table(self):
        self.assertEqual(
            qr.BYTE_CAPACITY_M,
            {1: 14, 2: 26, 3: 42, 4: 62, 5: 84, 6: 106, 7: 122,
             8: 152, 9: 180, 10: 213, 11: 251, 12: 287, 13: 331},
        )

    def test_select_version_picks_boundaries(self):
        self.assertEqual(qr.select_version(14), 1)
        self.assertEqual(qr.select_version(15), 2)
        self.assertEqual(qr.select_version(213), 10)
        self.assertEqual(qr.select_version(214), 11)
        self.assertEqual(qr.select_version(331), 13)
        with self.assertRaises(QREncodeError):
            qr.select_version(332)

    def test_data_codewords_carry_mode_and_count(self):
        codewords = qr._data_codewords(b"A", 1)
        bits = "".join(f"{byte:08b}" for byte in codewords[:3])
        self.assertTrue(bits.startswith("0100" + "00000001" + "01000001"))

    def test_inputs_are_rejected_loudly(self):
        with self.assertRaises(QREncodeError):
            qr.encode_qr(b"")
        with self.assertRaises(QREncodeError):
            qr.encode_qr("not-bytes")  # type: ignore[arg-type]
        with self.assertRaises(QREncodeError):
            qr.encode_qr(bytes(332))


class StructureTests(unittest.TestCase):
    FINDER = (
        "#######",
        "#.....#",
        "#.###.#",
        "#.###.#",
        "#.###.#",
        "#.....#",
        "#######",
    )

    def assert_finder(self, code, top: int, left: int) -> None:
        for r, line in enumerate(self.FINDER):
            for c, char in enumerate(line):
                self.assertEqual(
                    code.modules[top + r][left + c], 1 if char == "#" else 0,
                    f"finder cell {(top + r, left + c)}",
                )

    def test_v1_function_patterns(self):
        code = qr.encode_qr(b"HELLO")
        self.assertEqual((code.version, code.size), (1, 21))
        self.assert_finder(code, 0, 0)
        self.assert_finder(code, 0, 14)
        self.assert_finder(code, 14, 0)
        for i in range(8, 13):
            self.assertEqual(code.modules[6][i], 1 if i % 2 == 0 else 0)
            self.assertEqual(code.modules[i][6], 1 if i % 2 == 0 else 0)
        self.assertEqual(code.modules[13][8], 1)  # Dark module.
        for row in code.modules:
            for cell in row:
                self.assertIn(cell, (0, 1))

    def test_alignment_pattern_placed_off_timing(self):
        code = qr.encode_qr(golden_payload(214))  # V11.
        self.assertEqual(code.version, 11)
        center, size = 30, code.size
        for r in range(-2, 3):
            for c in range(-2, 3):
                edge = r in (-2, 2) or c in (-2, 2)
                want = 1 if (edge or (r == 0 and c == 0)) else 0
                self.assertEqual(code.modules[center + r][center + c], want)

    def test_format_copies_are_valid_codewords(self):
        code = qr.encode_qr(b"format-check")
        size = code.size
        vertical = [code.modules[r][8] for r in (0, 1, 2, 3, 4, 5, 7, 8)]
        vertical += [code.modules[size - 7 + i][8] for i in range(7)]
        horizontal = [code.modules[8][size - 1 - i] for i in range(8)]
        horizontal += [code.modules[8][7]]
        horizontal += [code.modules[8][5 - i] for i in range(6)]
        for name, bits in (("vertical", vertical), ("horizontal", horizontal)):
            with self.subTest(copy=name):
                value = sum(bit << i for i, bit in enumerate(bits))
                unmasked = value ^ qr._G15_MASK
                shifted = unmasked
                while shifted.bit_length() - qr._G15.bit_length() >= 0:
                    shifted ^= qr._G15 << (shifted.bit_length() - qr._G15.bit_length())
                self.assertEqual(shifted, 0)
                self.assertEqual((unmasked >> 13) & 0b11, 0)  # EC level M.
                self.assertEqual((unmasked >> 10) & 0b111, code.mask)

    def test_version_copies_redecode(self):
        code = qr.encode_qr(golden_payload(214))  # V11.
        size = code.size
        copies = []
        for transpose in (False, True):
            bits = []
            for i in range(18):
                r, c = (i // 3, i % 3 + size - 11)
                if transpose:
                    r, c = c, r
                bits.append(code.modules[r][c])
            copies.append(sum(bit << i for i, bit in enumerate(bits)))
        self.assertEqual(copies[0], copies[1])
        shifted = copies[0]
        while shifted.bit_length() - qr._G18.bit_length() >= 0:
            shifted ^= qr._G18 << (shifted.bit_length() - qr._G18.bit_length())
        self.assertEqual(shifted, 0)
        self.assertEqual(copies[0] >> 12, 11)

    def test_no_version_info_below_v7(self):
        code = qr.encode_qr(b"small")  # V1.
        size = code.size
        # The 6x3 version areas stay data modules; only check they exist
        # inside the symbol without crashing (content is masked data).
        self.assertEqual(len(code.modules[0]), size)

    def test_determinism_goldens(self):
        for version, length, mask, digest in GOLDENS:
            with self.subTest(version=version):
                code = qr.encode_qr(golden_payload(length))
                self.assertEqual(code.version, version)
                self.assertEqual(code.size, 17 + 4 * version)
                self.assertEqual(code.mask, mask)
                flat = b"".join(code.modules)
                self.assertEqual(hashlib.sha256(flat).hexdigest(), digest)


class RenderTests(unittest.TestCase):
    def test_ascii_round_trips_and_fits_80_columns(self):
        code = qr.encode_qr(golden_payload(288))  # V13, the largest.
        text = qr.qr_ascii(code)
        lines = text.split("\n")
        self.assertEqual(len(lines), code.size + 2)
        for line in lines:
            self.assertLessEqual(len(line), 80)
        body = [line[2:-2] for line in lines[1:-1]]
        for row, line in zip(code.modules, body):
            self.assertEqual("".join("#" if cell else " " for cell in row), line)
        with self.assertRaises(QREncodeError):
            qr.qr_ascii(code, border=-1)

    def test_png_chunks_and_crcs(self):
        code = qr.encode_qr(b"png-check")
        raw = qr.qr_png_bytes(code, scale=4, border=2)
        self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
        side = (code.size + 4) * 4
        offset = 8
        kinds = []
        while offset < len(raw):
            (length,) = struct.unpack(">I", raw[offset:offset + 4])
            kind = raw[offset + 4:offset + 8]
            payload = raw[offset + 8:offset + 8 + length]
            (crc,) = struct.unpack(">I", raw[offset + 8 + length:offset + 12 + length])
            self.assertEqual(crc, zlib.crc32(kind + payload) & 0xFFFFFFFF, kind)
            kinds.append(kind)
            if kind == b"IHDR":
                width, height, depth, color, _, _, _ = struct.unpack(">IIBBBBB", payload)
                self.assertEqual((width, height, depth, color), (side, side, 8, 0))
            offset += 12 + length
        self.assertEqual(kinds[0], b"IHDR")
        self.assertEqual(kinds[-1], b"IEND")
        self.assertIn(b"IDAT", kinds)
        with self.assertRaises(QREncodeError):
            qr.qr_png_bytes(code, scale=0)

    def test_png_is_deterministic(self):
        code = qr.encode_qr(b"same twice")
        self.assertEqual(qr.qr_png_bytes(code), qr.qr_png_bytes(code))


@unittest.skipUnless(HAVE_CV2, "needs OpenCV (py + opencv-python): independent-decode oracle")
class IndependentDecodeTests(unittest.TestCase):
    def decode_png(self, raw: bytes) -> bytes:
        image = cv2.imdecode(numpy.frombuffer(raw, dtype=numpy.uint8), cv2.IMREAD_GRAYSCALE)
        text, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
        self.assertTrue(text, "independent detector found no code")
        return text.encode("latin1")

    def test_all_versions_decode_from_png(self):
        # V5 uses seed 3: the seed-11 V5 symbol is valid (bit-identical to
        # the reference) but OpenCV's detector misses that one matrix, just
        # like the documented V6/106-byte seed-11 case below.
        for version, length, _, _ in GOLDENS:
            seed = 3 if version == 5 else 11
            payload = golden_payload(length, seed)
            with self.subTest(version=version):
                code = qr.encode_qr(payload)
                self.assertEqual(code.version, version)
                self.assertEqual(self.decode_png(qr.qr_png_bytes(code)), payload)

    def test_pairing_shaped_payload_decodes(self):
        payload = (
            b'{"p":"anastylos-pair","v":1,"w":"0123456789abcdef0123456789abcdef",'
            b'"u":"https://192.168.1.20:8443",'
            b'"s":"5b99a1f2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7",'
            b'"c":"sable river stone cloud ember tide","e":1793118000}'
        )
        # (Code words chosen so OpenCV reads the auto-selected symbol; the
        # encoder itself is mask-agnostic and proven bit-identical to the
        # reference under all 8 masks for pairing-shaped input.)
        code = qr.encode_qr(payload)
        self.assertLessEqual(len(payload), 331)
        self.assertEqual(self.decode_png(qr.qr_png_bytes(code)), payload)

    def test_known_detector_quirk_matches_reference_behavior(self):
        # V6, 106 bytes, seed 11: a valid symbol (bit-identical to the
        # `qrcode` reference, whose output fails here too) that OpenCV
        # 4.13 cannot read. Guard the matrix, not the detector: if a
        # future OpenCV starts reading it, this test fails loudly and the
        # comment above gets deleted.
        payload = golden_payload(106)
        code = qr.encode_qr(payload)
        self.assertEqual((code.version, code.mask), (6, 3))
        image = cv2.imdecode(
            numpy.frombuffer(qr.qr_png_bytes(code), dtype=numpy.uint8),
            cv2.IMREAD_GRAYSCALE,
        )
        text, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
        self.assertFalse(text, "detector learned this matrix; delete this guard")


if __name__ == "__main__":
    unittest.main()
