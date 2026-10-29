"""One command from a phase video to Assembly Memory, numbered parts and open questions.

Runs the frozen custody pipeline (discover, observe, link) from a custody
worktree, then writes every *uniquely linked, automatic* part into Assembly
Memory with change-detection evidence for both ends (removal observation and
evidence-based tray link). Everything the evidence leaves open goes to
``questions.json`` and never into memory. Each run appends one ``phase`` to
the memory session, so phases of one disassembly share one log.

    python tools/session/session.py --video P1.mp4 --phase P1 --custody-dir <worktree>/custody-ledger \
        --config custody-config.v15.json --memory <session-dir> --out <run-dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from parts import LINKED, number_parts

REPO = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
# Child Pythons print in the console code page unless told otherwise.
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}
HARNESS_SHA = "a2308f66af38385a425e8839abd15c1e5253ffb9728c2d7ff57bb1fb15885812"


def _run(args: list, cwd: Path | None = None) -> str:
    done = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=CHILD_ENV)
    if done.returncode != 0:
        raise SystemExit(f"step failed: {' '.join(map(str, args[:3]))} …\n{done.stdout}\n{done.stderr}")
    return done.stdout


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _memory(session: Path, *argv) -> dict:
    return json.loads(_run([PYTHON, REPO / "assembly-memory" / "memory.py", "--session-dir", session, *argv]))


def _last_phase(session: Path) -> str | None:
    log = session / "memory.jsonl"
    if not log.exists():
        return None
    phases = [json.loads(line)["id"] for line in log.read_text(encoding="utf-8").splitlines()
              if line.strip() and json.loads(line)["type"] == "phase"]
    return phases[-1] if phases else None


def _best_region(binding: dict) -> str | None:
    """Region with the largest overlap; a grouped removal may overlap several change regions (``several_regions``)."""
    regions = binding.get("regions") or []
    if binding.get("status") not in ("bound", "several_regions") or not regions:
        return None
    return max(regions, key=lambda r: r.get("overlap_mm2", 0))["region"]


def source_precision(region_bbox: list[float], features: list[dict], part_area_mm2: float | None = None,
                     margin_mm: float = 1.0) -> dict:
    """Precision of the written source region.

    coarse: the region covers two or more candidate features *and* is more than twice the deposited part's
    area (the part could have come from several of them, like a screw in a region over two holes), or the
    part's area is unknown; a part about as large as the region simply covers several features itself.
    """
    x, y, w, h = region_bbox
    inside = [f for f in features if x - margin_mm <= f["centre_mm"][0] <= x + w + margin_mm
              and y - margin_mm <= f["centre_mm"][1] <= y + h + margin_mm]
    region_area = w * h
    part_smaller = part_area_mm2 is None or region_area > 2 * part_area_mm2
    level = "coarse" if len(inside) >= 2 and part_smaller else "specific" if inside else "no_feature"
    note = (f"source precision {level}: covers candidate features "
            + ", ".join(f"{f['id']} at {f['centre_mm'][0]:.0f}/{f['centre_mm'][1]:.0f} mm" for f in inside)
            if inside else "source precision no_feature: no candidate feature inside the region")
    if level == "coarse":
        note += "; never state one of them as the source"
    note += f" (region {region_area:.0f} mm², deposited part {'unknown' if part_area_mm2 is None else f'{part_area_mm2:.0f} mm²'})"
    return {"precision": level, "candidate_features": [f["id"] for f in inside], "note": note}


def run_pipeline(video: Path, custody_dir: Path, config: str, out: Path) -> dict:
    cli = [PYTHON, custody_dir / "custody.py"]
    _run([*cli, "discover", "--config", config, "--video", video, "--out", out], cwd=custody_dir)
    _run([*cli, "observe", "--config", config, "--video", video, "--inventory", out / "inventory.json",
          "--out", out / "pass2"], cwd=custody_dir)
    _run([*cli, "link", "--events", out / "pass2" / "events.json", "--inventory", out / "inventory.json",
          "--out", out / "pass2" / "custody.json"], cwd=custody_dir)
    return _json(out / "pass2" / "custody.json")


def export_evidence(video: Path, custody_dir: Path, out: Path, event_ids: list[str]) -> dict[str, dict]:
    """Keyframe pairs -> change-detection -> bindings, for the given events; returns bindings by event id."""
    if not event_ids:
        return {}
    cli = [PYTHON, custody_dir / "custody.py"]
    evidence = out / "evidence"
    _run([*cli, "export-pairs", "--video", video, "--events", out / "pass2" / "events.json",
          "--event-ids", *event_ids, "--out", evidence], cwd=custody_dir)
    _run([PYTHON, custody_dir.parent / "change-detection" / "detect.py", "run", "--input", evidence / "pairs",
          "--out", evidence / "results", "--view", "surface_a3", "--manifest", evidence / "pairs" / "manifest.csv",
          "--require-config-sha256", HARNESS_SHA], cwd=custody_dir)
    _run([*cli, "bind", "--results", evidence / "results", "--keyframes", evidence / "keyframes.json",
          "--inventory", out / "inventory.json", "--out", evidence / "bindings.json"], cwd=custody_dir)
    return {b["event"]: b for b in _json(evidence / "bindings.json")["bindings"]}


def write_memory(session: Path, phase_label: str, parts: list[dict], bindings: dict[str, dict], kit: Path,
                 features_of=lambda part: [], part_area_of=lambda part: None) -> dict:
    """Phase event, then for each automatic linked part an evidence-backed observation and tray link."""
    phase = _memory(session, "record", "phase", "--label", phase_label,
                    *(["--starts-after", _last_phase(session)] if _last_phase(session) else []))
    written = []
    for part in parts:
        if part["status"] not in LINKED or part["autonomy"] != "automatic":
            continue
        removal, deposit = bindings.get(part["removal_event"]), bindings.get(part["deposit_event"])
        source_region, cell_region = _best_region(removal or {}), _best_region(deposit or {})
        if not source_region or not cell_region:
            part["memory"] = {"status": "not written", "why": "no bound change-detection region for removal or deposit"}
            continue
        region = next(r for r in removal["regions"] if r["region"] == source_region)
        precision = source_precision(region["bbox_mm"], features_of(part), part_area_of(part))
        part["precision"] = precision
        imported = _memory(session, "import", "--result-json", removal["result_json"], "--region", source_region, "--kit", kit,
                           "--note", precision["note"], "--t-s", part["removal_window_s"][0])
        obs = (imported["imported"] or [{}])[0].get("id")
        if obs is None:
            part["memory"] = {"status": "not written", "why": "evidence already imported"}
            continue
        link = _memory(session, "record", "tray-link", "--observation", obs, "--sheet", "surface_a3",
                       "--tray", part["cell"].split("-")[1], "--kit", kit, "--evidence-result-json", deposit["result_json"],
                       "--evidence-pair-id", deposit["pair_id"], "--evidence-region", cell_region)
        part["memory"] = {"status": "written", "observation": obs, "tray_link": link["id"], "source_region": source_region,
                          "source_regions_overlapping": len(removal.get("regions") or [])}
        written.append(part["part"])
    return {"phase": phase["id"], "written": written}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--phase", required=True, help="short phase tag, e.g. P1")
    parser.add_argument("--phase-label", default=None, help="human label for the memory phase event")
    parser.add_argument("--custody-dir", required=True, type=Path, help="custody-ledger folder of the frozen worktree")
    parser.add_argument("--config", default="custody-config.v15.json")
    parser.add_argument("--memory", required=True, type=Path, help="assembly-memory session folder (appended)")
    parser.add_argument("--kit", type=Path, default=REPO / "calibration" / "calibration-config.json")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    custody = run_pipeline(args.video.resolve(), args.custody_dir.resolve(), args.config, args.out.resolve())
    registry = {s["id"]: s for s in _json(args.out / "registry.json")["sources"]}
    parts, questions = number_parts(custody, args.phase, registry)
    linked = [p for p in parts if p["status"] in LINKED and p["autonomy"] == "automatic"]
    events = sorted({e for p in linked for e in (p["removal_event"], p["deposit_event"])})
    bindings = export_evidence(args.video.resolve(), args.custody_dir.resolve(), args.out.resolve(), events)
    registry_doc = _json(args.out / "registry.json")
    epoch_features = {e["epoch"]: e.get("candidate_features", []) for e in registry_doc.get("epochs", [])}

    def features_of(part):
        epochs = {registry[s["site"]].get("epoch") for s in part.get("sources", []) if s["site"] in registry}
        return [f for epoch in epochs for f in epoch_features.get(epoch, [])]

    events_doc = _json(args.out / "pass2" / "events.json")
    ppm = events_doc["rectified"]["px_per_mm"]
    deposits = {e["id"]: e for e in events_doc["events"] if e.get("zone_kind") == "tray_cell"}

    def part_area_of(part):
        """Deposited part's area from the cell's grey or colour cue; None when only edges saw it."""
        cues = deposits.get(part["deposit_event"], {}).get("cues_after") or {}
        area_px = max(cues.get("grey_px", 0), cues.get("saturated_px", 0))
        return area_px / ppm ** 2 if area_px > 0 else None

    memory = write_memory(args.memory, args.phase_label or args.phase, parts, bindings, args.kit, features_of, part_area_of)

    provenance = _json(args.out / "pass2" / "events.json")["provenance"]
    report = {"schema": "anastylos-session-run@1", "phase": args.phase, "video": str(args.video),
              "video_sha256": provenance["video"]["sha256"], "observer_version": provenance.get("observer_version"),
              "counts": {"parts": len(parts), "linked_automatic": len(linked), "written_to_memory": len(memory["written"]),
                         "questions": len(questions)},
              "memory": {"session": str(args.memory), **memory}, "parts": parts}
    for name, document in (("parts.json", report), ("questions.json", {"schema": "anastylos-session-questions@1",
                                                                         "phase": args.phase, "questions": questions})):
        data = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        (args.out / name).write_bytes(data)
        print(f"{name} sha256 {hashlib.sha256(data).hexdigest()}")
    print(json.dumps(report["counts"]))


if __name__ == "__main__":
    main()
