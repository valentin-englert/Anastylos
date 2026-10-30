"""Locate the adjacent Microscopy Core without adding a runtime dependency."""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_core_importable() -> None:
    core_source = Path(__file__).resolve().parents[3] / "src"
    if not (core_source / "anastylos_microscopy" / "capture.py").is_file():
        raise RuntimeError(
            "Microscopy Core source was not found beside the node; set PYTHONPATH "
            "to <repository>/anastylos-microscopy/src"
        )
    core_path = str(core_source)
    if core_path not in sys.path:
        sys.path.insert(0, core_path)
