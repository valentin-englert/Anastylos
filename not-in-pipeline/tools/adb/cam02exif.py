import struct, sys

def parse_exif(path):
    d = open(path, 'rb').read()
    print('bytes=', len(d), 'magic=', d[:4].hex())
    assert d[:2] == b'\xff\xd8', 'not a JPEG'
    # walk markers to APP1 Exif
    i = 2
    exif = None
    while i < len(d):
        if d[i] != 0xFF:
            break
        m = d[i + 1]
        if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        ln = struct.unpack('>H', d[i + 2:i + 4])[0]
        if m == 0xE1 and d[i + 4:i + 10] == b'Exif\x00\x00':
            exif = d[i + 10:i + 2 + ln]
            break
        i += 2 + ln
    assert exif, 'no EXIF APP1'
    bo = '<' if exif[:2] == b'II' else '>'
    assert struct.unpack(bo + 'H', exif[2:4])[0] == 42
    TAGS = {0x8827: 'ISO', 0x829A: 'ExposureTime', 0x829D: 'FNumber',
            0x9209: 'Flash', 0xA002: 'PxWidth', 0xA003: 'PxHeight',
            0x920A: 'FocalLen', 0xA403: 'WB', 0x010F: 'Make', 0x0110: 'Model',
            0x0112: 'Orient', 0x8769: 'ExifPtr', 0xA434: 'LensModel'}
    SZ = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

    def val(typ, cnt, raw):
        if typ == 3:
            return struct.unpack(bo + 'H' * cnt, raw[:2 * cnt])[0] if cnt == 1 else struct.unpack(bo + 'H' * cnt, raw[:2 * cnt])
        if typ == 4:
            return struct.unpack(bo + 'L' * cnt, raw[:4 * cnt])[0] if cnt == 1 else struct.unpack(bo + 'L' * cnt, raw[:4 * cnt])
        if typ == 5:
            rs = [struct.unpack(bo + 'LL', raw[k:k + 8]) for k in range(0, 8 * cnt, 8)]
            return rs[0] if cnt == 1 else rs
        if typ == 10:
            rs = [struct.unpack(bo + 'll', raw[k:k + 8]) for k in range(0, 8 * cnt, 8)]
            return rs[0] if cnt == 1 else rs
        if typ == 2:
            return raw[:cnt].rstrip(b'\x00').decode('ascii', 'replace')
        return raw[:SZ[typ] * cnt].hex()

    def ifd(off, out):
        n = struct.unpack(bo + 'H', exif[off:off + 2])[0]
        for k in range(n):
            e = off + 2 + 12 * k
            tag, typ, cnt = struct.unpack(bo + 'HHL', exif[e:e + 8])
            total = SZ.get(typ, 1) * cnt
            raw = exif[e + 8:e + 12] if total <= 4 else exif[struct.unpack(bo + 'L', exif[e + 8:e + 12])[0]:][:total]
            if tag in TAGS:
                out[TAGS[tag]] = val(typ, cnt, raw)
            if tag == 0x8769:
                ifd(struct.unpack(bo + 'L', exif[e + 8:e + 12])[0], out)
    out = {}
    ifd(struct.unpack(bo + 'L', exif[4:8])[0], out)
    return out

for k, v in parse_exif(sys.argv[1]).items():
    if isinstance(v, tuple) and len(v) == 2 and v[1]:
        print(f'{k}={v[0]}/{v[1]} (={v[0]/v[1]:.6g})')
    else:
        print(f'{k}={v}')
