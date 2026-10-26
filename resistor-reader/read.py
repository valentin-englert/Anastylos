#!/usr/bin/env python3
"""ANASTYLOS resistor color-code reader. Use the calibration virtualenv."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anastylos_resistor.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
