#!/usr/bin/env python3
"""Validate ANASTYLOS CAPTURE evidence packages. Standard library only.

A package is a ZIP with ``manifest.json``, original assets, optional
previews and optional ``annotations.json``. The validator checks the schema
version, required fields, asset hashes, filename safety and size limits,
then reports which captures can be accepted for review.

It never modifies memory.jsonl, never asserts confirmed relationships, and
never writes outside an explicitly requested, empty staging directory.

Exit codes: 0 valid, 1 invalid package, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
APP_ID = "anastylos-capture"

MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 5 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 5000

CAPTURE_SOURCES = ("camera", "file", "gallery")
PHOTO_ROLES = ("front", "back", "detail", "other")
REVIEW_STATES = ("unreviewed", "reviewed", "confirmed")
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")
MEASURE_PROVENANCE = ("operator-measured", "operator-estimated", "unknown")

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")


def is_id(value: object) -> bool:
    return isinstance(value, str) and UUID_RE.match(value) is not None


def is_iso_utc(value: object) -> bool:
    if not isinstance(value, str) or ISO_UTC_RE.match(value) is None:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def is_non_empty_text(value: object) -> bool:
    return isinstance(value, str) and len(value.strip()) > 0


def unsafe_path_reason(path: str) -> str | None:
    """Why a ZIP member name is unsafe, or None when it is acceptable."""
    if not path or len(path) > 1024:
        return "empty or over-long path"
    if "\\" in path:
        return "backslashes are forbidden"
    if path.startswith("/") or re.match(r"^[a-zA-Z]:", path):
        return "absolute paths are forbidden"
    segments = path.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        return "empty, dot or dot-dot segments are forbidden"
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in path):
        return "control characters are forbidden"
    return None


def check_confirmed_field(value: object, what: str, issues: list[str]) -> None:
    if not isinstance(value, dict):
        issues.append(f"{what} must be an object with value and state")
        return
    state = value.get("state")
    field_value = value.get("value")
    if state == "confirmed":
        if field_value is None:
            issues.append(f"{what} is confirmed but has no value")
    elif state == "unknown":
        if field_value is not None:
            issues.append(f"{what} is unknown but carries a value; unknowns stay null")
    else:
        issues.append(f"{what} state must be confirmed or unknown")


def validate_session(session: object, prefix: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(session, dict):
        return [f"{prefix} is not an object"]
    if session.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"{prefix} has an unsupported schema_version")
    if not is_id(session.get("session_id")):
        issues.append(f"{prefix} session_id is not a valid id")
    if not is_non_empty_text(session.get("name")):
        issues.append(f"{prefix} name must be a non-empty string")
    if not is_iso_utc(session.get("created_at")):
        issues.append(f"{prefix} created_at is not ISO 8601 UTC")
    if not is_iso_utc(session.get("updated_at")):
        issues.append(f"{prefix} updated_at is not ISO 8601 UTC")
    for key in ("assembly_memory_session", "tray_default"):
        value = session.get(key)
        if value is not None and not is_non_empty_text(value):
            issues.append(f"{prefix} {key} must be a non-empty string or null")
    return issues


def validate_measurement(measurement: object, prefix: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(measurement, dict):
        return [f"{prefix} is not an object"]
    value = measurement.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        issues.append(f"{prefix} value must be a number")
    if not is_non_empty_text(measurement.get("unit")):
        issues.append(f"{prefix} needs an explicit unit")
    if measurement.get("provenance") not in MEASURE_PROVENANCE:
        issues.append(f"{prefix} needs explicit provenance")
    label = measurement.get("label")
    if label is not None and not isinstance(label, str):
        issues.append(f"{prefix} label must be a string or null")
    return issues


def validate_candidate(candidate: object, prefix: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(candidate, dict):
        return [f"{prefix} is not an object"]
    if candidate.get("state") != "suggested":
        issues.append(f"{prefix} state must be 'suggested', never confirmed")
    if not is_non_empty_text(candidate.get("label")):
        issues.append(f"{prefix} label must be a non-empty string")
    if not is_non_empty_text(candidate.get("provider")) or not is_non_empty_text(
        candidate.get("model")
    ):
        issues.append(f"{prefix} needs provider and model identifiers")
    return issues


def validate_image(image: object, prefix: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(image, dict):
        return [f"{prefix} is not an object"]
    if image.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"{prefix} has an unsupported schema_version")
    if not is_id(image.get("image_id")):
        issues.append(f"{prefix} image_id is not a valid id")
    if not is_id(image.get("observation_id")):
        issues.append(f"{prefix} observation_id is not a valid id")
    if image.get("role") not in PHOTO_ROLES:
        issues.append(f"{prefix} role must be front, back, detail or other")
    filename = image.get("filename")
    if not is_non_empty_text(filename) or unsafe_path_reason(str(filename)) is not None:
        issues.append(f"{prefix} filename is empty or unsafe")
    elif "/" in str(filename):
        issues.append(f"{prefix} filename must not contain directories")
    if image.get("mime") not in IMAGE_MIMES:
        issues.append(f"{prefix} mime {image.get('mime')!r} is not an accepted image type")
    byte_length = image.get("byte_length")
    if not isinstance(byte_length, int) or isinstance(byte_length, bool) or byte_length <= 0:
        issues.append(f"{prefix} byte_length must be a positive integer")
    elif byte_length > MAX_FILE_BYTES:
        issues.append(f"{prefix} byte_length exceeds the per-file limit")
    sha = image.get("sha256")
    if not isinstance(sha, str) or HEX64_RE.match(sha) is None:
        issues.append(f"{prefix} sha256 must be lowercase hex over the original bytes")
    if not is_iso_utc(image.get("captured_at")):
        issues.append(f"{prefix} captured_at is not ISO 8601 UTC")
    crop = image.get("crop")
    if crop is not None:
        if not isinstance(crop, dict):
            issues.append(f"{prefix} crop must be an object or null")
        else:
            numbers: dict[str, float] = {}
            for key in ("x", "y", "width", "height"):
                value = crop.get(key)
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not 0 <= value <= 1
                ):
                    issues.append(f"{prefix} crop.{key} must be a fraction in 0..1")
                else:
                    numbers[key] = float(value)
            if len(numbers) == 4:
                if numbers["x"] + numbers["width"] > 1 or numbers["y"] + numbers["height"] > 1:
                    issues.append(f"{prefix} crop rectangle must stay inside the image")
                if numbers["width"] <= 0 or numbers["height"] <= 0:
                    issues.append(f"{prefix} crop rectangle must have positive size")
    return issues


def validate_observation(obs: object, prefix: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(obs, dict):
        return [f"{prefix} is not an object"]
    if obs.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"{prefix} has an unsupported schema_version")
    if not is_id(obs.get("observation_id")):
        issues.append(f"{prefix} observation_id is not a valid id")
    if not is_id(obs.get("session_id")):
        issues.append(f"{prefix} session_id is not a valid id")
    for key in ("component_reference", "tray_reference", "operator_notes"):
        value = obs.get(key)
        if value is not None and not is_non_empty_text(value):
            issues.append(f"{prefix} {key} must be a non-empty string or null")
    if not is_iso_utc(obs.get("captured_at")):
        issues.append(f"{prefix} captured_at is not ISO 8601 UTC")
    if obs.get("capture_source") not in CAPTURE_SOURCES:
        issues.append(f"{prefix} capture_source must be camera, file or gallery")
    if obs.get("review_state") not in REVIEW_STATES:
        issues.append(f"{prefix} review_state must be unreviewed, reviewed or confirmed")
    markings = obs.get("markings")
    if not isinstance(markings, list):
        issues.append(f"{prefix} markings must be an array")
    else:
        for index, marking in enumerate(markings):
            if not isinstance(marking, dict) or not is_non_empty_text(marking.get("text")):
                issues.append(f"{prefix} markings[{index}] needs non-empty text")
            elif marking.get("state") != "observed":
                issues.append(f"{prefix} markings[{index}] state must be 'observed'")
    category = obs.get("category")
    check_confirmed_field(category, f"{prefix} category", issues)
    if (
        isinstance(category, dict)
        and category.get("state") == "confirmed"
        and not is_non_empty_text(category.get("value"))
    ):
        issues.append(f"{prefix} confirmed category needs a non-empty value")
    measurements = obs.get("measurements")
    if not isinstance(measurements, list):
        issues.append(f"{prefix} measurements must be an array")
    else:
        for index, measurement in enumerate(measurements):
            issues.extend(validate_measurement(measurement, f"{prefix} measurements[{index}]"))
    quantity = obs.get("quantity")
    check_confirmed_field(quantity, f"{prefix} quantity", issues)
    if isinstance(quantity, dict) and quantity.get("state") == "confirmed":
        value = quantity.get("value")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            issues.append(f"{prefix} confirmed quantity must be a positive integer")
    candidates = obs.get("candidates")
    if not isinstance(candidates, list):
        issues.append(f"{prefix} candidates must be an array")
    else:
        for index, candidate in enumerate(candidates):
            issues.extend(validate_candidate(candidate, f"{prefix} candidates[{index}]"))
    provenance = obs.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("app") != APP_ID:
        issues.append(f"{prefix} provenance.app must be {APP_ID}")
    else:
        if not is_non_empty_text(provenance.get("app_version")):
            issues.append(f"{prefix} provenance.app_version must be a non-empty string")
        user_agent = provenance.get("user_agent")
        if user_agent is not None and not isinstance(user_agent, str):
            issues.append(f"{prefix} provenance.user_agent must be a string or null")
    if not isinstance(obs.get("export_history"), list):
        issues.append(f"{prefix} export_history must be an array")
    images = obs.get("images")
    if not isinstance(images, list) or len(images) == 0:
        issues.append(f"{prefix} carries no photo evidence")
    else:
        for index, image in enumerate(images):
            issues.extend(validate_image(image, f"{prefix} images[{index}]"))
    return issues


def hash_member(zf: zipfile.ZipFile, name: str) -> tuple[str, int]:
    """SHA-256 hex and length of a member, streamed with a size cap."""
    digest = hashlib.sha256()
    total = 0
    with zf.open(name, "r") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ValueError(f"{name} exceeds the per-file limit")
            digest.update(chunk)
    return digest.hexdigest(), total


def validate_package(path: Path) -> dict:
    """Validate a package file. Returns a JSON-serializable report dict."""
    report: dict = {
        "package_path": str(path),
        "package_id": None,
        "schema_version": None,
        "status": "invalid",
        "issues": [],
        "observations": [],
        "accepted_for_review": [],
    }

    def fail(message: str) -> dict:
        report["issues"].append(message)
        return report

    try:
        size = path.stat().st_size
    except OSError as exc:
        return fail(f"cannot read package: {exc}")
    if size > MAX_PACKAGE_BYTES:
        return fail(f"package is {size} bytes; the limit is {MAX_PACKAGE_BYTES}")
    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        return fail(f"not a readable ZIP archive: {exc}")
    except OSError as exc:
        return fail(f"cannot read package: {exc}")

    with zf:
        names = zf.namelist()
        if len(names) > MAX_ENTRIES:
            return fail(f"archive holds {len(names)} entries; the limit is {MAX_ENTRIES}")
        seen: set[str] = set()
        for name in names:
            reason = unsafe_path_reason(name.rstrip("/")) if not name.endswith("/") else None
            if name.endswith("/"):
                info_reason = f"directory entry {name!r} is not expected"
                report["issues"].append(info_reason)
                continue
            if reason is not None:
                return fail(f"unsafe ZIP path {name!r}: {reason}")
            if name in seen:
                return fail(f"duplicate ZIP entry {name!r}")
            seen.add(name)
        if "manifest.json" not in seen:
            return fail("manifest.json is missing")
        try:
            manifest_size = zf.getinfo("manifest.json").file_size
        except KeyError:
            return fail("manifest.json is missing")
        if manifest_size > MAX_MANIFEST_BYTES:
            return fail(f"manifest.json is {manifest_size} bytes; the limit is {MAX_MANIFEST_BYTES}")
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return fail(f"manifest.json is not valid UTF-8 JSON: {exc}")
        except (zipfile.BadZipFile, OSError, EOFError) as exc:
            return fail(f"manifest.json cannot be read from the archive: {exc}")
        if not isinstance(manifest, dict):
            return fail("manifest.json is not a JSON object")

        issues: list[str] = list(report["issues"])
        if manifest.get("schema_version") != SCHEMA_VERSION:
            return fail(f"unsupported schema_version {manifest.get('schema_version')!r}")
        report["schema_version"] = SCHEMA_VERSION
        package_id = manifest.get("package_id")
        if not is_id(package_id):
            return fail("package_id is not a valid id")
        report["package_id"] = package_id
        if not is_iso_utc(manifest.get("exported_at")):
            issues.append("exported_at is not ISO 8601 UTC")
        generator = manifest.get("generator")
        if not isinstance(generator, dict) or generator.get("app") != APP_ID:
            issues.append("generator.app must be anastylos-capture")
        sessions = manifest.get("sessions")
        observations = manifest.get("observations")
        files = manifest.get("files")
        if (
            not isinstance(sessions, list)
            or not isinstance(observations, list)
            or not isinstance(files, list)
        ):
            return fail("manifest needs sessions, observations and files arrays")

        session_ids: set[str] = set()
        for index, session in enumerate(sessions):
            issues.extend(validate_session(session, f"sessions[{index}]"))
            ident = session.get("session_id") if isinstance(session, dict) else None
            if isinstance(ident, str):
                if ident in session_ids:
                    issues.append(f"duplicate session_id {ident}")
                session_ids.add(ident)

        observation_ids: set[str] = set()
        image_ids: set[str] = set()
        for index, obs in enumerate(observations):
            prefix = f"observations[{index}]"
            issues.extend(validate_observation(obs, prefix))
            if not isinstance(obs, dict):
                continue
            ident = obs.get("observation_id")
            if isinstance(ident, str):
                if ident in observation_ids:
                    issues.append(f"duplicate observation_id {ident}")
                observation_ids.add(ident)
            session_ref = obs.get("session_id")
            if isinstance(session_ref, str) and session_ref not in session_ids:
                issues.append(f"{prefix} references unknown session {session_ref}")
            images = obs.get("images")
            if isinstance(images, list):
                for image in images:
                    image_id = image.get("image_id") if isinstance(image, dict) else None
                    if isinstance(image_id, str):
                        if image_id in image_ids:
                            issues.append(f"duplicate image_id {image_id}")
                        image_ids.add(image_id)

        file_paths: set[str] = set()
        file_records: dict[str, dict] = {}
        for index, entry in enumerate(files):
            prefix = f"files[{index}]"
            if not isinstance(entry, dict):
                issues.append(f"{prefix} is not an object")
                continue
            file_path = entry.get("path")
            if not isinstance(file_path, str) or not file_path:
                issues.append(f"{prefix} has no path")
                continue
            if file_path in file_paths:
                issues.append(f"duplicate file path {file_path}")
            file_paths.add(file_path)
            file_records[file_path] = entry
            if entry.get("kind") not in ("original", "preview"):
                issues.append(f"{prefix} has unknown kind")
            if not is_id(entry.get("observation_id")) or not is_id(entry.get("image_id")):
                issues.append(f"{prefix} needs observation and image ids")
            sha = entry.get("sha256")
            if not isinstance(sha, str) or HEX64_RE.match(sha) is None:
                issues.append(f"{prefix} sha256 must be lowercase hex")
            byte_count = entry.get("bytes")
            if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
                issues.append(f"{prefix} bytes must be a non-negative integer")

        # The archive must hold exactly the manifest, the listed files, and
        # the optional annotations file. Anything else is unaccounted evidence.
        expected = set(file_paths) | {"manifest.json"}
        has_annotations = "annotations.json" in seen
        if has_annotations:
            expected.add("annotations.json")
        for name in sorted(seen - expected):
            issues.append(f"unlisted archive member {name!r} is not covered by the manifest")
        for file_path in sorted(file_paths - seen):
            issues.append(f"manifest file {file_path!r} is missing from the archive")

        # Verify hashes and byte counts for members that exist.
        verified: dict[str, bool] = {}
        for file_path in sorted(file_paths & seen):
            try:
                digest, total = hash_member(zf, file_path)
            except ValueError as exc:
                issues.append(str(exc))
                verified[file_path] = False
                continue
            except (zipfile.BadZipFile, OSError, EOFError) as exc:
                issues.append(f"{file_path!r} cannot be read from the archive: {exc}")
                verified[file_path] = False
                continue
            record = file_records[file_path]
            if digest != record.get("sha256"):
                issues.append(f"{file_path!r} fails the SHA-256 check")
                verified[file_path] = False
            elif total != record.get("bytes"):
                issues.append(f"{file_path!r} length disagrees with the manifest")
                verified[file_path] = False
            else:
                verified[file_path] = True

        if has_annotations:
            try:
                annotations = json.loads(zf.read("annotations.json").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                issues.append(f"annotations.json is not valid UTF-8 JSON: {exc}")
            except (zipfile.BadZipFile, OSError, EOFError) as exc:
                issues.append(f"annotations.json cannot be read from the archive: {exc}")
            else:
                if not isinstance(annotations, dict):
                    issues.append("annotations.json is not a JSON object")

        # Per-observation verdicts: structurally valid, fully present, hashes good.
        for index, obs in enumerate(observations):
            if not isinstance(obs, dict) or not is_id(obs.get("observation_id")):
                continue
            ident = str(obs.get("observation_id"))
            prefix = "observations[%d]" % index
            obs_issues = [
                i for i in issues if i.startswith(prefix) or _mentions(i, obs, ident)
            ]
            member_ok = True
            for file_path, record in file_records.items():
                if record.get("observation_id") != ident:
                    continue
                if not verified.get(file_path, False):
                    member_ok = False
            accepted = member_ok and not obs_issues and not _global_blockers(issues)
            entry_report = {"observation_id": ident, "accepted": accepted, "issues": obs_issues}
            if not member_ok and not obs_issues:
                entry_report["issues"] = ["one or more evidence files failed verification"]
            report["observations"].append(entry_report)
            if accepted:
                report["accepted_for_review"].append(ident)

        report["issues"] = issues
        report["status"] = "valid" if not issues else "invalid"
        return report


def _mentions(issue: str, obs: dict, ident: str) -> bool:
    """Whether a manifest issue belongs to one observation's verdict."""
    if ident in issue:
        return True
    images = obs.get("images")
    if isinstance(images, list):
        for image in images:
            if not isinstance(image, dict):
                continue
            for key in ("image_id", "filename"):
                value = image.get(key)
                if isinstance(value, str) and value and value in issue:
                    return True
    return False


