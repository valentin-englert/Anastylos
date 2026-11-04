"""Durable Core Capture Evidence Index (SQLite + content-addressed blobs)."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA
from .paths import blob_relpath, is_sha256, is_uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packages (
  package_id TEXT PRIMARY KEY,
  zip_sha256 TEXT NOT NULL,
  zip_bytes INTEGER NOT NULL,
  staged_at TEXT,
  ingested_at TEXT NOT NULL,
  source_path TEXT NOT NULL,
  session_ids_json TEXT NOT NULL,
  observation_ids_json TEXT NOT NULL,
  receipt_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
  observation_id TEXT PRIMARY KEY,
  capture_session_id TEXT NOT NULL,
  core_session_id TEXT NOT NULL,
  captured_at TEXT,
  capture_source TEXT,
  review_state TEXT,
  provenance_json TEXT NOT NULL,
  first_package_id TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  association_basis TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS images (
  image_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  byte_length INTEGER NOT NULL,
  mime TEXT,
  filename TEXT,
  role TEXT,
  captured_at TEXT,
  blob_relpath TEXT NOT NULL,
  first_package_id TEXT NOT NULL,
  FOREIGN KEY (observation_id) REFERENCES observations(observation_id)
);

CREATE TABLE IF NOT EXISTS package_observations (
  package_id TEXT NOT NULL,
  observation_id TEXT NOT NULL,
  PRIMARY KEY (package_id, observation_id)
);

CREATE TABLE IF NOT EXISTS import_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  package_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  result TEXT NOT NULL,
  detail TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_core_session ON observations(core_session_id);
CREATE INDEX IF NOT EXISTS idx_obs_capture_session ON observations(capture_session_id);
CREATE INDEX IF NOT EXISTS idx_images_sha ON images(sha256);
CREATE INDEX IF NOT EXISTS idx_images_obs ON images(observation_id);
CREATE INDEX IF NOT EXISTS idx_ledger_package ON import_ledger(package_id);
"""


class ConflictError(ValueError):
    """Existing evidence disagrees with a new claim; nothing is rewritten."""


