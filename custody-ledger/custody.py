#!/usr/bin/env python3
"""ANASTYLOS custody ledger. Use the calibration virtualenv."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "change-detection"))

from anastylos_custody.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
