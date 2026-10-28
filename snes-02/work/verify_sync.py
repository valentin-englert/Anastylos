"""SNES-02 sync, definitive pass: explicit-shift normalized correlation.

History: FFT-based coarse (sync.py, envelope_sync.py) reported -0.977 s,
but the onset-peak PATTERN matches at +0.98 s across ~10 independent
transient pairs (cam1 23.93->cam2 24.91, 62.68->63.66, 75.79->76.77,
89.51->90.49, 94.75->95.73, 98.98->99.96, 103.35->104.33, ...). The FFT
indexing carried a flipped sign convention. This script uses only
explicit shifts (no FFT sign ambiguity):

  score(L) = normalized dot of cam1-wav with cam2-wav shifted by L,
  L > 0 means cam2's copy is LATER (cam2 started earlier).

Outputs WAV-coordinate lag; manifest converts to video PTS with the
container audio start_times (cam1 +0.208 s, cam2 +0.0 s).
"""
import json

import numpy as np

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "syncmod", "snes-02/work/sync.py")
syncmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(syncmod)


def ncc_at(a, b, lag):
    """Normalized correlation of a[t] with b[t+lag]; lag in samples."""
    if lag >= 0:
        n = min(len(a) - lag, len(b) - lag)
        x, y = a[:n], b[lag:lag + n]
    else:
        n = min(len(a) + lag, len(b))
        x, y = a[-lag:-lag + n], b[:n]
    x = x - x.mean()
    y = y - y.mean()
    d = np.sqrt(float(np.dot(x, x) * np.dot(y, y)))
    return float(np.dot(x, y) / d) if d > 0 else -np.inf


def main():
    sr1, w1 = syncmod.read_wav_mono("snes-02/work/cam1.mono48k.wav")
    sr2, w2 = syncmod.read_wav_mono("snes-02/work/cam2.mono48k.wav")
    a4 = syncmod.norm(syncmod.pool(w1, 12))
    b4 = syncmod.norm(syncmod.pool(w2, 12))

    # 1) global scan at 4 kHz over +/-2 s, 1 ms steps.
    cands = []
    for lag_ms in range(-2000, 2001, 1):
        lag = int(round(lag_ms * 4))
        cands.append((lag_ms / 1000.0, ncc_at(a4, b4, lag)))
    cands.sort(key=lambda p: -p[1])
    print("global 4kHz NCC top 6 (lag_s, ncc):")
    for t, v in cands[:6]:
        print(f"  {t:+.3f}  {v:.5f}")
    best = cands[0][0]
    # local-maximum check: ensure best is a peak, print neighborhood
    d = {round(t, 3): v for t, v in cands}
    print("neighborhood of best:")
    for dt in [-0.01, -0.005, -0.002, -0.001, 0, 0.001, 0.002, 0.005, 0.01]:
        print(f"  {best+dt:+.3f}  {d.get(round(best+dt,3), float('nan')):.5f}")

    # 2) refine at 48 kHz, +/-30 ms, 1-sample steps.
    n1, n2 = syncmod.norm(w1), syncmod.norm(w2)
    center = int(round(best * 48000))
    prof = [(k, ncc_at(n1, n2, k))
            for k in range(center - 1440, center + 1441)]
    prof.sort(key=lambda p: -p[1])
    fk, fv = prof[0]
    print(f"refined 48kHz: lag {fk} samples = {fk/48000:+.6f} s "
          f"(ncc={fv:.5f}, runner-up={prof[1][1]:.5f})")

    # 3) drift: per-sixth NCC at 4 kHz, +/-0.5 s around refined lag.
    out = {"global_lag_wav_s": fk / 48000, "global_ncc": fv, "windows": []}
    q = len(a4) // 6
    for i in range(6):
        seg = a4[i*q:(i+1)*q]
        exp = int(round(i * q + best * 4000))
        bj, bv = exp, -np.inf
        for j in range(exp - 2000, exp + 2001, 4):
            # placement j: seg[s] <-> b4[j+s]
            if j < 0:
                n = min(len(seg) + j, len(b4))
                x, y = seg[-j:-j + n], b4[:n]
            else:
                n = min(len(seg), len(b4) - j)
                x, y = seg[:n], b4[j:j + n]
            if n < len(seg) // 2:
                continue
            x, y = x - x.mean(), y - y.mean()
            dd = np.sqrt(float(np.dot(x, x) * np.dot(y, y)))
            v = float(np.dot(x, y) / dd) if dd > 0 else -np.inf
            if v > bv:
                bv, bj = v, j
        lag = (bj - i * q) / 4000
        t_mid = ((i + 0.5) * q) / 4000
        out["windows"].append({"window": i, "t_mid_s": round(t_mid, 2),
                               "lag_s": round(lag, 4),
                               "ncc": round(bv, 4)})
        print(f"window {i} (t~{t_mid:6.1f}s): lag {lag:+.4f} s "
              f"(ncc={bv:.4f})")
    json.dump(out, open("snes-02/work/verify_sync.json", "w"), indent=2)


if __name__ == "__main__":
    main()
