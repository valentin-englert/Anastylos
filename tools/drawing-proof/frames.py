"""Cheap frame pre-selection: sharpness, motion, skin share, near-duplicates and time spread.

These are technical ranking signals only. They never establish that a part is complete or correctly shown;
that judgement is left to Muse (choose_frame) and the operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

import quality


@dataclass
class Candidate:
    frame_index: int
    t_s: float
    sharpness: float
    motion: float
    skin: float
    dhash: int


def _dhash(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def sample(video: Path, stride_s: float, t_range: tuple[float, float] | None = None, width: int = 480) -> list[Candidate]:
    capture = cv2.VideoCapture(str(video))
    fps = capture.get(cv2.CAP_PROP_FPS)
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    start, stop = (0, total) if t_range is None else (int(t_range[0] * fps), min(total, int(t_range[1] * fps)))
    step = max(1, round(stride_s * fps))
    capture.set(cv2.CAP_PROP_POS_FRAMES, start)
    out, previous, index = [], None, start
    try:
        while index < stop:
            ok, frame = capture.read()
            if not ok:
                break
            if (index - start) % step == 0:
                small = cv2.resize(frame, (width, round(frame.shape[0] * width / frame.shape[1])), interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                motion = float(np.mean(cv2.absdiff(gray, previous))) if previous is not None else 0.0
                out.append(Candidate(index, round(index / fps, 3), float(cv2.Laplacian(gray, cv2.CV_64F).var()), motion,
                                     quality.skin_fraction(small, np.ones(gray.shape, bool)), _dhash(gray)))
                previous = gray
            index += 1
    finally:
        capture.release()
    return out


def select(candidates: list[Candidate], count: int, min_hamming: int = 6) -> list[Candidate]:
    """Best candidate per time bucket by (low skin, low motion, high sharpness ranks), skipping near-duplicates."""
    if not candidates:
        return []
    def rank(values, reverse):
        order = sorted(range(len(values)), key=lambda i: values[i], reverse=reverse)
        r = [0] * len(values)
        for position, i in enumerate(order):
            r[i] = position
        return r
    sharp = rank([c.sharpness for c in candidates], True)
    still = rank([c.motion for c in candidates], False)
    skin = rank([c.skin for c in candidates], False)
    score = [sharp[i] + still[i] + 2 * skin[i] for i in range(len(candidates))]
    buckets = np.array_split(np.arange(len(candidates)), min(count, len(candidates)))
    chosen: list[Candidate] = []
    for bucket in buckets:
        for i in sorted(bucket, key=lambda i: score[i]):
            if all(bin(candidates[i].dhash ^ c.dhash).count("1") >= min_hamming for c in chosen):
                chosen.append(candidates[i])
                break
    return chosen


def contact_sheet(tiles: list[np.ndarray], tile_width: int = 480) -> np.ndarray:
    """Numbered tiles (1..n) in rows of three, for one Muse choice call."""
    resized = []
    for number, tile in enumerate(tiles, 1):
        t = cv2.resize(tile, (tile_width, round(tile.shape[0] * tile_width / tile.shape[1])), interpolation=cv2.INTER_AREA)
        cv2.rectangle(t, (0, 0), (60, 44), (255, 255, 255), -1)
        cv2.putText(t, str(number), (10, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 3, cv2.LINE_AA)
        resized.append(t)
    height = max(t.shape[0] for t in resized)
    padded = [cv2.copyMakeBorder(t, 0, height - t.shape[0], 0, 4, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for t in resized]
    while len(padded) % 3:
        padded.append(np.full_like(padded[0], 255))
    return np.vstack([np.hstack(padded[k:k + 3]) for k in range(0, len(padded), 3)])
