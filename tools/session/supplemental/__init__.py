"""Read-only supplemental evidence adapter (no memory writes, no store imports)."""

from supplemental.adapter import build_view, load_memory_events

__all__ = ["build_view", "load_memory_events"]
