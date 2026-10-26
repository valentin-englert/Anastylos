#!/usr/bin/env python3
"""Generate ANASTYLOS calibration sheets. Run with the project virtualenv."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anastylos.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["generate"]))
