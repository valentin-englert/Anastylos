import struct, zlib

def read_png_gray(path, ymax):
    d = open(path, 'rb').read()
    assert d[:8] == b'\x89PNG\r\n\x1a\n'
    w = h = None
    ctype = None
    raw = b''
    i = 8
    while i < len(d):
        ln = struct.unpack('>I', d[i:i + 4])[0]
        typ = d[i + 4:i + 8]
        if typ == b'IHDR':
            w, h, depth, ctype = struct.unpack('>IIBB', d[i + 8:i + 18])
            assert depth == 8, depth
        elif typ == b'IDAT':
            raw += d[i + 8:i + 8 + ln]
        i += 12 + ln
    px = zlib.decompress(raw)
    ch = {0: 1, 2: 3, 6: 4}[ctype]
    stride = w * ch
    tot = 0.0
    n = 0
    prev = bytearray(stride)
    p = 0
    for y in range(min(h, ymax)):
        f = px[p]
        p += 1
        line = bytearray(px[p:p + stride])
        p += stride
        if f == 1:
            for k in range(ch, stride):
                line[k] = (line[k] + line[k - ch]) & 255
        elif f == 2:
            for k in range(stride):
                line[k] = (line[k] + prev[k]) & 255
        elif f == 3:
            for k in range(stride):
                a = line[k - ch] if k >= ch else 0
                line[k] = (line[k] + ((a + prev[k]) >> 1)) & 255
        elif f == 4:
            for k in range(stride):
                a = line[k - ch] if k >= ch else 0
                b = prev[k]
                c = prev[k - ch] if k >= ch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[k] = (line[k] + pr) & 255
        prev = line
        if ch == 1:
            tot += sum(line)
            n += w
        else:
            for k in range(0, stride, ch):
                r, g, b = line[k], line[k + 1], line[k + 2]
                tot += 0.299 * r + 0.587 * g + 0.114 * b
            n += w
    return tot / n

import sys
for path in sys.argv[1:]:
    print(path, 'meanLuma(top1100)=%.1f' % read_png_gray(path, 1100))
