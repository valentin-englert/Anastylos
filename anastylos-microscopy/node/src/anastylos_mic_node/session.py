"""Create and verify capture sessions using the Microscopy Core contract."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from anastylos_mic_node.core_access import ensure_core_importable

MAX_FRAME_BYTES = 64 * 1024 * 1024
MAX_SESSION_BYTES = 512 * 1024 * 1024
MAX_FRAMES = 32
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


class SessionError(ValueError):
    """Invalid input, unsafe session or incomplete capture evidence."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _captured_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_jpeg(data: bytes) -> None:
    if len(data) < 4 or not data.startswith(JPEG_SOI) or not data.endswith(JPEG_EOI):
        raise SessionError("frame is not a complete JPEG stream (SOI/EOI markers missing)")


def read_jpeg(path: str | os.PathLike[str], *, limit: int = MAX_FRAME_BYTES) -> bytes:
    source = Path(path)
    try:
        info = source.stat()
        if not stat.S_ISREG(info.st_mode):
            raise SessionError(f"input is not a regular file: {source}")
        if info.st_size > limit:
            raise SessionError(f"input exceeds the {limit}-byte allowed limit: {source}")
        with source.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as exc:
        raise SessionError(f"cannot read input frame {source}: {exc}") from exc
    if len(data) > limit:
        raise SessionError(f"input exceeds the {limit}-byte allowed limit: {source}")
    validate_jpeg(data)
    return data


