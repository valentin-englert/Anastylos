"""Provider-agnostic embedding adapter interface.

Future vision models (local or vendor) plug in by implementing
EmbeddingProvider. Nothing here calls a cloud API or downloads
weights; providers run wherever the caller runs them, and their
vectors are stored namespaced with a version so mismatched
generations never compare.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Embed a BGR crop into a fixed-length vector, or decline (None)."""

    name = "base"
    version_text = "0"

    def version(self) -> str:
        return self.version_text

    @abstractmethod
    def embed(self, crop_bgr):
        """Return a 1-D sequence of floats, or None when not applicable."""
        raise NotImplementedError