def _global_blockers(issues: list[str]) -> bool:
    """Manifest-wide problems that veto every observation verdict."""
    markers = (
        "schema_version",
        "package_id",
        "exported_at",
        "generator",
        "manifest needs",
        "sessions[",
        "files[",
        "unlisted archive member",
        "annotations.json",
        "duplicate session_id",
        "directory entry",
    )
    return any(any(marker in issue for marker in markers) for issue in issues)


def stage_package(package_path: Path, target: Path, report: dict) -> list[str]:
    """Extract a VALIDATED package into an empty staging directory.

    Refuses to touch non-empty targets, so nothing is ever overwritten.
    Returns the staged relative paths.
    """
    if report.get("status") != "valid":
        raise ValueError("refusing to stage an invalid package")
    if target.exists():
        if not target.is_dir() or any(target.iterdir()):
            raise ValueError(f"staging target {target} exists and is not an empty directory")
    else:
        target.mkdir(parents=True)
    staged: list[str] = []
    with zipfile.ZipFile(package_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            reason = unsafe_path_reason(name)
            if reason is not None:
                raise ValueError(f"unsafe ZIP path {name!r}: {reason}")
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name, "r") as source, destination.open("wb") as out:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            staged.append(name)
    return sorted(staged)


def format_human(report: dict) -> str:
    lines = [
        f"package: {report['package_path']}",
        f"package_id: {report['package_id']}",
        f"status: {report['status']}",
    ]
    if report["issues"]:
        lines.append(f"issues ({len(report['issues'])}):")
        lines.extend(f"  - {issue}" for issue in report["issues"])
    lines.append(f"accepted for review ({len(report['accepted_for_review'])}):")
    lines.extend(f"  - {ident}" for ident in report["accepted_for_review"])
    for obs in report["observations"]:
        if not obs["accepted"]:
            lines.append(f"rejected {obs['observation_id']}:")
            lines.extend(f"    - {issue}" for issue in obs["issues"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate an ANASTYLOS CAPTURE evidence package."
    )
    parser.add_argument("command", choices=["validate"], help="command to run")
    parser.add_argument("package", type=Path, help="path to the .zip package")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--extract",
        type=Path,
        default=None,
        metavar="DIR",
        help="stage a valid package into an empty directory (refuses non-empty targets)",
    )
    args = parser.parse_args(argv)

    report = validate_package(args.package)
    if args.extract is not None:
        try:
            staged = stage_package(args.package, args.extract, report)
        except (OSError, ValueError, zipfile.BadZipFile, EOFError) as exc:
            print(f"error: cannot stage package: {exc}", file=sys.stderr)
            return 2
        report["staged_in"] = str(args.extract)
        report["staged_files"] = staged

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_human(report))
    return 0 if report["status"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
