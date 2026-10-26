#!/usr/bin/env python3
"""ANASTYLOS compliance checker. Standard library only.

Compares pinned requirements against the SPDX inventory, verifies required
attribution files exist, and lists open review items. Run from anywhere:

    py -3.12 tools/compliance/check.py

Exit 0: consistent (review items may still be listed). Exit 1: errors.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPDX_PATH = ROOT / "tools" / "compliance" / "spdx.json"
REQUIREMENTS = [ROOT / "calibration" / "requirements.txt"]
REQUIRED_FILES = [
    ROOT / "THIRD_PARTY_NOTICES.md",
    ROOT / "pcb-ocr" / "THIRD_PARTY.md",
    SPDX_PATH,
]
IGNORED_DISTS = {"pip", "setuptools", "wheel"}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^;\s]+)", line)
        if not match:
            raise ValueError(f"{path}:{lineno}: not a pinned requirement: {line!r}")
        pins[normalize(match.group(1))] = match.group(2)
    return pins


def load_inventory(path: Path = SPDX_PATH) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read inventory {path}: {exc}") from exc
    if not str(document.get("spdxVersion", "")).startswith("SPDX-2."):
        raise ValueError(f"{path}: not an SPDX 2.x document")
    packages = document.get("packages") or []
    if not packages:
        raise ValueError(f"{path}: inventory has no packages")
    seen: set[str] = set()
    for package in packages:
        for key in ("SPDXID", "name", "versionInfo", "licenseConcluded"):
            if key not in package:
                raise ValueError(f"{path}: package {package.get('name', '?')} misses {key!r}")
        if package["SPDXID"] in seen:
            raise ValueError(f"{path}: duplicate SPDXID {package['SPDXID']}")
        seen.add(package["SPDXID"])
    return document


def pypi_purls(document: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    for package in document["packages"]:
        for ref in package.get("externalRefs", []):
            locator = ref.get("referenceLocator", "")
            if ref.get("referenceType") == "purl" and locator.startswith("pkg:pypi/"):
                match = re.fullmatch(r"pkg:pypi/([^@]+)@([^?#]+)", locator)
                if match:
                    found[normalize(match.group(1))] = match.group(2)
    return found


def check_requirements(pins: dict[str, str], purls: dict[str, str]) -> list[str]:
    errors = []
    for name, version in sorted(pins.items()):
        if name not in purls:
            errors.append(f"undocumented dependency: {name}=={version} (add to spdx.json)")
        elif purls[name] != version:
            errors.append(f"version drift: {name} pinned {version} but inventoried {purls[name]}")
    for name in sorted(purls):
        if name not in pins:
            errors.append(f"stale inventory entry: {name}=={purls[name]} is no longer pinned")
    return errors


def check_files(files: list[Path] | None = None) -> list[str]:
    return [f"missing required file: {path}" for path in (files or REQUIRED_FILES) if not path.is_file()]


def collect_reviews(document: dict) -> list[str]:
    reviews = []
    for package in document["packages"]:
        for note in package.get("annotations", []):
            comment = note.get("comment", "")
            if comment.startswith("REVIEW:"):
                reviews.append(f"{package['name']} ({package['SPDXID']}): {comment}")
    return reviews


def _venv_site_packages(root: Path = ROOT) -> Path | None:
    candidates = [root / ".venv" / "Lib" / "site-packages",
                  *sorted((root / ".venv" / "lib").glob("python3*"))] if (root / ".venv").is_dir() else []
    for base in candidates:
        site = base if base.name == "site-packages" else base / "site-packages"
        if site.is_dir():
            return site
    return None


def check_venv(pins: dict[str, str], root: Path = ROOT) -> tuple[list[str], list[str]]:
    site = _venv_site_packages(root)
    if site is None:
        return [], ["no .venv found; installed-environment check skipped"]
    installed: dict[str, str] = {}
    for dist_info in site.glob("*.dist-info"):
        name, version = dist_info.name.rsplit("-", 1)[0], ""
        meta = dist_info / "METADATA"
        if meta.is_file():
            fields: dict[str, str] = {}
            for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
                if ": " in line:
                    key, value = line.split(": ", 1)
                    fields.setdefault(key, value)
            name, version = fields.get("Name", name), fields.get("Version", "")
        installed[normalize(name)] = version
    errors = [f"installed drift: {name}=={installed[name]} but pinned {pins[name]}"
              for name in sorted(pins) if name in installed and installed[name] != pins[name]]
    warnings = [f"not installed in .venv: {name}=={pins[name]}" for name in sorted(pins) if name not in installed]
    warnings += [f"unpinned installed distribution: {name}=={version}"
                 for name, version in sorted(installed.items())
                 if name not in pins and name not in IGNORED_DISTS]
    return errors, warnings


def run(root: Path = ROOT) -> dict:
    errors: list[str] = []
    try:
        document = load_inventory(root / "tools" / "compliance" / "spdx.json")
    except ValueError as exc:
        return {"errors": [str(exc)], "warnings": [], "reviews": []}
    pins: dict[str, str] = {}
    for path in [root / "calibration" / "requirements.txt"]:
        if not path.is_file():
            errors.append(f"missing requirements file: {path}")
            continue
        try:
            pins.update(parse_requirements(path))
        except ValueError as exc:
            errors.append(str(exc))
    errors += check_requirements(pins, pypi_purls(document))
    errors += check_files([root / "THIRD_PARTY_NOTICES.md",
                           root / "pcb-ocr" / "THIRD_PARTY.md",
                           root / "tools" / "compliance" / "spdx.json"])
    venv_errors, warnings = check_venv(pins, root)
    return {"errors": errors + venv_errors, "warnings": warnings,
            "reviews": collect_reviews(document)}


def main(argv: list[str] | None = None) -> int:
    report = run()
    if argv and "--json" in argv:
        print(json.dumps(report, indent=2))
        return 1 if report["errors"] else 0
    for error in report["errors"]:
        print(f"ERROR: {error}")
    for warning in report["warnings"]:
        print(f"warning: {warning}")
    for review in report["reviews"]:
        print(f"REVIEW: {review}")
    print(f"\n{len(report['errors'])} error(s), {len(report['warnings'])} warning(s), "
          f"{len(report['reviews'])} open review item(s)")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
