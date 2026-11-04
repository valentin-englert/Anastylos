"""Capture-manifest intake (anastylos-microscopy-capture-manifest/1).

A narrow, additive contract for evidence produced later by a capture
node (e.g. the planned Raspberry Pi 3B+ Microscopy Node: MUSTOOL HDMI ->
UGREEN 25854 -> MJPEG frames). The Core only reads a folder containing
JPEG files plus this manifest; it never talks to the node, never streams,
and does not depend on BlobCam. Concepts (byte-identical JPEG originals,
per-frame SHA-256, UTC timestamps, explicit orientation metadata) are
reimplemented independently.

Frame confinement (v0.1.1, QA-02)
---------------------------------
The capture root is the real path of the directory that contains the
manifest. Every frame reference must

1. be a relative, lexically clean path (no ``..``, no empty or ``.``
   components, no drive letter, UNC prefix, root, NUL/control characters,
   ``:`` (NTFS alternate data streams), Windows reserved device names or
   trailing dots/spaces), and
2. resolve - after following every symlink, Windows junction or mount point -
   to a regular file whose real path is inside the real capture root, and
3. not pass through a Windows reparse point other than a symlink or junction
   (those are resolved and checked by 2.; other tags, e.g. cloud placeholders,
   are rejected fail-closed), and
4. have exactly one hard link (a second name could live outside the root).

``read_frame`` opens the resolved file once (``O_NOFOLLOW`` where the OS has
it), verifies after opening that the open handle is the same file
(device + inode / NTFS file index) that the in-root path resolves to *now*,
reads the bytes once and returns them. The Core hashes, decodes and
preserves exactly those bytes, so a frame can never be validated as one file
and decoded as another. See LIMITATIONS.md for the remaining race window.
"""

from __future__ import annotations

import os
import re
import stat as stat_mod
from pathlib import Path, PurePosixPath, PureWindowsPath

from anastylos_microscopy import CAPTURE_MANIFEST_SCHEMA
from anastylos_microscopy.util import parse_utc, read_json

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ROTATIONS = (0, 90, 180, 270)
VALIDATION_CLASSES = ("synthetic", "sample", "real_microscope")


MAX_FRAME_BYTES = 512 * 1024 * 1024  # same bound as evidence.MAX_FILE_BYTES
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
                    "COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT\u00b9", "LPT\u00b2", "LPT\u00b3"}
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # directory junction / volume mount point
IO_REPARSE_TAG_SYMLINK = 0xA000000C
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class ManifestError(ValueError):
    pass


