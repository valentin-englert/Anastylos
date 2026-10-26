#!/usr/bin/env python3
"""ANASTYLOS screw head recognition. Use the calibration virtualenv."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anastylos_screw.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
