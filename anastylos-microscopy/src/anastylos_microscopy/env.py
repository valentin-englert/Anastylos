"""Locate the existing ANASTYLOS reader modules and fingerprint their code.

The Core never vendors or rewrites reader code. It needs a checkout of
`valentin-englert/Anastylos` (pinned revision in `PINNED_ANASTYLOS_REVISION`)
and finds it through, in order: an explicit argument, the `ANASTYLOS_ROOT`
environment variable. Each reader module directory is put on `sys.path`
exactly as the module's own CLI wrapper does (`read.py`, `smd.py`, ...).

Revision detection only *reads* `.git/HEAD` / refs files when present; no
git command is ever executed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from anastylos_microscopy import PINNED_ANASTYLOS_REVISION

# reader key -> (module directory, python package, entry script, kind)
READER_MODULES: dict[str, tuple[str, str, str]] = {
    "resistor": ("resistor-reader", "anastylos_resistor", "read.py"),
    "smd": ("smd-reader", "anastylos_smd", "smd.py"),
    "pcb_ocr": ("pcb-ocr", "anastylos_pcb", "ocr.py"),
    "markings": ("component-markings", "anastylos_markings", "markings.py"),
    "polarity": ("polarity-reader", "anastylos_polarity", "polarity.py"),
    "screw": ("screw-reader", "anastylos_screw", "screw.py"),
    "screw_measure": ("screw-measurement", "anastylos_screw_measure", "measure.py"),
    "washer": ("washer-spacer", "anastylos_washer", "washer.py"),
    "fingerprints": ("component-fingerprints", "anastylos_fingerprints", "fingerprints.py"),
    "connectors": ("connector-matching", "anastylos_connectors", "connectors.py"),
}

# Deliberately NOT microscopy readers (Assembly Observation/Verification):
NON_MICROSCOPY_MODULES = ("tray-counter", "missing-parts")

PINNED_HASHES_FILE = Path(__file__).with_name("pinned_reader_hashes.json")


class ReaderEnvironmentError(RuntimeError):
    pass


def resolve_root(explicit: str | os.PathLike | None = None) -> Path:
    raw = explicit if explicit is not None else os.environ.get("ANASTYLOS_ROOT")
    if not raw:
        raise ReaderEnvironmentError(
            "ANASTYLOS_ROOT is not set; point it at a checkout of valentin-englert/Anastylos "
            f"(tested revision {PINNED_ANASTYLOS_REVISION[:12]})")
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise ReaderEnvironmentError(f"ANASTYLOS_ROOT {str(root)!r} is not a directory")
    return root.resolve()


def module_dir(root: Path, key: str) -> Path:
    return root / READER_MODULES[key][0]


def activate(root: Path) -> list[str]:
    """Prepend every present reader module directory to sys.path (idempotent)."""
    added = []
    for key in READER_MODULES:
        directory = module_dir(root, key)
        if directory.is_dir() and str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
            added.append(str(directory))
    return added


def code_sha256(package_dir: Path) -> str | None:
    """Hash of the package's .py files, line endings normalized to LF.

    Normalization keeps the value identical across a Windows (CRLF) and a
    Linux (LF) checkout of the same commit.
    """
    if not package_dir.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(package_dir).as_posix()
        data = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()


def detect_revision(root: Path) -> dict:
    """Best-effort read-only revision detection (no git process)."""
    declared = os.environ.get("ANASTYLOS_REVISION")
    detected = None
    git = root / ".git"
    try:
        if git.is_file():  # worktree pointer: "gitdir: <path>"
            text = git.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                git = (root / text.split(":", 1)[1].strip()).resolve()
        head_file = git / "HEAD"
        if head_file.is_file():
            head = head_file.read_text(encoding="utf-8").strip()
            if head.startswith("ref:"):
                ref = head.split(":", 1)[1].strip()
                for base in (git, git.parent.parent if git.parent.name == "worktrees" else git):
                    ref_file = base / ref
                    if ref_file.is_file():
                        detected = ref_file.read_text(encoding="utf-8").strip()
                        break
                if detected is None:
                    packed = git / "packed-refs"
                    if packed.is_file():
                        for line in packed.read_text(encoding="utf-8").splitlines():
                            parts = line.split()
                            if len(parts) == 2 and parts[1] == ref:
                                detected = parts[0]
            elif len(head) == 40:
                detected = head
    except OSError:
        detected = None
    return {"detected_git_head": detected, "declared": declared,
            "pinned": PINNED_ANASTYLOS_REVISION}


def load_pinned_hashes() -> dict:
    try:
        return json.loads(PINNED_HASHES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def reader_identity(root: Path, key: str, pinned: dict | None = None) -> dict:
    directory, package, entry = READER_MODULES[key]
    pinned = load_pinned_hashes() if pinned is None else pinned
    sha = code_sha256(root / directory / package)
    expected = (pinned.get("readers") or {}).get(key)
    return {
        "reader": key,
        "module": directory,
        "package": package,
        "entry_point": f"{directory}/{entry}",
        "present": sha is not None,
        "code_sha256": sha,
        "pinned_code_sha256": expected,
        "matches_pinned_revision": (sha == expected) if (sha and expected) else None,
    }


def environment_report(root: Path) -> dict:
    pinned = load_pinned_hashes()
    return {
        "anastylos_root": str(root),
        "revision": detect_revision(root),
        "readers": [reader_identity(root, key, pinned) for key in READER_MODULES],
        "excluded_modules": list(NON_MICROSCOPY_MODULES),
    }
