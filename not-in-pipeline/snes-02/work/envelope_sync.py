"""SNES-02 sync via onset-envelope matching (robust to drone/hum).

Raw broadband correlation gave inconsistent lags across bands/windows,
so this script instead:
  1. builds a 100 Hz onset (spectral-flux proxy: rectified high-passed
     energy novelty) envelope for each track,
  2. FFT-correlates the envelopes over +/-4 s and prints the top peaks
     (uniqueness check),
  3. prints the strongest envelope peaks per track for pattern eyeballing,
  4. repeats the envelope correlation per quarter-window (drift check).

Lag convention (same as sync.py): L maximizes sum a[t]*b[t+L] with a=cam1,
b=cam2, in WAV-sample coordinates (cam1 wav t=0 <=> video PTS 0.208 s;
cam2 wav t=0 <=> video PTS 0.0 s). Positive L: cam2's copy is later.
"""
import json

import numpy as np

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "syncmod", "snes-02/work/sync.py")
syncmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(syncmod)


def onset_envelope(w, sr, hop=480):
    """100 Hz onset envelope: novelty of high-frequency energy."""
    n = (len(w) // hop) * hop
    f = w[:n].reshape(-1, hop)
    # high-pass within frame (remove DC-ish drone), energy, half-wave novelty
    e = ((f - f.mean(axis=1, keepdims=True)) ** 2).mean(axis=1)
    e = np.log1p(e / (np.median(e) + 1e-12))
    nov = np.maximum(0.0, np.diff(e, prepend=e[0]))
    return nov


def top_peaks(env, rate, n=20, min_gap_s=1.0):
    idx = np.argsort(env)[::-1]
    picked, last = [], -1e9
    for i in idx:
        if env[i] <= 0:
            break
        t = i / rate
        if all(abs(t - p[0]) > min_gap_s for p in picked):
            picked.append((round(t, 3), round(float(env[i]), 3)))
        if len(picked) >= n:
            break
    return sorted(picked)


def fft_corr_top(a, b, rate, max_lag_s=4.0, top=8):
    n = 1
    while n < len(a) + len(b):
        n *= 2
    corr = np.fft.ifft(np.fft.fft(a, n) * np.conj(np.fft.fft(b, n))).real
    ml = int(max_lag_s * rate)
    cands = [(k / rate, corr[k % n]) for k in range(-ml, ml + 1)]
    cands.sort(key=lambda p: -p[1])
    # non-maximum suppression to distinct peaks
    peaks = []
    for t, v in cands:
        if all(abs(t - p[0]) > 0.05 for p in peaks):
            peaks.append((round(t, 4), float(v)))
        if len(peaks) >= top:
            break
    return peaks


def main():
    sr1, w1 = syncmod.read_wav_mono("snes-02/work/cam1.mono48k.wav")
    sr2, w2 = syncmod.read_wav_mono("snes-02/work/cam2.mono48k.wav")
    assert sr1 == sr2 == 48000
    e1 = onset_envelope(w1, 48000)
    e2 = onset_envelope(w2, 48000)
    rate = 100.0
    print(f"envelope len: {len(e1)/rate:.1f}s / {len(e2)/rate:.1f}s")

    a, b = syncmod.norm(e1), syncmod.norm(e2)
    print("full-length envelope correlation top peaks (lag_s, score):")
    peaks = fft_corr_top(a, b, rate)
    for t, v in peaks:
        print(f"  {t:+.4f}  {v:.6g}")
    print("ratio best/runner-up: %.2f" % (peaks[0][1] / peaks[1][1]))

    print("cam1 top onset peaks (t_s, amp):")
    for p in top_peaks(e1, rate):
        print("  ", p)
    print("cam2 top onset peaks (t_s, amp):")
    for p in top_peaks(e2, rate):
        print("  ", p)

    # drift: per-quarter NCC (normalized, overlap-aware incl. negative
    # placements), +/-1 s around global best.
    L = int(round(peaks[0][0] * rate))
    out = {"global_lag_s": peaks[0][0], "windows": []}
    q = len(a) // 4

    def ncc(seg, j):
        a0 = max(0, -j)
        b0 = max(0, j)
        n = min(len(seg) - a0, len(b) - b0)
        if n < len(seg) // 2:
            return -np.inf
        x = seg[a0:a0 + n]
        y = b[b0:b0 + n]
        x = x - x.mean()
        y = y - y.mean()
        d = np.sqrt(float(np.dot(x, x) * np.dot(y, y)))
        return float(np.dot(x, y) / d) if d > 0 else -np.inf

    for i in range(4):
        seg = a[i*q:(i+1)*q]
        exp = i * q + L
        cands = [(j, ncc(seg, j)) for j in range(exp - 100, exp + 101)]
        cands.sort(key=lambda p: -p[1])
        bj, bv = cands[0]
        lag = (bj - i * q) / rate
        t_mid = ((i + 0.5) * q) / rate
        out["windows"].append({"window": i, "t_mid_s": round(t_mid, 2),
                               "lag_s": round(lag, 4),
                               "ncc": round(bv, 4),
                               "runner_up_ncc": round(cands[1][1], 4)})
        print(f"window {i} (t~{t_mid:6.1f}s): lag {lag:+.4f} s "
              f"(ncc={bv:.3f}, runner-up={cands[1][1]:.3f})")
    json.dump(out, open("snes-02/work/envelope_sync.json", "w"), indent=2)


if __name__ == "__main__":
    main()
