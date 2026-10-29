"""Run zone observation over one fixed-camera video and collect events with provenance."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from anastylos_diff.config import sha256_file
from anastylos_custody import footage
from anastylos_custody.device_alignment import AlignmentLimits, DevicePose
from anastylos_custody.observer import OBSERVER_VERSION, ObserverParams, ZoneGeometry, ZoneObserver
from anastylos_custody.sheet import DriftTracker, Rectifier, SheetReader, find_registration
from anastylos_custody.signals import SignalTracker
from anastylos_custody.zones import Zone, apply_tray_occupancy, grow, load_inventory, tray_cells

EVENTS_SCHEMA = "custody-events@1"


def observe_video(video: Path, inventory: Path | None, custody: dict, harness: dict, kit: dict, provenance: dict,
                  trace_zones: tuple[str, ...] = ()) -> dict:
    started = time.perf_counter()
    step = int(custody["sampling"]["step_frames"])
    reader = SheetReader(harness, kit, custody["view"])
    registration = find_registration(reader, footage.sample_frames(video, step), float(custody["session_registration"]["search_window_s"]))
    rectifier = Rectifier(registration.homography_image_to_mm, reader.page_rect_mm(), custody["rectified"]["px_per_mm"], custody["rectified"]["margin_mm"])

    zones = tray_cells(reader.zones())
    inventory_doc = None
    if inventory is not None:
        sites, inventory_doc = load_inventory(inventory, custody["view"])
        zones = apply_tray_occupancy(zones, inventory_doc, inventory) + sites
    params = _observer_params(custody, rectifier.ppm)
    device = _device_pose([zone for zone in zones if zone.kind == "site"], rectifier, custody)
    observers = [ZoneObserver(zone.id, zone.kind, _geometry(zone, rectifier, custody["zones"]), params, zone.valid_from_s,
                              device if zone.kind == "site" else None, initially_occupied=zone.initially_occupied)
                 for zone in zones]
    for observer in observers:
        if observer.zone_id in trace_zones:
            observer.trace = []
    tracker = SignalTracker(custody["signal"], custody["skin"], rectifier.ppm)

    drift_spec = custody["session_registration"]
    drift = DriftTracker(reader, registration.marker_corners_px, int(drift_spec["drift_min_markers"]), float(drift_spec["drift_max_residual_px"]))
    update_every = float(drift_spec["drift_update_every_s"])
    next_update = 0.0
    times = []
    for sample in footage.sample_frames(video, step):
        if sample.t_s >= next_update:
            drift.update(sample.t_s, sample.image)
            next_update = sample.t_s + update_every
        times.append(sample.t_s)
        signals = tracker.update(sample.t_s, rectifier.rectify(sample.image, drift.reference_to_current))
        for observer in observers:
            observer.observe(signals, sample.frame_index)

    events = _number(observer.events for observer in observers)
    return {
        "schema": EVENTS_SCHEMA,
        "provenance": {
            **provenance,
            "observer_version": OBSERVER_VERSION,
            "video": {**footage.describe(video), "sha256": footage.sha256_file(video)},
            "inventory_path": None if inventory is None else str(inventory),
            "inventory_sha256": None if inventory is None else sha256_file(inventory),
            "inventory_basis": None if inventory_doc is None else inventory_doc.get("basis"),
        },
        "registration": {"frame_index": registration.frame_index, "t_s": round(registration.t_s, 3), "report": registration.report},
        "rectified": {"px_per_mm": rectifier.ppm, "origin_frame_mm": [rectifier.x0, rectifier.y0], "size_px": list(rectifier.size)},
        "camera_drift": drift.report(),
        "sampling": _sampling_report(times, step, float(custody["sampling"]["max_gap_s"])),
        "zones": [observer.summary() for observer in observers],
        "device": None if device is None else {"region_px": list(device.region), "reference_t_s": device.reference_t_s, "realignments": device.realignments},
        "traces": {observer.zone_id: observer.trace for observer in observers if observer.trace is not None},
        "events": events,
        "processing_s": round(time.perf_counter() - started, 1),
    }


def _observer_params(custody: dict, ppm: float) -> ObserverParams:
    clean, content, skin = custody["clean"], custody["content"], custody["skin"]
    return ObserverParams(
        change_threshold=float(custody["signal"]["change_threshold"]),
        quiet_hold_s=float(clean["quiet_hold_s"]),
        max_motion_fraction=float(clean["max_motion_fraction"]),
        intrusion_min_area_px=float(skin["intrusion_min_area_mm2"]) * ppm ** 2,
        min_changed_area_px=float(content["min_changed_area_mm2"]) * ppm ** 2,
        confirm_hold_s=float(content["confirm_hold_s"]),
        max_anchor_changed_fraction=float(content["max_anchor_changed_fraction"]),
        ring_change_with_verified_pose=_ring_rule(content.get("ring_change_with_verified_pose", "pose_break")),
        cell_occupancy=_occupancy_rule(content.get("cell_occupancy", "start_relative")),
        print_stroke_px=int(round(float(content.get("cell_print_stroke_mm", 0.0)) * ppm)),
        min_part_area_px=float(content.get("min_part_area_mm2", 0.0)) * ppm ** 2,
    )


def _device_pose(sites: list[Zone], rectifier: Rectifier, custody: dict) -> DevicePose | None:
    """One rigid device per inventory: the union of its sites grown by the region margin."""
    if not sites:
        return None
    spec = custody["device_alignment"]
    boxes = [rectifier.rect_to_px(grow(site.rect_mm, float(spec["region_grow_mm"]))) for site in sites]
    region = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    sites_mask = np.zeros((region[3] - region[1], region[2] - region[0]), bool)
    for site in sites:
        sites_mask |= _box_mask(rectifier.rect_to_px(grow(site.rect_mm, float(custody["zones"]["anchor_inner_mm"]))), region)
    limits = AlignmentLimits(float(spec["max_shift_mm"]) * rectifier.ppm, float(spec["max_rotation_deg"]), float(spec["min_correlation"]))
    return DevicePose(region, sites_mask, limits)


def _geometry(zone: Zone, rectifier: Rectifier, spec: dict) -> ZoneGeometry:
    content = rectifier.rect_to_px(grow(zone.rect_mm, -float(spec["content_shrink_mm"])))
    guard = rectifier.rect_to_px(grow(zone.rect_mm, float(spec["intrusion_margin_mm"])))
    context = rectifier.rect_to_px(grow(zone.rect_mm, float(spec["context_grow_mm"])))
    if zone.kind != "site":
        return ZoneGeometry(content, guard, context)
    inner = float(spec["anchor_inner_mm"])
    sites = np.zeros_like(_box_mask(context, context))
    for rect in (zone.rect_mm, *zone.excluded_from_anchor):
        sites |= _box_mask(rectifier.rect_to_px(grow(rect, inner)), context)
    ring = _box_mask(rectifier.rect_to_px(grow(zone.rect_mm, float(spec["anchor_outer_mm"]))), context) & ~sites
    return ZoneGeometry(content, guard, context, ring)


def _box_mask(box: tuple[int, int, int, int], window: tuple[int, int, int, int]) -> np.ndarray:
    mask = np.zeros((window[3] - window[1], window[2] - window[0]), bool)
    x0, y0, x1, y1 = box
    mask[max(0, y0 - window[1]):max(0, y1 - window[1]), max(0, x0 - window[0]):max(0, x1 - window[0])] = True
    return mask


def _number(event_lists) -> list[dict]:
    events = [event for events in event_lists for event in events]
    events.sort(key=lambda event: (event.get("t_after_s", event.get("t_s")), event["zone"]))
    return [{"id": f"E-{index:04d}", **event} for index, event in enumerate(events, 1)]


def _sampling_report(times: list[float], step: int, max_gap_s: float) -> dict:
    gaps = [(a, b) for a, b in zip(times, times[1:]) if b - a > max_gap_s]
    return {
        "step_frames": step,
        "samples": len(times),
        "first_t_s": round(times[0], 3) if times else None,
        "last_t_s": round(times[-1], 3) if times else None,
        "max_interval_s": round(max((b - a for a, b in zip(times, times[1:])), default=0.0), 3),
        "gaps_over_max": [[round(a, 3), round(b, 3)] for a, b in gaps],
    }


def _ring_rule(value: str) -> str:
    if value not in ("pose_break", "content"):
        raise SystemExit(f"content.ring_change_with_verified_pose must be 'pose_break' or 'content', not {value!r}")
    return value


def _occupancy_rule(value: str) -> str:
    if value not in ("start_relative", "paper_model"):
        raise SystemExit(f"content.cell_occupancy must be 'start_relative' or 'paper_model', not {value!r}")
    return value
