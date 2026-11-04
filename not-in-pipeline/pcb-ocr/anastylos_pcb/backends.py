"""Pluggable OCR backends. No weights are downloaded; engines must be installed."""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2


class OcrBackend:
    name = "base"

    def available(self) -> bool:
        raise NotImplementedError

    def version(self) -> str | None:
        raise NotImplementedError

    def recognize(self, image) -> list[dict]:
        """Return words: [{text, confidence (0-100|None), box_xywh}]."""
        raise NotImplementedError


class TesseractBackend(OcrBackend):
    """Tesseract OCR via its command-line binary (external dependency).

    Uses the locally installed binary and tessdata only; nothing is
    downloaded. Word boxes and confidences come from TSV output.
    """

    name = "tesseract"

    def __init__(self, command: str = "tesseract", psm: int = 6):
        self.command = command
        self.psm = psm

    def available(self) -> bool:
        return shutil.which(self.command) is not None

    def version(self) -> str | None:
        if not self.available():
            return None
        try:
            proc = subprocess.run([self.command, "--version"], capture_output=True,
                                  text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        first = (proc.stdout or proc.stderr).strip().splitlines()
        return first[0].strip() if first else "tesseract (version unknown)"

    def recognize(self, image) -> list[dict]:
        if not self.available():
            raise RuntimeError("tesseract binary is not installed")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page.png"
            cv2.imwrite(str(path), image)
            try:
                proc = subprocess.run(
                    [self.command, str(path), "stdout", "--psm", str(self.psm), "tsv"],
                    capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError(f"tesseract failed: {exc}") from exc
        if proc.returncode != 0:
            raise RuntimeError(f"tesseract failed: {proc.stderr.strip()[-300:]}")
        return _parse_tsv(proc.stdout)


def _parse_tsv(payload: str) -> list[dict]:
    words = []
    reader = csv.DictReader(io.StringIO(payload), delimiter="\t")
    for row in reader:
        try:
            if row.get("level") != "5" or not (row.get("text") or "").strip():
                continue
            words.append({
                "text": row["text"].strip(),
                "confidence": float(row["conf"]),
                "box_xywh": [int(row["left"]), int(row["top"]),
                             int(row["width"]), int(row["height"])],
            })
        except (KeyError, ValueError, TypeError):
            continue
    return words


BACKENDS = {"tesseract": TesseractBackend}


def get_backend(name: str, **kwargs) -> OcrBackend:
    try:
        return BACKENDS[name](**kwargs)
    except KeyError:
        raise ValueError(f"unknown OCR backend {name!r}; available: {sorted(BACKENDS)}") from None
    except TypeError as exc:
        raise ValueError(f"backend {name!r} rejects options: {exc}") from exc
