"""Dependency-injection wiring for the TL vertical slices and the real L2 arm (CONTRACT §3.1).

The only place under `tools/live/integration/` that names `providers/`. Factories are given to
`l2sim/run.py --proposer integration.wiring:<factory>` with `PYTHONPATH=tools/live;tools/live/providers`.
"""

from __future__ import annotations

import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parents[1]
for _path in (LIVE / "providers", LIVE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from meta import MetaProposer  # noqa: E402  (path set above)
from provider_core import build_request  # noqa: E402


def meta_proposer(run_dir, call_cap):
    """The real Muse proposer for one run; its own attempt cap equals the caller's call cap."""
    return MetaProposer(Path(run_dir) / "provider", max_calls=call_cap)


class PayloadCheckedProposer:
    """Wraps any proposer and fails loudly if a payload would be refused by the real provider."""

    def __init__(self, inner):
        self._inner = inner
        self.checked = 0

    def propose(self, task, payload, request_id):
        build_request(task, payload)  # raises ValueError for a payload outside the task input schema
        self.checked += 1
        return self._inner.propose(task, payload, request_id)


def checked_good_fake():
    """l2sim's good fake behind the real provider's payload check: a dry run before spending calls."""
    import fakes  # l2sim's module; run.py puts l2sim on sys.path
    return PayloadCheckedProposer(fakes.make("good"))
