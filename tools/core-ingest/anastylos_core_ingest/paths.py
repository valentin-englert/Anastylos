"""Path safety helpers. Never trust package-supplied paths."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def is_uuid(value: object) -> bool:
    return isinstance(value, str) and UUID_RE.fullmatch(value) is not None


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def unsafe_rel_path_reason(path: str) -> str | None:
    """Reject absolute, drive-letter, backslash, dot-dot, and control paths."""
    if not isinstance(path, str) or not path or path != path.strip():
        return "empty or padded"
    if "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return "absolute or backslash path"
    if any(ord(ch) < 32 for ch in path):
        return "control character"
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return "dot-segment or empty"
    return None


def safe_join(root: Path, rel: str) -> Path:
    reason = unsafe_rel_path_reason(rel)
    if reason is not None:
        raise ValueError(f"unsafe path {rel!r}: {reason}")
    candidate = (root / PurePosixPath(rel)).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes root: {rel!r}") from exc
    return candidate


def blob_relpath(sha256: str) -> str:
    if not is_sha256(sha256):
        raise ValueError("blob key must be lowercase sha256 hex")
    return f"blobs/sha256/{sha256[:2]}/{sha256}"
