import os
import re
d = open(os.environ.get('CAM02UI', r'C:\tmp\cam02ui.xml'), encoding='utf-8').read()
for m in re.finditer(r'<node\b[^>]*>', d):
    tag = m.group(0)
    tm = re.search(r'text="([^"]*)"', tag)
    if not tm:
        continue
    t = tm.group(1)
    if t in ('Max',) or '1920' in t or t == 'Still':
        sm = re.search(r'selected="([^"]*)"', tag)
        bm = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
        print(ascii(t), 'selected=', sm.group(1) if sm else '?',
              'box=', bm.groups() if bm else None)
