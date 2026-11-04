import sys

s = open("snes-02/sync-manifest.json").read()
depth = 0
instr = False
esc = False
stack = []
BS = chr(92)
for i, ch in enumerate(s):
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
    elif ch in "{[":
        depth += 1
        stack.append((ch, i))
    elif ch in "}]":
        depth -= 1
        stack.pop()
print("final depth:", depth)
for c, i in stack:
    print("unclosed", c, "at", i, repr(s[max(0, i - 80):i + 10]))
