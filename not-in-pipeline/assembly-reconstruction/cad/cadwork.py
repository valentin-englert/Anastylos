#!/usr/bin/env python3
"""ANASTYLOS trusted CAD worker entry. Kernel commands need the worker venv:

  .venv/Scripts/python cadwork.py run --spec <model.json> --out <dir>

`validate` is standard library only and runs on any Python 3.12.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anastylos_cad.worker import main

if __name__ == "__main__":
    raise SystemExit(main())
