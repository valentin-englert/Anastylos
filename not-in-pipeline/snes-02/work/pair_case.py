"""Extract time-paired stills: cam1 window [t0,t1] and cam2 window shifted
by the audio sync offset D=+0.76896 s (cam2_pts = cam1_pts + D).

Exact per-still PTS via showinfo; source frame indices via lookup in the
full PTS dumps (snes-02/work/cam{1,2}.vpts.txt). Pair check:
|pts2 - pts1 - D| must be < 40 ms or the pair is flagged MISMATCH.

Usage: python pair_case.py <t0_cam1> <t1_cam1> <fps> <outdir> <tag>
Writes outdir/<tag>-c1-NNN.jpg, <tag>-c2-NNN.jpg and <tag>-pairs.json.
"""
import json
import os
import re
import subprocess
import sys

D = 0.76896  # cam2_pts - cam1_pts for the same wall event (audio-derived)
CAM1 = r"C:\ANASTYLOS\incoming\20261028_144225CAM1.mp4"
CAM2 = r"C:\ANASTYLOS\incoming\VID_20261028_144224CAM2.mp4"

_cache = {}


def load_vpts(path):
    if path not in _cache:
        pts = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    pts.append(float(line.split(",")[0]))
        _cache[path] = pts
    return _cache[path]


def nearest_idx(pts, t):
    lo, hi = 0, len(pts) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if pts[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    best = lo
    if lo > 0 and abs(pts[lo-1] - t) < abs(pts[lo] - t):
        best = lo - 1
    return best


def extract(src, times, pattern, tol=0.016):
    sel = "+".join(
        f"between(t\\,{x-tol:.6f}\\,{x+tol:.6f})" for x in times)
    cmd = ["ffmpeg", "-v", "info", "-copyts", "-ss",
           str(max(0.0, min(times) - 2.0)), "-i", src,
           "-frames:v", str(len(times)), "-vf",
           f"select='{sel}',showinfo",
           "-fps_mode", "passthrough", "-q:v", "4", pattern, "-y"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    times = []
    for line in p.stderr.splitlines():
        m = re.search(r"showinfo.*pts_time:([0-9.]+)", line)
        if m:
            times.append(float(m.group(1)))
    return times


def main():
    t0, t1 = float(sys.argv[1]), float(sys.argv[2])
    fps, outdir, tag = sys.argv[3], sys.argv[4], sys.argv[5]
    os.makedirs(outdir, exist_ok=True)
    step = 1.0 / float(fps)
    nominal = []
    t = t0
    while t <= t1 + 1e-9:
        nominal.append(round(t, 6))
        t += step
    p1 = os.path.join(outdir, f"{tag}-c1-%03d.jpg")
    p2 = os.path.join(outdir, f"{tag}-c2-%03d.jpg")
    v1snap = load_vpts("snes-02/work/cam1.vpts.txt")
    v2snap = load_vpts("snes-02/work/cam2.vpts.txt")
    snap1 = [v1snap[nearest_idx(v1snap, x)] for x in nominal]
    snap2 = [v2snap[nearest_idx(v2snap, x + D)] for x in nominal]
    t1c = extract(CAM1, snap1, p1, tol=0.005)
    t2c = extract(CAM2, snap2, p2, tol=0.005)
    print(f"nominal={len(nominal)} cam1={len(t1c)} cam2={len(t2c)}")
    v1, v2 = load_vpts("snes-02/work/cam1.vpts.txt"), load_vpts(
        "snes-02/work/cam2.vpts.txt")
    n = min(len(t1c), len(t2c))
    pairs, bad = [], 0
    for k in range(n):
        a, b = t1c[k], t2c[k]
        i1, i2 = nearest_idx(v1, a), nearest_idx(v2, b)
        resid = (b - a) - D
        ok = abs(resid) < 0.040
        bad += (not ok)
        pairs.append({
            "k": k,
            "cam1": {"file": f"{tag}-c1-{k+1:03d}.jpg", "pts": round(a, 6),
                     "frame": i1},
            "cam2": {"file": f"{tag}-c2-{k+1:03d}.jpg", "pts": round(b, 6),
                     "frame": i2},
            "residual_ms": round(resid * 1000, 2),
            "sync_ok": ok})
    json.dump({"t0_cam1": t0, "t1_cam1": t1, "fps": fps, "D": D,
               "pairs": pairs},
              open(os.path.join(outdir, f"{tag}-pairs.json"), "w"), indent=1)
    print(f"{tag}: {n} pairs, {bad} MISMATCH (>40ms)")
    for pr in pairs:
        if not pr["sync_ok"]:
            print("  MISMATCH", pr)


if __name__ == "__main__":
    main()
