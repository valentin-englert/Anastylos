"""Provider-independent assistant adapter interface.

Future vision models (local or vendor) plug in by implementing
AssistantProvider. Assistants are advisory only: their notes ride
along on results and never change a verdict. Nothing here calls a
cloud API or downloads weights.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AssistantProvider(ABC):
    """Advise on an observation; return JSON-serializable notes or None."""

    name = "base"
    version_text = "0"

    def version(self) -> str:
        return self.version_text

    @abstractmethod
    def advise(self, observation):
        """Return notes for one observation dict, or None to stay silent."""
        raise NotImplementedError
