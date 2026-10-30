"""Start the live worker using the existing virtual environment's packages."""

import os
import site
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
site.addsitedir(str(root / ".venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(root / "anastylos-microscopy" / "src"))
sys.path.insert(0, str(root / "anastylos-microscopy" / "node" / "src"))
os.environ["ANASTYLOS_ROOT"] = str(root)

from anastylos_mic_node.live_analysis import main

main()
