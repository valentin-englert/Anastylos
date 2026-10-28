"""Load the frozen custody config and the change-detection kit it cites."""

from __future__ import annotations

import json
from pathlib import Path

from anastylos_diff.config import canonical_sha256, load_configs, sha256_file


def load_custody_config(path: Path) -> tuple[dict, dict, dict, dict]:
    """Return (custody, harness, kit, provenance).

    The harness and kit come from change-detection's own loader, so the kit
    hash gate there applies unchanged.
    """
    path = Path(path).resolve()
    custody = json.loads(path.read_text(encoding="utf-8"))
    if custody.get("schema") != "custody-config@1":
        raise SystemExit(f"{path}: not a custody-config@1 file")
    harness_path = (path.parent / custody["change_detection_harness"]).resolve()
    harness, kit, harness_meta = load_configs(harness_path)
    provenance = {
        "custody_config_path": str(path),
        "custody_config_file_sha256": sha256_file(path),
        "custody_config_canonical_sha256": canonical_sha256(custody),
        **harness_meta,
    }
    return custody, harness, kit, provenance
