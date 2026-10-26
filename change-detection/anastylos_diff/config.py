"""Load the harness config and check it against the calibration kit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(obj) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_configs(config_path: Path):
    config_path = Path(config_path).resolve()
    harness = json.loads(config_path.read_text(encoding="utf-8"))
    kit_path = Path(harness["calibration_config"]["path"])
    if not kit_path.is_absolute():
        kit_path = (config_path.parent / kit_path).resolve()
    if not kit_path.exists():
        raise SystemExit(f"Calibration config not found: {kit_path}")
    kit_hash = sha256_file(kit_path)
    expected = harness["calibration_config"]["sha256"]
    if kit_hash != expected:
        raise SystemExit(
            f"Calibration config hash mismatch ({kit_hash} != {expected}). "
            "Refusing to run with a different coordinate file."
        )
    kit = json.loads(kit_path.read_text(encoding="utf-8"))
    meta = {
        "harness_config_path": str(config_path),
        "harness_config_file_sha256": sha256_file(config_path),
        "harness_config_canonical_sha256": canonical_sha256(harness),
        "kit_config_path": str(kit_path),
        "kit_config_sha256": kit_hash,
    }
    return harness, kit, meta
