"""Handles to the existing, frozen memory contracts the live core reuses unchanged.

``tools/session/answers.py`` is imported (plan only; its writes are re-issued step by step by
``memory_writer``) and ``assembly-memory/memory.py`` is run as a subprocess. Neither file is
edited from here (E3-B freeze, docs/PROTO-01-E3B.md §2).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MEMORY_CLI = REPO / "assembly-memory" / "memory.py"

sys.path.insert(0, str(REPO / "tools" / "session"))
sys.path.insert(0, str(REPO / "assembly-memory"))
import answers  # noqa: E402,F401  (frozen production module, read by import only)
from anastylos_memory.store import CorruptLogError, load_events  # noqa: E402,F401
