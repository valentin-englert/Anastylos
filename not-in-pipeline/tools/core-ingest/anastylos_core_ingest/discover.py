"""Read-only discovery over a co-located Receiver ``--data-dir``.

Prefers the Session Feed contract fields from ``index.json``. Does not
modify Receiver-owned files and does not open a network endpoint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import is_uuid


@dataclass(frozen=True)
class StagedPackage:
    package_id: str
    package_dir: Path
    zip_path: Path
    zip_sha256: str
    zip_bytes: int | None
    staged_at: str | None
    status: str | None
    session_ids: list[str]
    observation_ids: list[str]
    accepted_for_review: list[str]
    summary: dict[str, Any]


def load_receiver_index(receiver_data_dir: Path) -> dict[str, Any]:
    path = Path(receiver_data_dir) / "index.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("receiver index.json must be an object")
    packages = raw.get("packages")
    if packages is None:
        return {}
    if not isinstance(packages, dict):
        raise ValueError("receiver index.json packages must be an object")
    return packages


def list_staged_packages(
    receiver_data_dir: Path,
    *,
    session_id: str | None = None,
    package_id: str | None = None,
) -> list[StagedPackage]:
    """Return staged package descriptors, optionally filtered.

    Filtering mirrors ``GET /v1/packages?session_id=``: an unknown but
    well-formed session id yields an empty list.
    """
    if session_id is not None and not is_uuid(session_id):
        raise ValueError("session_id must be a UUID")
    if package_id is not None and not is_uuid(package_id):
        raise ValueError("package_id must be a UUID")

    root = Path(receiver_data_dir)
    packages = load_receiver_index(root)
    out: list[StagedPackage] = []
    for pid, summary in packages.items():
        if not is_uuid(pid):
            continue
        if package_id is not None and pid != package_id:
            continue
        if not isinstance(summary, dict):
            continue
        session_ids = [
            sid for sid in (summary.get("session_ids") or []) if is_uuid(sid)
        ]
        if session_id is not None and session_id not in session_ids:
            continue
        package_dir = root / "packages" / pid
        zip_path = package_dir / "package.zip"
        if not zip_path.is_file():
            continue
        observation_ids = [
            oid
            for oid in (summary.get("observation_ids") or [])
            if isinstance(oid, str) and oid
        ]
        accepted = [
            oid
            for oid in (summary.get("accepted_for_review") or [])
            if isinstance(oid, str) and oid
        ]
        zip_bytes = summary.get("zip_bytes")
        if zip_bytes is not None and not isinstance(zip_bytes, int):
            zip_bytes = None
        out.append(
            StagedPackage(
                package_id=pid,
                package_dir=package_dir,
                zip_path=zip_path,
                zip_sha256=str(summary.get("zip_sha256") or ""),
                zip_bytes=zip_bytes,
                staged_at=summary.get("staged_at")
                if isinstance(summary.get("staged_at"), str)
                else None,
                status=summary.get("status")
                if isinstance(summary.get("status"), str)
                else None,
                session_ids=sorted(session_ids),
                observation_ids=observation_ids,
                accepted_for_review=accepted,
                summary=summary,
            )
        )
    # Stable order by staged_at then package_id (arrival is not semantic order).
    out.sort(key=lambda item: (item.staged_at or "", item.package_id))
    return out
