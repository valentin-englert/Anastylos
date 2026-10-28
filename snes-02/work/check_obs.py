"""Contract + evidence checker for snes02-observations.json.

Validates anastylos-observation-v1 shape rules (enums, required keys,
pairing refs, ms/frame consistency) AND verifies every cited
(camera, frame, pts) against the full PTS dumps and the pair
extraction logs. Exits nonzero on any failure.
"""
import glob
import json
import sys

OBS = "snes-02/observations/snes02-observations.json"
CAM1H = "dd985c9dbdfe08bdad943b157fab8517d596340ec3a4d4a960def08954efa399"
CAM2H = "f179d34924d9b2511a7b9d3d0b2b03354ea3efa593118b45a1f44c452eb27566"
DIMS = {"CAM1": (720, 1280), "CAM2": (1080, 1920)}
KINDS = {"tool_interaction", "source_state", "tray_state", "visibility_gap",
         "pose_change"}
ERRS = []


def err(msg):
    ERRS.append(msg)
    print("FAIL:", msg)


def load_vpts(path):
    pts = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                pts.append(float(line.split(",")[0]))
    return pts


def main():
    spec = json.load(open(OBS))
    assert spec["schema_version"] == "anastylos-observation-v1"
    assert spec["dataset_id"] == "snes02-obs-20261028"
    ref = spec["sync_manifest_ref"]
    import hashlib
    h = hashlib.sha256(open(ref["path"], "rb").read()).hexdigest()
    if h != ref["sha256"]:
        err(f"sync manifest hash mismatch: {h} != {ref['sha256']}")
    evs = spec["events"]
    ids = [e["event_id"] for e in evs]
    if len(set(ids)) != len(ids):
        err("duplicate event_ids")
    idset = set(ids)
    v1 = load_vpts("snes-02/work/cam1.vpts.txt")
    v2 = load_vpts("snes-02/work/cam2.vpts.txt")
    # index all pair logs: (cam, frame) -> pts
    logged = {}
    for p in glob.glob("snes-02/work/cases/*/*-pairs.json"):
        d = json.load(open(p))
        for q in d["pairs"]:
            for cam in ("cam1", "cam2"):
                c = q[cam]
                logged[(cam.upper(), c["frame"])] = c["pts"]
    for e in evs:
        eid = e["event_id"]
        for k in ("schema_version", "event_id", "kind", "status", "time",
                  "location", "state", "evidence", "limitations"):
            if k not in e:
                err(f"{eid}: missing key {k}")
        if e["kind"] not in KINDS:
            err(f"{eid}: bad kind {e['kind']}")
        if e["status"] not in ("observed", "inferred"):
            err(f"{eid}: bad status")
        t = e["time"]
        if t["clock_id"] not in ("cam1-pts", "cam2-pts"):
            err(f"{eid}: bad clock")
        if not (isinstance(t["start_ms"], int)
                and isinstance(t["end_ms"], int)
                and t["start_ms"] <= t["end_ms"]):
            err(f"{eid}: bad ms interval")
        ev = e["evidence"]
        cam = ev["camera"]
        if cam not in DIMS:
            err(f"{eid}: bad camera")
            continue
        want_h = CAM1H if cam == "CAM1" else CAM2H
        if ev["source_sha256"] != want_h:
            err(f"{eid}: bad source hash")
        if e["status"] == "observed" and not ev["frames"]:
            err(f"{eid}: observed without frames")
        if e["status"] == "inferred" and not ev["basis_event_ids"]:
            err(f"{eid}: inferred without basis")
        for b in ev["basis_event_ids"]:
            if b not in idset:
                err(f"{eid}: unknown basis {b}")
        vdump = v1 if cam == "CAM1" else v2
        for f in ev["frames"]:
            fr, pts = f["frame"], f["pts_seconds"]
            if not (0 <= fr < len(vdump)):
                err(f"{eid}: frame {fr} out of range")
                continue
            if abs(vdump[fr] - pts) > 0.001:
                err(f"{eid}: pts {pts} != dump {vdump[fr]} for frame {fr}")
            if (cam, fr) not in logged:
                err(f"{eid}: frame {fr} has no extraction log entry")
            elif abs(logged[(cam, fr)] - pts) > 0.001:
                err(f"{eid}: log pts mismatch frame {fr}")
            if f["role"] not in ("primary", "before", "after", "context"):
                err(f"{eid}: bad role")
        loc = e["location"]
        if loc is not None:
            w, hh = DIMS[loc["camera"]]
            if not (0 <= loc["x"] <= w and 0 <= loc["y"] <= hh):
                err(f"{eid}: location outside frame")
            if loc["uncertainty_px"] < 0:
                err(f"{eid}: negative uncertainty")
            if loc["camera"] != cam:
                err(f"{eid}: location camera != evidence camera")
        st = e["state"]
        k = e["kind"]
        if k == "tool_interaction":
            if st["interaction"] not in ("engagement", "loosening", "lift",
                                         "touch", "reposition", "unknown"):
                err(f"{eid}: bad interaction")
            if not isinstance(st["tip_visible"], bool):
                err(f"{eid}: tip_visible not bool")
            if st["contact"] not in ("observed", "unknown"):
                err(f"{eid}: bad contact")
        elif k == "source_state":
            if st["presence"] not in ("present", "absent", "unknown"):
                err(f"{eid}: bad presence")
        elif k == "tray_state":
            if st["delta"] not in ("added", "removed", "unchanged",
                                   "unknown"):
                err(f"{eid}: bad delta")
        elif k == "visibility_gap":
            if st["cause"] not in ("hand", "tool", "object", "out_of_frame",
                                   "unknown"):
                err(f"{eid}: bad cause")
        if not isinstance(e["limitations"], list):
            err(f"{eid}: limitations not list")
    print(f"checked {len(evs)} events, {len(ERRS)} errors")
    return 1 if ERRS else 0


if __name__ == "__main__":
    sys.exit(main())
