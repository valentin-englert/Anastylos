"""Read-only view of one drawing-proof job folder: state, operator binding, outline geometry, input hashes.

Nothing here writes into a job folder and nothing calls ``job_control.status`` (which can rewrite progress.json):
the explosion view only reads what the drawing jobs and the operator already left on disk.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

JOB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
RECTIFIED_NAMES = ("rectified.png", "chosen_rectified.png")  # drawing_job.py writes the second name
MAX_SVG_BYTES = 4_000_000
DRAWABLE_STATUSES = frozenset({"drawn_unconfirmed", "NEEDS_CONFIRMATION"})
MM_TOLERANCE = 0.5  # width="159.3mm" next to viewBox 159.33: rounding in the generator

_PATH_CHARS = re.compile(r"^[MLHVZ0-9eE.,\s+\-]+$")
_PATH_COMMAND = re.compile(r"([MLHVZ])([^MLHVZ]*)")
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


class JobError(ValueError):
    """A job folder or its drawing cannot be used; the message is shown to the operator."""


@dataclass(frozen=True)
class Outline:
    paths: tuple[str, ...]  # path data exactly as the drawing job wrote it (absolute M/L/H/V/Z only)
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in the drawing's mm
    sheet_mm: tuple[float, float]


@dataclass(frozen=True)
class JobEvidence:
    job: str
    exists: bool
    state: str | None
    status: str | None
    part: str | None
    scope: str | None
    view_side: str | None
    binding_state: str  # none | confirmed | rejected | stale | unreadable
    outline: Outline | None
    outline_error: str | None
    files: dict  # name -> {"path": relative to drawings/, "sha256": hex}


def jobs_root(session_dir: Path) -> Path:
    return Path(session_dir) / "drawings" / "jobs"


def plain_job(job: str) -> str:
    if not isinstance(job, str) or not JOB_NAME.match(job):
        raise JobError(f"Unknown drawing job {job!r}.")
    return job


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def find_rectified(job_path: Path) -> Path | None:
    for name in RECTIFIED_NAMES:
        if (job_path / name).is_file():
            return job_path / name
    return None


def list_job_names(session_dir: Path) -> list[str]:
    root = jobs_root(session_dir)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if JOB_NAME.match(p.name) and (p / "progress.json").is_file())


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mm(value: str | None) -> float | None:
    match = re.fullmatch(r"\s*([0-9.]+)\s*mm\s*", value or "")
    return float(match.group(1)) if match else None


def _bbox(path_data: str) -> list[tuple[float, float]]:
    """Corner points of an absolute M/L/H/V/Z path. Anything else is refused rather than guessed."""
    if not _PATH_CHARS.match(path_data):
        raise JobError("outline path uses commands other than absolute M, L, H, V, Z")
    points: list[tuple[float, float]] = []
    x = y = 0.0
    for command, args in _PATH_COMMAND.findall(path_data):
        numbers = [float(n) for n in _NUMBER.findall(args)]
        if command in "ML":
            if len(numbers) < 2 or len(numbers) % 2:
                raise JobError("outline path has an incomplete coordinate pair")
            for i in range(0, len(numbers), 2):
                x, y = numbers[i], numbers[i + 1]
                points.append((x, y))
        elif command == "H":
            for x in numbers:
                points.append((x, y))
        elif command == "V":
            for y in numbers:
                points.append((x, y))
    return points


def parse_outline(svg_bytes: bytes) -> Outline:
    """The ``observed_outline`` paths of a drawing-proof drawing.svg (1 unit = 1 mm). Only ``d`` is taken over."""
    if len(svg_bytes) > MAX_SVG_BYTES:
        raise JobError("drawing.svg is unexpectedly large")
    if b"<!DOCTYPE" in svg_bytes or b"<!ENTITY" in svg_bytes:  # generated drawings never declare entities; no new dependency for XML hardening
        raise JobError("drawing.svg declares a DOCTYPE or entity")
    try:
        root = ElementTree.fromstring(svg_bytes)
    except ElementTree.ParseError as exc:
        raise JobError(f"drawing.svg is not valid XML ({exc})") from exc
    try:
        view = [float(v) for v in root.get("viewBox", "").replace(",", " ").split()]
    except ValueError:
        view = []
    if len(view) != 4 or view[2] <= 0 or view[3] <= 0:
        raise JobError("drawing.svg has no usable viewBox")
    width_mm, height_mm = _mm(root.get("width")), _mm(root.get("height"))
    if width_mm is None or height_mm is None or abs(width_mm - view[2]) > MM_TOLERANCE or abs(height_mm - view[3]) > MM_TOLERANCE:
        raise JobError("drawing.svg is not in millimetre sheet units (width/height in mm must match the viewBox)")
    group = next((el for el in root.iter() if el.tag.endswith("}g") and el.get("id") == "observed_outline"), None)
    paths = tuple(el.get("d", "").strip() for el in (group.iter() if group is not None else ())
                  if el.tag.endswith("}path") and el.get("d", "").strip())
    if not paths:
        raise JobError("drawing.svg has no observed outline")
    points = [p for d in paths for p in _bbox(d)]
    if len(points) < 3:
        raise JobError("observed outline has fewer than three points")
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return Outline(paths, (min(xs), min(ys), max(xs), max(ys)), (view[2], view[3]))


def _binding_state(job_path: Path, drawing_sha: str | None) -> str:
    path = job_path / "binding.json"
    if not path.is_file():
        return "none"
    record = _read_json(path)
    if not isinstance(record, dict) or record.get("decision") not in ("confirm", "reject"):
        return "unreadable"
    if drawing_sha is None or record.get("drawing_sha256") != drawing_sha:
        return "stale"  # the drawing changed after the operator decided: that decision no longer covers these bytes
    return "confirmed" if record["decision"] == "confirm" else "rejected"


def read_job(session_dir: Path, job: str) -> JobEvidence:
    job_path = jobs_root(session_dir) / plain_job(job)
    progress = _read_json(job_path / "progress.json") if job_path.is_dir() else None
    if not isinstance(progress, dict):
        return JobEvidence(job, False, None, None, None, None, None, "none", None, "job folder or progress.json missing", {})
    result = progress.get("result") if isinstance(progress.get("result"), dict) else {}
    spec = result.get("spec") if isinstance(result.get("spec"), dict) else {}
    drawing = job_path / "drawing.svg"
    drawing_sha = sha256_file(drawing)
    outline, error = None, None
    if drawing_sha is None:
        error = "drawing.svg missing"
    else:
        try:
            outline = parse_outline(drawing.read_bytes())
        except JobError as exc:
            error = str(exc)
    files = {}
    for name, path in (("drawing.svg", drawing), ("overlay.jpg", job_path / "overlay.jpg"),
                       ("rectified.png", find_rectified(job_path) or job_path / "rectified.png"),
                       ("binding.json", job_path / "binding.json"), ("progress.json", job_path / "progress.json")):
        sha = drawing_sha if path == drawing else sha256_file(path)
        if sha:
            files[name] = {"path": f"jobs/{job}/{path.name}", "sha256": sha}
    return JobEvidence(job, True, progress.get("state"), result.get("status"), spec.get("part"), spec.get("scope"),
                       spec.get("view_side"), _binding_state(job_path, drawing_sha), outline, error, files)
