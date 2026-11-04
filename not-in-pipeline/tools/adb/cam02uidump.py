import os
import re, sys
d = open(os.environ.get('CAM02UI', r'C:\tmp\cam02ui.xml'), encoding='utf-8').read()
seen = 0
for m in re.finditer(r'<node\b[^>]*>', d):
    tag = m.group(0)
    tm = re.search(r'text="([^"]*)"', tag)
    bm = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
    if not tm or not bm:
        continue
    t = tm.group(1)
    if not t.strip():
        continue
    x1, y1, x2, y2 = map(int, bm.groups())
    print(ascii(t), 'center=', (x1 + x2) // 2, (y1 + y2) // 2)
    seen += 1
    if seen >= 30:
        break
