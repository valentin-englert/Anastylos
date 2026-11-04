#!/usr/bin/env python3
"""ANASTYLOS polarity and orientation recognition. Use the calibration virtualenv."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anastylos_polarity.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
