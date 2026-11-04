"""One-off repair: shift still filenames in *-pairs.json from 000-based
to 001-based (image2 muxer numbers from 1). PTS/frame/residual data
untouched. Skips unreadable files (reported, not overwritten)."""
import glob
import json
import re

for p in sorted(glob.glob("snes-02/work/cases/*/*-pairs.json")):
    try:
        with open(p) as f:
            spec = json.load(f)
    except Exception as e:
        print(f"SKIP (unreadable): {p}: {e}")
        continue
    for q in spec["pairs"]:
        for cam in ("cam1", "cam2"):
            q[cam]["file"] = re.sub(
                r"-(\d{3})\.jpg",
                lambda m: "-%03d.jpg" % (int(m.group(1)) + 1),
                q[cam]["file"])
    with open(p, "w") as f:
        json.dump(spec, f, indent=1)
    print(f"fixed: {p}")
