import os
import re
d = open(os.environ.get('CAM02UI', r'C:\tmp\cam02ui.xml'), encoding='utf-8').read()
for m in re.finditer(r'<node\b[^>]*>', d):
    tag = m.group(0)
    bm = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
    if not bm:
        continue
    x1, y1, x2, y2 = map(int, bm.groups())
    if 1880 <= y1 <= 2040 and x1 > 150:
        cm = re.search(r'class="([^"]*)"', tag)
        tm = re.search(r'text="([^"]*)"', tag)
        km = re.search(r'checkable="([^"]*)"', tag)
        print(ascii(cm.group(1) if cm else '?'), ascii(tm.group(1) if tm else ''),
              'checkable=', km.group(1) if km else '-', 'center=', (x1 + x2) // 2, (y1 + y2) // 2)
