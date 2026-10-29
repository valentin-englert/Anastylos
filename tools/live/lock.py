"""Exclusive single-writer lock per memory session dir (CONTRACT §3.5).

An OS byte-range lock on ``<session dir>/live-coordinator.lock``, held through an open handle for the
coordinator's lifetime. The operating system drops it when the process dies, so a crashed
coordinator never leaves a stale lock and no pid heuristic is needed. The lock excludes other live
coordinators only: ``session.py``, ``answers.py apply`` and manual ``memory.py`` calls do not honour it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LOCK_NAME = "live-coordinator.lock"
_LOCK_OFFSET = 1 << 20  # lock a byte far past the holder note so a rejected coordinator can still read who holds it

if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class LockHeld(RuntimeError):
    """Another live coordinator holds this memory session dir."""


class SessionLock:
    def __init__(self, session_dir: Path):
        self.path = Path(session_dir) / LOCK_NAME
        self._fd: int | None = None

    def acquire(self, holder: dict) -> None:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if not _try_lock(fd):
            os.close(fd)
            raise LockHeld(f"{self.path} is held by another live coordinator: {self.holder()}")
        note = (json.dumps({**holder, "pid": os.getpid()}, ensure_ascii=False) + "\n").encode("utf-8")
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, note)
        os.fsync(fd)
        self._fd = fd

    def holder(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip() or "unknown"
        except OSError as exc:
            return f"unreadable ({exc})"

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        """Normal shutdown. (A crash needs no call: the OS releases the lock with the handle.)"""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def drop_handle_like_a_crash(self) -> None:
        """Close the handle without unlocking first, as process death does (tests only)."""
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)
