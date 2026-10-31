import os
import re
d = open(os.environ.get('CAM02UI', r'C:\tmp\cam02ui.xml'), encoding='utf-8').read()
for m in re.finditer(r'<node\b[^>]*>', d):
    tag = m.group(0)
    cm = re.search(r'class="([^"]*)"', tag)
    tm = re.search(r'text="([^"]*)"', tag)
    bm = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
    if not bm:
        continue
    cls = cm.group(1).split('.')[-1] if cm else '?'
    t = tm.group(1) if tm else ''
    x1, y1, x2, y2 = map(int, bm.groups())
    if 'SeekBar' in cls or 'Slider' in cls or (t and 'Distance' in t):
        print(ascii(cls), ascii(t), 'bounds=', x1, y1, x2, y2)