def _write_new_file(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(directory: Path, name: str, data: bytes) -> None:
    temporary = directory / f".{name}.{uuid.uuid4().hex}.part"
    destination = directory / name
    try:
        _write_new_file(temporary, data)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _safe_label(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise SessionError(f"{label} must be a non-empty string of at most 160 characters")
    if any(ord(char) < 32 for char in value):
        raise SessionError(f"{label} must not contain control characters")
    return value.strip()


def _new_session(output_root: str | os.PathLike[str], session_id: str | None) -> tuple[Path, str]:
    root = Path(output_root)
    created_session = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise SessionError(f"output root is not a directory: {root}")
        if session_id is None:
            session_id = f"mic-{_timestamp(utc_now())}-{uuid.uuid4().hex[:12]}"
        session_id = _safe_label(session_id, "session_id")
        if session_id in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", session_id):
            raise SessionError("session_id must use only letters, numbers, dot, underscore or hyphen")
        session = root / session_id
        session.mkdir(mode=0o700, exist_ok=False)
        created_session = session
        frames = session / "frames"
        frames.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise SessionError(f"session already exists; refusing to overwrite: {session_id}") from exc
    except OSError as exc:
        retained = (f"; incomplete directory remains at {created_session} without a manifest"
                    if created_session is not None else "")
        raise SessionError(f"cannot create capture session under {root}: {exc}{retained}") from exc
    return session, session_id


def _publish_manifest(session: Path, manifest: dict) -> Path:
    ensure_core_importable()
    from anastylos_microscopy.schema import validate_named

    errors = validate_named(manifest, "capture_manifest")
    if errors:
        raise SessionError("generated manifest does not satisfy the Core schema: " + "; ".join(errors))
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write(session, "capture-manifest.json", payload)
    return session / "capture-manifest.json"


def create_session(
    *,
    output_root: str | os.PathLike[str],
    camera_id: str,
    validation_class: str,
    frames: Iterable[tuple[bytes, datetime | None]],
    capture_device: str | None = None,
    capture_format: str | None = None,
    capture_resolution: list[int] | None = None,
    optical_profile: str | None = None,
    node_id: str | None = None,
    camera_settings_note: str | None = None,
    session_id: str | None = None,
) -> Path:
    """Publish a manifest last, after every exact JPEG payload is durable."""
    ensure_core_importable()
    from anastylos_microscopy import CAPTURE_MANIFEST_SCHEMA

    camera_id = _safe_label(camera_id, "camera_id")
    if validation_class not in {"synthetic", "sample", "real_microscope"}:
        raise SessionError("validation_class must be synthetic, sample or real_microscope")
    if optical_profile is not None:
        optical_profile = _safe_label(optical_profile, "optical_profile")
    if node_id is not None:
        node_id = _safe_label(node_id, "node_id")
    frames = list(itertools.islice(frames, MAX_FRAMES + 1))
    if not frames or len(frames) > MAX_FRAMES:
        raise SessionError(f"a session must contain between 1 and {MAX_FRAMES} frames")

    checked: list[tuple[bytes, datetime | None]] = []
    total = 0
    for data, captured in frames:
        if not isinstance(data, bytes):
            raise SessionError("frame backend must return bytes")
        validate_jpeg(data)
        if len(data) > MAX_FRAME_BYTES:
            raise SessionError(f"frame exceeds the {MAX_FRAME_BYTES}-byte frame limit")
        total += len(data)
        if total > MAX_SESSION_BYTES:
            raise SessionError(f"session exceeds the {MAX_SESSION_BYTES}-byte aggregate limit")
        if captured is not None and captured.tzinfo is None:
            raise SessionError("capture timestamps must be timezone-aware")
        checked.append((data, captured))

    session, session_id = _new_session(output_root, session_id)
    try:
        manifest_frames = []
        for sequence, (data, captured) in enumerate(checked, start=1):
            stamp = f"_{_timestamp(captured)}" if captured is not None else ""
            filename = f"frame_{sequence:06d}{stamp}.jpg"
            _atomic_write(session / "frames", filename, data)
            manifest_frames.append({
                "file": f"frames/{filename}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "seq": sequence,
                "captured_utc": _captured_utc(captured) if captured is not None else None,
                "orientation_cw_deg": None,
            })

        source = {
            "camera_id": camera_id,
            "optical_profile": optical_profile,
            "capture_device": capture_device,
            "capture_format": capture_format,
            "capture_resolution": capture_resolution,
            "camera_settings_note": camera_settings_note,
        }
        manifest = {
            "schema": CAPTURE_MANIFEST_SCHEMA,
            "node_id": node_id,
            "session_id": session_id,
            "validation_class": validation_class,
            "source": source,
            "orientation_cw_deg": None,
            "calibration": None,
            "frames": manifest_frames,
        }
        return _publish_manifest(session, manifest)
    except Exception as exc:
        if isinstance(exc, SessionError):
            raise SessionError(f"{exc}; incomplete session retained at {session}, without a manifest") from exc
        raise SessionError(f"session creation failed; incomplete session retained at {session}, "
                           f"without a manifest: {exc}") from exc


def package_files(
    input_paths: Iterable[str | os.PathLike[str]],
    *,
    output_root: str | os.PathLike[str],
    camera_id: str,
    validation_class: str,
    node_id: str | None = None,
    optical_profile: str | None = None,
    session_id: str | None = None,
) -> Path:
    paths = list(input_paths)
    if not paths:
        raise SessionError("package requires at least one --input frame")
    if len(paths) > MAX_FRAMES:
        raise SessionError(f"package supports at most {MAX_FRAMES} frames")
    frames = []
    total = 0
    for path in paths:
        limit = min(MAX_FRAME_BYTES, MAX_SESSION_BYTES - total)
        data = read_jpeg(path, limit=limit)
        total += len(data)
        frames.append((data, None))
    return create_session(
        output_root=output_root,
        camera_id=camera_id,
        validation_class=validation_class,
        frames=frames,
        node_id=node_id,
        optical_profile=optical_profile,
        session_id=session_id,
    )


def verify_manifest(manifest_path: str | os.PathLike[str]) -> dict:
    ensure_core_importable()
    from anastylos_microscopy import capture
    from anastylos_microscopy.schema import validate_named
    from anastylos_microscopy.util import read_json

    path = Path(manifest_path)
    try:
        manifest = read_json(path)
    except (OSError, ValueError) as exc:
        raise SessionError(f"cannot read manifest {path}: {exc}") from exc
    schema_errors = validate_named(manifest, "capture_manifest")
    if schema_errors:
        raise SessionError("manifest schema validation failed: " + "; ".join(schema_errors))
    errors = capture.verify(path)
    if errors:
        raise SessionError("Core capture verification failed: " + "; ".join(errors))
    loaded = capture.load(path)
    seen_sequences = set()
    total = 0
    for entry in loaded["entries"]:
        data, _ = capture.read_frame(loaded["capture_root"], entry["rel"])
        validate_jpeg(data)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise SessionError(f"frame hash changed during verification: {entry['file']}")
        total += len(data)
        if total > MAX_SESSION_BYTES:
            raise SessionError(f"session exceeds the {MAX_SESSION_BYTES}-byte aggregate limit")
        sequence = entry.get("seq")
        if sequence is not None:
            if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1 or sequence in seen_sequences:
                raise SessionError("manifest sequence numbers must be unique positive integers")
            seen_sequences.add(sequence)
    return {
        "manifest": str(path),
        "frames": len(loaded["entries"]),
        "total_bytes": total,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "schema": manifest["schema"],
        "validation_class": manifest["validation_class"],
    }
