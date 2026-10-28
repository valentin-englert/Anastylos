"""Reproduce the reported A-format rejection by B's validator.

Builds Muse A's SCHEMA.md section 9 example inside A's envelope and runs
Muse B's load_observation_file over it. Read-only wrt the matcher.
"""
import json
import sys

sys.path.insert(0, "tools/retrospective-custody/matcher")
from validate import ObservationError, load_observation_file  # noqa: E402

a_example = {
    "schema_version": "anastylos-observation-v1",
    "event_id": "example-001",
    "kind": "tool_interaction",
    "status": "observed",
    "time": {"clock_id": "cam1-pts", "start_ms": 12000, "end_ms": 15500},
    "location": {"camera": "CAM1", "x": 640.0, "y": 360.0,
                 "uncertainty_px": 25.0, "pose_epoch": "cam1-full",
                 "frame": 360},
    "state": {"interaction": "engagement", "tip_visible": True,
              "contact": "unknown"},
    "evidence": {"camera": "CAM1", "source_sha256": "0" * 64,
                 "frames": [{"frame": 360, "pts_seconds": 12.0,
                             "role": "primary"}],
                 "basis_event_ids": []},
    "limitations": ["Tip resolved but contact occluded by hand."],
}

# Attempt 1: A's envelope verbatim.
doc_a = {"schema_version": "anastylos-observation-v1",
         "dataset_id": "snes02-obs-20261028",
         "sync_manifest_ref": None,
         "events": [a_example]}
json.dump(doc_a, open("snes-02/work/repro-a.json", "w"))
try:
    load_observation_file(__import__("pathlib").Path("snes-02/work/repro-a.json"))
    print("attempt1: VALID (unexpected)")
except ObservationError as e:
    print(f"attempt1: REJECTED: {e}")

# Attempt 2: add session_ref (keep A's event shape).
doc_b = dict(doc_a)
doc_b["session_ref"] = "snes02"
json.dump(doc_b, open("snes-02/work/repro-b.json", "w"))
try:
    load_observation_file(__import__("pathlib").Path("snes-02/work/repro-b.json"))
    print("attempt2: VALID (unexpected)")
except ObservationError as e:
    print(f"attempt2: REJECTED: {e}")
