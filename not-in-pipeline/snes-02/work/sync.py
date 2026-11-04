"""SNES-02 audio sync: coarse FFT correlation + windowed drift check.

Reads snes-02/work/cam{1,2}.mono48k.wav (48 kHz mono, first sample =
first decoded audio sample of each file). Prints lag of CAM2 relative
to CAM1 in WAV-sample coordinates; caller converts to video PTS using
container audio start_times (CAM1 +0.208 s, CAM2 +0.0 s).

Method: mean-pool to 4 kHz, full-length FFT cross-correlation for
coarse lag, refine at 48 kHz in a narrow window, then repeat per
quarter-window for drift. No dependencies beyond numpy (pinned venv).
"""
import json
import struct
import sys

import numpy as np


def read_wav_mono(path):
    with open(path, "rb") as f:
        riff = f.read(12)
        assert riff[:4] == b"RIFF" and riff[8:] == b"WAVE", path
        sr = nch = sampwidth = None
        data = None
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
            chunk = f.read(size + (size % 2))
            if cid == b"fmt ":
                _, nch, sr, _, _, bits = struct.unpack(
                    "<HHIIHH", chunk[:16])
                sampwidth = bits // 8
            elif cid == b"data":
                data = chunk[:size]
    assert nch == 1 and sampwidth == 2, (path, nch, sampwidth)
    return sr, np.frombuffer(data, dtype=np.int16).astype(np.float64)


def pool(x, n):
    m = (len(x) // n) * n
    return x[:m].reshape(-1, n).mean(axis=1)


def fft_lag(a, b, max_lag):
    """Lag of b relative to a in samples of a's rate, |lag| <= max_lag.

    Returns lag L maximizing sum a[t]*b[t-L]... using correlation
    c[k] = sum_t a[t]*b[t+k]; L = argmax. Positive L means b lags a
    (b's copy of an event sits L samples later than a's).
    """
    n = 1
    while n < len(a) + len(b):
        n *= 2
    corr = np.fft.ifft(np.fft.fft(a, n) * np.conj(np.fft.fft(b, n))).real
    # fft-based circular corr: index k holds sum a[t]*b[t+k] for small k
    # (with wraparound at the tail for negative k).
    best_k, best_v = 0, -np.inf
    for k in range(-max_lag, max_lag + 1):
        v = corr[k % n]
        if v > best_v:
            best_v, best_k = v, k
    return best_k, best_v


def norm(x):
    x = x - x.mean()
    s = x.std()
    return x / s if s > 0 else x


def main():
    sr1, w1 = read_wav_mono("snes-02/work/cam1.mono48k.wav")
    sr2, w2 = read_wav_mono("snes-02/work/cam2.mono48k.wav")
    assert sr1 == sr2 == 48000, (sr1, sr2)
    print(f"wav len s: cam1={len(w1)/48000:.3f} cam2={len(w2)/48000:.3f}")

    # Coarse: 4 kHz full-length, search +/-4 s.
    a4, b4 = norm(pool(w1, 12)), norm(pool(w2, 12))
    coarse, v = fft_lag(a4, b4, 4 * 4000)
    print(f"coarse lag @4kHz: {coarse} samples = {coarse/4000:.6f} s "
          f"(peak={v:.3g})")

    # Refine: 48 kHz in +/-120 ms around coarse lag. Scan at 12-sample
    # steps, print the profile's top local maxima, then 1-sample refine.
    center = int(round(coarse * 12))
    lo, hi = center - int(0.12 * 48000), center + int(0.12 * 48000)
    n1, n2 = norm(w1), norm(w2)

    def score48(k):
        i0, i1 = max(0, -k), min(len(n1), len(n2) - k)
        if i1 - i0 < 48000:
            return -np.inf
        return float(np.dot(n1[i0:i1], n2[i0 + k:i1 + k]))

    ks = list(range(lo, hi + 1, 12))
    prof = [(k, score48(k)) for k in ks]
    locmax = [p for i, p in enumerate(prof[1:-1], 1)
              if p[1] >= prof[i-1][1] and p[1] >= prof[i+1][1]]
    locmax.sort(key=lambda p: -p[1])
    print("top local maxima near coarse (lag_s, score):")
    for k, v in locmax[:5]:
        print(f"  {k/48000:+.6f}  {v:.6g}")
    best_k = locmax[0][0]
    fine_k, fine_v = best_k, locmax[0][1]
    for k in range(best_k - 12, best_k + 13):  # 1-sample refine
        v = score48(k)
        if v > fine_v:
            fine_v, fine_k = v, k
    print(f"refined lag @48kHz: {fine_k} samples = {fine_k/48000:.6f} s")

    # Drift: per-quarter correlation at 4 kHz, +/-0.5 s around coarse.
    out = {"coarse_s": coarse / 4000, "refined_lag_s": fine_k / 48000,
           "windows": []}
    q = len(a4) // 4
    for i in range(4):
        seg = a4[i*q:(i+1)*q]
        # direct placement search: seg starts at i*q in a4; find its
        # start j in b4 within +/-0.5 s of the coarse expectation.
        exp = i * q + coarse
        lo = max(0, exp - 2000)
        hi = min(len(b4) - len(seg), exp + 2000)
        best_j, best_v = lo, -np.inf
        for j in range(lo, hi + 1, 4):
            v = float(np.dot(seg[::1], b4[j:j + len(seg)]))
            if v > best_v:
                best_v, best_j = v, j
        for j in range(max(lo, best_j - 4), min(hi, best_j + 4) + 1):
            v = float(np.dot(seg, b4[j:j + len(seg)]))
            if v > best_v:
                best_v, best_j = v, j
        lag = best_j - i * q
        t_mid = ((i + 0.5) * q) / 4000
        out["windows"].append(
            {"window": i, "t_mid_s": round(t_mid, 3),
             "lag_s": round(lag / 4000, 6)})
        print(f"window {i} (t~{t_mid:6.1f}s): lag {lag/4000:+.6f} s")
    json.dump(out, open("snes-02/work/audio_sync.json", "w"), indent=2)
    print("wrote snes-02/work/audio_sync.json")


if __name__ == "__main__":
    sys.exit(main())
