"""Small deterministic helpers: canonical JSON, hashing, atomic writes, time."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def parse_utc(text: str) -> str:
    """Validate an ISO-8601 UTC stamp ending in Z; return it unchanged."""
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError(f"timestamp must be ISO-8601 UTC ending in 'Z', got {text!r}")
    try:
        datetime.fromisoformat(text[:-1])
    except ValueError as exc:
        raise ValueError(f"invalid timestamp {text!r}: {exc}") from exc
    return text


def seconds_clock(stamp: str) -> str:
    """Reader-style second-resolution stamp (YYYY-MM-DDTHH:MM:SSZ)."""
    return stamp[:19] + "Z"


def _clean(value):
    """Make values JSON-safe and deterministic (NaN/inf -> None, tuples -> lists)."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item") and callable(getattr(value, "item")):  # numpy scalars
        try:
            return _clean(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "tolist"):
        return _clean(value.tolist())
    return value


def json_safe(value):
    return _clean(value)


def canonical_json(value) -> bytes:
    return json.dumps(_clean(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pretty_json(value) -> str:
    return json.dumps(_clean(value), indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False) + "\n"


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str | Path, value) -> str:
    """Write pretty JSON atomically; return the SHA-256 of the written bytes."""
    data = pretty_json(value).encode("utf-8")
    atomic_write_bytes(path, data)
    return sha256_bytes(data)


def read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
