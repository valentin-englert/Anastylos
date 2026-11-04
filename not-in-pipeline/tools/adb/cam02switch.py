import os
import re
d = open(os.environ.get('CAM02UI', r'C:\tmp\cam02ui.xml'), encoding='utf-8').read()
for m in re.finditer(r'<node\b[^>]*>', d):
    tag = m.group(0)
    if 'Switch' not in tag:
        continue
    bm = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
    cm = re.search(r'checked="([^"]*)"', tag)
    x1, y1, x2, y2 = map(int, bm.groups())
    print('SWITCH checked=', cm.group(1), 'center=', (x1 + x2) // 2, (y1 + y2) // 2)