class EvidenceStore:
    """Operator-owned Core evidence directory. All paths stay under ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "blobs" / "sha256").mkdir(parents=True, exist_ok=True)
        (self.root / "imports").mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "capture-evidence.sqlite"
        self._conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA_SQL)
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema', ?)", (SCHEMA,)
            )
        elif row["value"] != SCHEMA:
            raise ConflictError(
                f"evidence store schema mismatch: {row['value']!r} != {SCHEMA!r}"
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- queries -------------------------------------------------------------

    def package_complete(self, package_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM packages WHERE package_id = ?", (package_id,)
        ).fetchone()
        return row is not None

    def get_observation(self, observation_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM observations WHERE observation_id = ?", (observation_id,)
        ).fetchone()
        if row is None:
            return None
        images = self._conn.execute(
            "SELECT * FROM images WHERE observation_id = ? ORDER BY captured_at, image_id",
            (observation_id,),
        ).fetchall()
        return {
            "observation": dict(row),
            "images": [dict(img) for img in images],
        }

    def lookup_session(self, session_id: str) -> list[dict[str, Any]]:
        if not is_uuid(session_id):
            raise ValueError("session_id must be a UUID")
        rows = self._conn.execute(
            """
            SELECT * FROM observations
            WHERE core_session_id = ? OR capture_session_id = ?
            ORDER BY captured_at, observation_id
            """,
            (session_id, session_id),
        ).fetchall()
        return [self.get_observation(row["observation_id"]) for row in rows]  # type: ignore[misc]

    def status(self) -> dict[str, Any]:
        packages = self._conn.execute("SELECT COUNT(*) AS n FROM packages").fetchone()["n"]
        observations = self._conn.execute(
            "SELECT COUNT(*) AS n FROM observations"
        ).fetchone()["n"]
        images = self._conn.execute("SELECT COUNT(*) AS n FROM images").fetchone()["n"]
        return {
            "schema": SCHEMA,
            "core_dir": str(self.root),
            "packages": packages,
            "observations": observations,
            "images": images,
        }

    def ledger_tail(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM import_ledger ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    # -- blob write ----------------------------------------------------------

    def write_blob(self, sha256: str, data: bytes) -> str:
        """Store original bytes content-addressed. Idempotent when hash matches."""
        if not is_sha256(sha256):
            raise ValueError("sha256 must be lowercase hex")
        rel = blob_relpath(sha256)
        dest = self.root / rel
        if dest.is_file():
            existing = dest.read_bytes()
            if existing != data:
                raise ConflictError(
                    f"blob {sha256} already stored with different bytes"
                )
            return rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".blob-", suffix=".tmp", dir=str(dest.parent)
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, dest)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return rel

    def blob_bytes(self, sha256: str) -> bytes:
        rel = blob_relpath(sha256)
        path = self.root / rel
        return path.read_bytes()

    # -- commit --------------------------------------------------------------

    def begin(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self._conn.execute("COMMIT")

    def rollback(self) -> None:
        self._conn.execute("ROLLBACK")

    def record_ledger(
        self,
        package_id: str,
        started_at: str,
        finished_at: str,
        result: str,
        detail: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO import_ledger(package_id, started_at, finished_at, result, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (package_id, started_at, finished_at, result, detail),
        )

    def insert_package(self, record: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO packages(
              package_id, zip_sha256, zip_bytes, staged_at, ingested_at,
              source_path, session_ids_json, observation_ids_json, receipt_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["package_id"],
                record["zip_sha256"],
                record["zip_bytes"],
                record.get("staged_at"),
                record["ingested_at"],
                record["source_path"],
                json.dumps(record["session_ids"]),
                json.dumps(record["observation_ids"]),
                record["receipt_path"],
            ),
        )

    def upsert_observation(self, record: dict[str, Any]) -> None:
        existing = self._conn.execute(
            "SELECT * FROM observations WHERE observation_id = ?",
            (record["observation_id"],),
        ).fetchone()
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO observations(
                  observation_id, capture_session_id, core_session_id,
                  captured_at, capture_source, review_state, provenance_json,
                  first_package_id, ingested_at, association_basis
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["observation_id"],
                    record["capture_session_id"],
                    record["core_session_id"],
                    record.get("captured_at"),
                    record.get("capture_source"),
                    record.get("review_state"),
                    json.dumps(record.get("provenance") or {}),
                    record["first_package_id"],
                    record["ingested_at"],
                    record["association_basis"],
                ),
            )
            return
        # Conflict if identity fields disagree; otherwise keep first record.
        checks = (
            ("capture_session_id", record["capture_session_id"]),
            ("core_session_id", record["core_session_id"]),
        )
        for key, expected in checks:
            if existing[key] != expected:
                raise ConflictError(
                    f"observation {record['observation_id']} conflict on {key}: "
                    f"stored {existing[key]!r} != new {expected!r}"
                )

    def upsert_image(self, record: dict[str, Any]) -> None:
        existing = self._conn.execute(
            "SELECT * FROM images WHERE image_id = ?", (record["image_id"],)
        ).fetchone()
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO images(
                  image_id, observation_id, sha256, byte_length, mime, filename,
                  role, captured_at, blob_relpath, first_package_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["image_id"],
                    record["observation_id"],
                    record["sha256"],
                    record["byte_length"],
                    record.get("mime"),
                    record.get("filename"),
                    record.get("role"),
                    record.get("captured_at"),
                    record["blob_relpath"],
                    record["first_package_id"],
                ),
            )
            return
        for key in ("observation_id", "sha256", "byte_length"):
            if existing[key] != record[key]:
                raise ConflictError(
                    f"image {record['image_id']} conflict on {key}: "
                    f"stored {existing[key]!r} != new {record[key]!r}"
                )

    def link_package_observation(self, package_id: str, observation_id: str) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO package_observations(package_id, observation_id)
            VALUES (?, ?)
            """,
            (package_id, observation_id),
        )

    def write_receipt(self, package_id: str, receipt: dict[str, Any]) -> str:
        dest_dir = self.root / "imports" / package_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "receipt.json"
        payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=".receipt-", suffix=".tmp", dir=str(dest_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, dest)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return str(dest.relative_to(self.root).as_posix())
