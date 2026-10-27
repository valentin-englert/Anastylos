#!/usr/bin/env python3
"""ANASTYLOS cable routing and assembly order. Use the calibration virtualenv."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "assembly-memory"))

from anastylos_cable.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
