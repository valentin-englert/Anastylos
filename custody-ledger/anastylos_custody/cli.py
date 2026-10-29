"""Command line for custody-ledger: observe, score, link, export-pairs, bind."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = HERE / "custody-config.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_observe(args) -> int:
    from anastylos_custody.config import load_custody_config
    from anastylos_custody.session import observe_video

    custody, harness, kit, provenance = load_custody_config(args.config)
    doc = observe_video(Path(args.video), Path(args.inventory) if args.inventory else None, custody, harness, kit, provenance, tuple(args.trace))
    out = Path(args.out) / "events.json"
    _write_json(out, doc)
    changes = [event for event in doc["events"] if event["kind"] != "reference_set"]
    drift = doc["camera_drift"]
    print(f"{len(changes)} events, {doc['sampling']['samples']} samples, {doc['processing_s']} s -> {out}")
    print(f"camera drift: {drift['accepted']}/{drift['fits']} fits accepted, max drift {drift['max_drift_px']} px, max residual {drift['max_residual_rms_px']} px")
    return 0


def cmd_discover(args) -> int:
    from anastylos_custody.config import load_custody_config
    from anastylos_custody.proposals import DEFAULTS, discover_video

    custody, harness, kit, provenance = load_custody_config(args.config)
    registry, inventory = discover_video(Path(args.video), custody, harness, kit, provenance, dict(DEFAULTS))
    _write_json(Path(args.out) / "registry.json", registry)
    _write_json(Path(args.out) / "inventory.json", inventory)
    print(f"{registry['proposal_count']} proposals -> {len(registry['sources'])} sources -> {args.out}")
    for source in registry["sources"]:
        print(source["id"], source["geometry"]["centroid_mm"], source["first_change"], source["confidence"])
    return 0


def cmd_score(args) -> int:
    from anastylos_custody.scoring import score

    report = score(_read_json(args.events), _read_json(args.gt), args.tolerance_s)
    _write_json(Path(args.out), report)
    for name in ("u2_tray_cells", "u1_device_sites"):
        part = report[name]
        print(f"{name}: {part['detected_true']}/{part['certain_gt']} certain events, {len(part['false_positives'])} false positives, missed {part['missed']}")
    return 0


def cmd_link(args) -> int:
    from anastylos_custody.linker import link

    declared = _read_json(args.declared_removals) if args.declared_removals else None
    externals = _read_json(args.declared_deposits) if args.declared_deposits else None
    rects = ({site["id"]: site["rect_mm"] for site in _read_json(args.inventory)["sites"]} if args.inventory else None)
    doc = link(_read_json(args.events), declared, externals, rects)
    doc["inputs"] = {"events": str(args.events), "events_sha256": _sha256(args.events),
                     "declared_removals": None if declared is None else str(args.declared_removals),
                     "declared_removals_sha256": None if declared is None else _sha256(args.declared_removals),
                     "declared_deposits": None if externals is None else str(args.declared_deposits),
                     "declared_deposits_sha256": None if externals is None else _sha256(args.declared_deposits),
                     "inventory": None if rects is None else str(args.inventory),
                     "inventory_sha256": None if rects is None else _sha256(args.inventory)}
    _write_json(Path(args.out), doc)
    for proposal in doc["proposals"]:
        deposit = proposal["deposit"]
        print(f"{proposal['id']} {deposit['zone']:8} {deposit['id']} [{deposit['t_before_s']}, {deposit['t_after_s']}] -> {proposal['status']}")
    return 0


def cmd_export_pairs(args) -> int:
    from anastylos_custody.config import load_custody_config
    from anastylos_custody.evidence import export_pairs
    from anastylos_custody.sheet import SheetReader

    custody, harness, kit, _provenance = load_custody_config(args.config)
    events_doc = _read_json(args.events)
    if args.declared_removals:
        from anastylos_custody.evidence import declared_as_events
        from anastylos_custody.linker import DECLARED_SCHEMA

        declared = _read_json(args.declared_removals)
        if declared.get("schema") != DECLARED_SCHEMA:
            raise SystemExit(f"declared removals are not {DECLARED_SCHEMA}")
        events_doc["events"] += declared_as_events(declared, events_doc["provenance"]["video"]["fps"], args.declared_search_s)
    doc = export_pairs(Path(args.video), events_doc, args.event_ids, SheetReader(harness, kit, custody["view"]), Path(args.out))
    for record in doc["pairs"]:
        sides = {side: (record[side] or {}).get("frame_index") for side in ("before", "after")}
        print(f"{record['pair_id']}: before frame {sides['before']}, after frame {sides['after']}")
    return 0


def cmd_bind(args) -> int:
    from anastylos_custody.config import load_custody_config
    from anastylos_custody.evidence import bind_regions
    from anastylos_custody.sheet import SheetReader
    from anastylos_custody.zones import load_inventory, tray_cells

    custody, harness, kit, _provenance = load_custody_config(args.config)
    zones = tray_cells(SheetReader(harness, kit, custody["view"]).zones())
    if args.inventory:
        zones += load_inventory(Path(args.inventory), custody["view"])[0]
    doc = bind_regions(Path(args.results), _read_json(args.keyframes), {zone.id: list(zone.rect_mm) for zone in zones}, args.tolerance_mm)
    _write_json(Path(args.out), doc)
    for binding in doc["bindings"]:
        labels = [item["region"] for item in binding.get("regions", [])]
        print(f"{binding['pair_id']}: {binding['status']} {labels}")
    return 0


def _sha256(path: Path) -> str:
    from anastylos_diff.config import sha256_file

    return sha256_file(Path(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="custody", description="Event-sourced part custody from a fixed overhead camera")
    sub = parser.add_subparsers(dest="command", required=True)
    observe = sub.add_parser("observe", help="watch tray cells and inventoried sites; write events.json")
    observe.add_argument("--video", required=True)
    observe.add_argument("--inventory", default=None, help="custody-site-inventory@1 JSON (optional)")
    observe.add_argument("--out", required=True)
    observe.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    observe.add_argument("--trace", nargs="*", default=[], help="zone ids to trace per sample (diagnostics)")
    observe.set_defaults(handler=cmd_observe)
    discover = sub.add_parser("discover", help="adaptive pass 1: propose sources without an inventory")
    discover.add_argument("--video", required=True)
    discover.add_argument("--out", required=True)
    discover.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    discover.set_defaults(handler=cmd_discover)
    scoring = sub.add_parser("score", help="score events.json against annotated ground truth")
    scoring.add_argument("--events", type=Path, required=True)
    scoring.add_argument("--gt", type=Path, required=True)
    scoring.add_argument("--out", type=Path, required=True)
    scoring.add_argument("--tolerance-s", type=float, default=2.0)
    scoring.set_defaults(handler=cmd_score)
    linking = sub.add_parser("link", help="propose removal -> deposit custody links (never writes memory)")
    linking.add_argument("--events", type=Path, required=True)
    linking.add_argument("--declared-removals", type=Path, default=None, help="custody-declared-removals@1 JSON (operator-declared sources)")
    linking.add_argument("--declared-deposits", type=Path, default=None, help="custody-declared-deposits@1 JSON (operator-declared deposits outside the watched cells)")
    linking.add_argument("--inventory", type=Path, default=None, help="site inventory used by observe; enables grouping of removals (linker 1.3)")
    linking.add_argument("--out", type=Path, required=True)
    linking.set_defaults(handler=cmd_link)
    export = sub.add_parser("export-pairs", help="write full-resolution keyframe pairs for change-detection")
    export.add_argument("--video", required=True)
    export.add_argument("--events", type=Path, required=True)
    export.add_argument("--event-ids", nargs="+", required=True)
    export.add_argument("--declared-removals", type=Path, default=None, help="also allow ids from operator-declared removals")
    export.add_argument("--declared-search-s", type=float, default=1.0, help="how far keyframe candidates may move away from declared times")
    export.add_argument("--out", required=True)
    export.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    export.set_defaults(handler=cmd_export_pairs)
    bind = sub.add_parser("bind", help="map change-detection regions of exported pairs back to their zones")
    bind.add_argument("--results", required=True, help="change-detection output dir with <pair>/result.json")
    bind.add_argument("--keyframes", type=Path, required=True)
    bind.add_argument("--inventory", default=None)
    bind.add_argument("--out", type=Path, required=True)
    bind.add_argument("--tolerance-mm", type=float, default=5.0)
    bind.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    bind.set_defaults(handler=cmd_bind)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
