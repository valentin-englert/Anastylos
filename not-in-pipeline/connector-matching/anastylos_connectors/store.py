"""File-based connector gallery with safe read/write operations.

One JSON observation per file, written atomically (temporary file
plus rename). Corrupt records and observation-version mismatches are
reported in `excluded`, never silently compared or silently dropped.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from anastylos_connectors.match import check_version

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_id(record_id):
    """Reject ids that could escape the gallery directory."""
    if not isinstance(record_id, str) or not ID_PATTERN.match(record_id):
        raise ValueError(f"record id must match {ID_PATTERN.pattern}, got {record_id!r}")
    return record_id


def save_record(gallery_dir, record, overwrite=False):
    """Store one observation atomically; refuse poor quality and clobbers."""
    record_id = validate_id(record.get("id"))
    quality = record.get("quality", {})
    if not quality.get("usable"):
        raise ValueError(f"refusing to enroll {record_id}: {quality.get('reason')}")
    gallery = Path(gallery_dir)
    gallery.mkdir(parents=True, exist_ok=True)
    target = gallery / f"{record_id}.json"
    if target.exists() and not overwrite:
        raise ValueError(f"gallery already holds {record_id}; pass overwrite to replace it")
    payload = json.dumps({**record, "id": record_id}, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=record_id, suffix=".tmp", dir=str(gallery))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def load_gallery(gallery_dir):
    """Read every observation; bad files land in `excluded` with reasons."""
    gallery = Path(gallery_dir)
    if not gallery.is_dir():
        raise ValueError(f"gallery directory not found: {gallery_dir}")
    records, excluded = [], []
    for path in sorted(gallery.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            excluded.append({"id": path.stem, "reason": f"unreadable record: {exc}"})
            continue
        if not isinstance(record, dict) or record.get("id") != path.stem:
            excluded.append({"id": path.stem, "reason": "record id does not match its file"})
            continue
        mismatch = check_version(record)
        if mismatch:
            excluded.append({"id": path.stem, "reason": mismatch})
            continue
        records.append(record)
    records.sort(key=lambda item: item["id"])
    return {"records": records, "excluded": excluded}
