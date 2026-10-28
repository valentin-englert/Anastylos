import json

s = open("snes-02/observations/snes02-observations.json").read()
start = s.find('"events":[') + len('"events":[')
# brace-match top-level events
evs = []
depth = 0
instr = False
esc = False
cur = None
BS = chr(92)
for i in range(start, len(s)):
    ch = s[i]
    if instr:
        if esc:
            esc = False
        elif ch == BS:
            esc = True
        elif ch == '"':
            instr = False
        continue
    if ch == '"':
        instr = True
    elif ch == "{":
        if depth == 0:
            cur = i
        depth += 1
    elif ch == "}":
        depth -= 1
        if depth == 0:
            evs.append((cur, i + 1))
    elif ch == "]" and depth == 0:
        break
print("top-level events found:", len(evs))
for n, (a, b) in enumerate(evs):
    try:
        e = json.loads(s[a:b])
        assert e["event_id"]
    except Exception as ex:
        print(f"EVENT #{n} BAD ({a}-{b}): {ex}")
        print(repr(s[a:a + 200]))
        print("...")
        print(repr(s[b - 200:b]))
