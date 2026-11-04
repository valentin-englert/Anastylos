"""Shared test fixtures. Needs ANASTYLOS_ROOT (checkout of the pinned revision)
and the ANASTYLOS calibration virtualenv (OpenCV + NumPy).

All images here are SYNTHETIC renders from the existing reader modules or
cv2 drawing; stub OCR engines implement the real pcb-ocr backend interface
so the full pcb-ocr / smd-reader / component-markings code runs unchanged.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from anastylos_microscopy import env  # noqa: E402

if not os.environ.get("ANASTYLOS_ROOT"):
    raise RuntimeError("tests need ANASTYLOS_ROOT pointing at an Anastylos checkout "
                       "(see README.md 'Install')")
ROOT = env.resolve_root()
env.activate(ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

CLOCK = "2026-10-29T12:00:00Z"


class StubOCR:
    """pcb-ocr backend interface; returns fixed words for wide variants only."""

    name = "stub-ocr"

    def __init__(self, words=(), confidence=92.0):
        self.words = list(words)
        self.confidence = confidence

    def available(self):
        return True

    def version(self):
        return "stub-1 (test double, not an OCR engine)"

    def recognize(self, variant):
        height, width = variant.shape[:2]
        if width <= height or float(np.std(variant)) < 1.0 and not self.words:
            return []
        if len(self.words) == 1:
            # A centred box is invariant under the reader's 180-degree orientation pass, so
            # the stub does not fabricate a second, mirrored word that a real engine would
            # not report for upside-down text.
            return [{"text": self.words[0], "confidence": self.confidence,
                     "box_xywh": [width // 4, height // 3, width // 2, height // 3]}]
        out = []
        step = max(1, width // (len(self.words) + 1))
        for index, text in enumerate(self.words):
            out.append({"text": text, "confidence": self.confidence,
                        "box_xywh": [step * index + 10, height // 3, step - 20, height // 3]})
        return out


class SizeKeyedOCR(StubOCR):
    """Returns words depending on the original crop width (variants are 2.5x)."""

    name = "size-keyed-stub-ocr"

    def __init__(self, table):
        super().__init__()
        self.table = table

    def recognize(self, variant):
        height, width = variant.shape[:2]
        if width <= height or float(np.std(variant)) < 5.0:
            return []
        words = self.table.get(int(round(width / 2.5)), [])
        self.words = words
        return super().recognize(variant) if words else []


class FailingOCR(StubOCR):
    name = "failing-stub-ocr"

    def recognize(self, variant):
        raise RuntimeError("simulated OCR engine crash")


class MissingTesseract:
    """The real Tesseract backend pointed at a binary that does not exist."""

    def __new__(cls):
        from anastylos_pcb.backends import TesseractBackend
        return TesseractBackend(command="tesseract-not-installed-anastylos-test")


def tmpdir(testcase) -> Path:
    directory = tempfile.TemporaryDirectory()
    testcase.addCleanup(directory.cleanup)
    return Path(directory.name)


def write_png(path: Path, image) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    path.write_bytes(buffer.tobytes())
    return path


def resistor_image(sequence=("brown", "black", "red", "gold"), **kwargs):
    from anastylos_resistor.synthetic import render
    return render(list(sequence), **kwargs)


def text_panel(text, size=(360, 140), body=(30, 30, 30)):
    image = np.full((size[1], size[0], 3), body, np.uint8)
    if text:
        cv2.putText(image, text, (20, size[1] // 2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 1.6,
                    (235, 235, 235), 3, cv2.LINE_AA)
    return image