class ConfinementError(ManifestError):
    """A frame reference escapes, or cannot be proven to stay in, the capture root."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def safe_relative(path_text: str) -> PurePosixPath:
    """Normalize a manifest file reference; reject absolute/traversal paths."""
    if not isinstance(path_text, str) or not path_text.strip():
        raise ManifestError("frame file must be a non-empty string")
    if "\\" in path_text or re.match(r"^[A-Za-z]:", path_text):
        pure = PureWindowsPath(path_text)
        if pure.drive or pure.root:
            raise ManifestError(f"frame file must be relative, got {path_text!r}")
        rel = PurePosixPath(*pure.parts)
    else:
        rel = PurePosixPath(path_text)
        if rel.is_absolute():
            raise ManifestError(f"frame file must be relative, got {path_text!r}")
    if any(part in ("..", "") for part in rel.parts):
        raise ManifestError(f"frame file must not traverse directories: {path_text!r}")
    raw_parts = re.split(r"[\\/]", path_text)
    if any(part in (".", "..") for part in raw_parts):
        raise ManifestError(f"frame file must not traverse directories: {path_text!r}")
    for part in rel.parts:
        if any(ord(ch) < 32 for ch in part):
            raise ManifestError(f"frame file contains control characters: {path_text!r}")
        if ":" in part:
            raise ManifestError(f"frame file must not contain ':' (drive or NTFS stream): {path_text!r}")
        if part != part.rstrip(" ."):
            raise ManifestError(f"frame file component ends in dot/space (ambiguous on Windows): "
                                f"{path_text!r}")
        if part.split(".")[0].upper().rstrip(" ") in WINDOWS_RESERVED:
            raise ManifestError(f"frame file uses a Windows reserved device name: {path_text!r}")
    return rel


def _strip_extended(path_text: str) -> str:
    """Drop Windows extended-length prefixes so two spellings compare equal."""
    if path_text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_text[8:]
    if path_text.startswith("\\\\?\\") or path_text.startswith("\\\\.\\"):
        return path_text[4:]
    return path_text


def _cmp(path_text: str) -> str:
    return os.path.normcase(os.path.normpath(_strip_extended(path_text)))


def is_within(child: str, root: str) -> bool:
    """True when real path ``child`` is ``root`` or below it (same drive/share)."""
    child_c, root_c = _cmp(child), _cmp(root)
    try:
        return os.path.commonpath([child_c, root_c]) == root_c
    except ValueError:  # different drives / UNC shares on Windows
        return False


def capture_root(manifest_path: str | os.PathLike) -> str:
    """Authorized root: real path of the directory that holds the manifest."""
    return os.path.realpath(os.path.dirname(os.path.abspath(os.fspath(manifest_path))))


def _reparse_problem(path_text: str) -> str | None:
    """Windows only: reject reparse points other than symlinks and junctions."""
    try:
        info = os.lstat(path_text)
    except OSError:
        return None
    attributes = getattr(info, "st_file_attributes", 0) or 0
    if not attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        return None
    tag = getattr(info, "st_reparse_tag", 0) or 0
    if tag in (IO_REPARSE_TAG_SYMLINK, IO_REPARSE_TAG_MOUNT_POINT):
        return None
    return f"unsupported Windows reparse point (tag 0x{tag:08X}) at {path_text!r}"


def _links_on_path(root: str, rel: PurePosixPath) -> list[dict]:
    """Report every symlink/junction between the root and the frame (provenance)."""
    links, current = [], root
    for part in rel.parts:
        current = os.path.join(current, part)
        problem = _reparse_problem(current)
        if problem:
            raise ConfinementError("unsupported_reparse_point", problem)
        junction = bool(getattr(os.path, "isjunction", lambda _p: False)(current))
        if os.path.islink(current) or junction:
            links.append({"component": os.path.relpath(current, root).replace(os.sep, "/"),
                          "kind": "junction" if junction else "symlink"})
    return links


def resolve_frame(root: str, rel: PurePosixPath) -> dict:
    """Resolve one lexically safe frame reference and prove it stays in ``root``.

    Raises ConfinementError; never reads file content.
    """
    lexical = os.path.join(root, *rel.parts)
    links = _links_on_path(root, rel)
    try:
        real = os.path.realpath(lexical, strict=True)
    except (OSError, RuntimeError) as exc:  # missing, broken link, loop
        if os.path.lexists(lexical) or links:
            raise ConfinementError("unresolvable_link",
                                   f"frame {str(rel)!r} cannot be resolved (broken link or "
                                   f"loop?): {exc}") from exc
        raise ConfinementError("frame_missing", f"frame {str(rel)!r} not found") from exc
    if not is_within(real, root):
        raise ConfinementError("outside_capture_root",
                               f"frame {str(rel)!r} resolves outside the capture root "
                               f"(via {', '.join(l['kind'] + ' ' + l['component'] for l in links) or 'path'})")
    info = os.stat(real)
    if not stat_mod.S_ISREG(info.st_mode):
        raise ConfinementError("not_a_regular_file", f"frame {str(rel)!r} is not a regular file")
    if info.st_nlink > 1:
        raise ConfinementError("multiple_hard_links",
                               f"frame {str(rel)!r} has {info.st_nlink} hard links; another name could "
                               "live outside the capture root (fail-closed)")
    return {"relative": str(rel), "lexical_path": lexical, "real_path": real, "links": links,
            "identity": [info.st_dev, info.st_ino], "size_bytes": info.st_size}


def read_frame(root: str, rel: PurePosixPath, max_bytes: int = MAX_FRAME_BYTES) -> tuple[bytes, dict]:
    """Confined single read of one frame: (bytes, confinement report)."""
    resolved = resolve_frame(root, rel)
    if resolved["size_bytes"] > max_bytes:
        raise ConfinementError("file_too_large", f"frame {str(rel)!r} exceeds {max_bytes} bytes")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) \
        | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(resolved["real_path"], flags)
    except OSError as exc:
        raise ConfinementError("open_failed", f"frame {str(rel)!r} could not be opened "
                                              f"without following links: {exc}") from exc
    try:
        opened = os.fstat(fd)
        # Re-resolve AFTER opening: the open handle must be the very file the in-root
        # path names now. A swapped directory/link between check and open fails here.
        try:
            again = os.path.realpath(resolved["lexical_path"], strict=True)
            now = os.stat(again)
        except OSError as exc:
            raise ConfinementError("changed_during_read", f"frame {str(rel)!r} vanished during "
                                                          f"read: {exc}") from exc
        identity_known = bool(opened.st_ino)
        same = (opened.st_dev, opened.st_ino) == (now.st_dev, now.st_ino) if identity_known \
            else _cmp(again) == _cmp(resolved["real_path"])
        if not (same and is_within(again, root) and stat_mod.S_ISREG(opened.st_mode)):
            raise ConfinementError("changed_during_read",
                                   f"frame {str(rel)!r} changed between check and open (fail-closed)")
        if opened.st_nlink > 1:
            raise ConfinementError("multiple_hard_links",
                                   f"frame {str(rel)!r} gained hard links during read (fail-closed)")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ConfinementError("file_too_large", f"frame {str(rel)!r} exceeds {max_bytes} bytes")
            chunks.append(chunk)
    finally:
        os.close(fd)
    report = {"policy": "anastylos-microscopy-capture-confinement/1", "capture_root": root,
              "relative": resolved["relative"], "real_path": resolved["real_path"],
              "links_followed": resolved["links"],
              "identity_check": "device+inode" if identity_known else "path_only (fs has no file id)",
              "single_read": True}
    return b"".join(chunks), report


def load(path: str | Path) -> dict:
    path = Path(path)
    try:
        doc = read_json(path)
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read capture manifest {path}: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != CAPTURE_MANIFEST_SCHEMA:
        raise ManifestError(f"capture manifest must declare schema {CAPTURE_MANIFEST_SCHEMA!r}")
    source = doc.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("camera_id"), str):
        raise ManifestError("manifest.source.camera_id is required")
    vclass = doc.get("validation_class")
    if vclass not in VALIDATION_CLASSES:
        raise ManifestError(f"manifest.validation_class must be one of {VALIDATION_CLASSES}")
    frames = doc.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ManifestError("manifest.frames must be a non-empty list")
    base = path.parent
    root = capture_root(path)
    entries, seen = [], set()
    for index, frame in enumerate(frames):
        problems = []
        if not isinstance(frame, dict):
            entries.append({"index": index, "problems": ["frame entry must be an object"]})
            continue
        try:
            rel = safe_relative(frame.get("file"))
        except ManifestError as exc:
            entries.append({"index": index, "file": frame.get("file"), "problems": [str(exc)]})
            continue
        sha = frame.get("sha256")
        if not isinstance(sha, str) or not SHA_RE.match(sha):
            problems.append("sha256 must be 64 lowercase hex characters")
        captured = frame.get("captured_utc")
        if captured is not None:
            try:
                parse_utc(captured)
            except ValueError as exc:
                problems.append(str(exc))
        rotation = frame.get("orientation_cw_deg", doc.get("orientation_cw_deg"))
        if rotation is not None and rotation not in ROTATIONS:
            problems.append(f"orientation_cw_deg must be one of {ROTATIONS}")
        roi = frame.get("roi_xywh")
        if str(rel) in seen:
            problems.append("duplicate frame file")
        seen.add(str(rel))
        confinement = None
        try:
            resolved = resolve_frame(root, rel)
            confinement = {"real_path": resolved["real_path"], "links": resolved["links"]}
        except ConfinementError as exc:
            entries.append({"index": index, "file": str(rel), "problems": [f"{exc.code}: {exc}"],
                            "rejection": exc.code})
            continue
        entries.append({"index": index, "file": str(rel), "path": str(base.joinpath(*rel.parts)),
                        "sha256": sha, "captured_utc": captured, "seq": frame.get("seq"),
                        "orientation_cw_deg": rotation, "roi_xywh": roi,
                        "component_observation_id": frame.get("component_observation_id"),
                        "rel": rel, "confinement": confinement, "problems": problems})
    return {"document": doc, "path": str(path), "capture_root": root, "entries": entries,
            "calibration": doc.get("calibration"), "source": source,
            "validation_class": vclass, "node_id": doc.get("node_id"),
            "session_id": doc.get("session_id")}


def verify(manifest_path: str | os.PathLike) -> list[str]:
    """Deep validation used by ``validate --kind capture_manifest``.

    Applies exactly the checks ``inspect --manifest`` applies (lexical rules,
    confinement, single confined read) and compares each frame's SHA-256.
    """
    from anastylos_microscopy.util import sha256_bytes

    manifest = load(manifest_path)
    errors = []
    for entry in manifest["entries"]:
        label = f"frames[{entry['index']}] {entry.get('file')!r}"
        if entry.get("problems"):
            errors += [f"{label}: {p}" for p in entry["problems"]]
            continue
        try:
            data, _ = read_frame(manifest["capture_root"], entry["rel"])
        except ConfinementError as exc:
            errors.append(f"{label}: {exc.code}: {exc}")
            continue
        if sha256_bytes(data) != entry["sha256"]:
            errors.append(f"{label}: sha256 mismatch (file {sha256_bytes(data)[:16]}..., "
                          f"manifest {entry['sha256'][:16]}...)")
    return errors


def capture_context(manifest: dict, entry: dict, manifest_sha256: str) -> dict:
    source = manifest["source"]
    return {
        "origin": "capture_manifest",
        "camera_id": source.get("camera_id"),
        "node_id": manifest.get("node_id"),
        "optical_profile": source.get("optical_profile"),
        "captured_utc": entry.get("captured_utc"),
        "orientation_cw_deg": entry.get("orientation_cw_deg"),
        "orientation_source": "capture_manifest",
        "roi_source": "capture_manifest",
        "validation_class": manifest["validation_class"],
        "manifest": {"path": manifest["path"], "sha256": manifest_sha256,
                     "frame_index": entry["index"], "frame_file": entry.get("file"),
                     "seq": entry.get("seq"), "declared_sha256": entry.get("sha256"),
                     "capture_root": manifest.get("capture_root")},
    }
