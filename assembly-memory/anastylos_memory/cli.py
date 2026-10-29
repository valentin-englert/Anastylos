"""CLI: `record`/`import` append events, `where` answers "where does this go?"."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anastylos_memory import query as queries
from anastylos_memory.importer import evidence_key, load_result, observations_from_result
from anastylos_memory.store import (
    append_event,
    evidence_ref,
    load_events,
    next_id,
    sha256_file,
    successors,
    utc_now,
    validate_compatible_group,
    validate_declared_removal,
    validate_observation,
    validate_phase,
    validate_storage_link,
    validate_tray_link,
)

HERE = Path(__file__).resolve().parents[1]
DEFAULT_KIT = HERE.parent / "calibration" / "calibration-config.json"
STORE_NAME = "memory.jsonl"


def resolve_store(session_dir: str | None, store: str | None) -> Path:
    if store:
        return Path(store)
    return Path(session_dir or ".") / STORE_NAME


def default_session(store_path: Path) -> str:
    name = store_path.parent.resolve().name
    return name or "session"


def _numbers(text: str, count: int, flag: str) -> list[float]:
    try:
        values = [float(item) for item in text.split(",")]
    except ValueError:
        raise SystemExit(f"{flag} must be {count} comma-separated numbers, got {text!r}")
    if len(values) != count:
        raise SystemExit(f"{flag} must be {count} comma-separated numbers, got {text!r}")
    return values


def cmd_record_observation(args) -> int:
    store_path = resolve_store(args.session_dir, args.store)
    events = load_events(store_path)
    event = {
        "type": "observation",
        "id": next_id(events, "obs"),
        "session": args.session or default_session(store_path),
        "timestamp": utc_now(),
        "source_view": args.view,
        "bbox_mm": _numbers(args.bbox, 4, "--bbox"),
        "centroid_mm": _numbers(args.centroid, 2, "--centroid"),
        "evidence": evidence_ref(args.result_json, args.pair_id, args.region),
        "kit_sha256": sha256_file(args.kit) if args.kit else None,
        "note": args.note,
        "supersedes": args.supersedes,
    }
    validate_observation(event, {item["id"] for item in events})
    append_event(store_path, event)
    print(json.dumps(event, indent=2))
    return 0


def cmd_record_tray_link(args) -> int:
    store_path = resolve_store(args.session_dir, args.store)
    events = load_events(store_path)
    kit, kit_sha = queries.load_kit(args.kit)
    tray = queries.resolve_tray(kit, args.sheet, args.tray)
    if args.evidence_result_json:
        basis: dict = {
            "kind": "evidence",
            **evidence_ref(args.evidence_result_json, args.evidence_pair_id, args.evidence_region),
        }
    else:
        basis = {"kind": "human", "confirmed_by": args.confirmed_by, "note": args.note}
    event = {
        "type": "tray_link",
        "id": next_id(events, "link"),
        "session": args.session or default_session(store_path),
        "timestamp": utc_now(),
        "observation_id": args.observation,
        "tray": tray,
        "basis": basis,
        "kit_sha256": kit_sha,
        "supersedes": args.supersedes,
    }
    validate_tray_link(event, {item["id"] for item in events}, successors(events))
    append_event(store_path, event)
    print(json.dumps(event, indent=2))
    return 0


def _append_declared(args, prefix: str, body: dict, validate) -> int:
    store_path = resolve_store(args.session_dir, args.store)
    events = load_events(store_path)
    event = {"type": body.pop("type"), "id": next_id(events, prefix),
             "session": args.session or default_session(store_path), "timestamp": utc_now(), **body,
             "supersedes": args.supersedes}
    validate(event, {item["id"]: item for item in events})
    append_event(store_path, event)
    print(json.dumps(event, indent=2, ensure_ascii=False))
    return 0


def cmd_record_phase(args) -> int:
    return _append_declared(args, "phase", {"type": "phase", "label": args.label, "pose_note": args.pose_note,
                                            "starts_after": args.starts_after}, validate_phase)


def cmd_record_declared_removal(args) -> int:
    return _append_declared(args, "dec", {
        "type": "declared_removal", "operator_label": args.label, "source": args.source, "phase": args.phase,
        "t_s": args.t_s, "observation_id": args.observation, "declared_by": args.declared_by,
        "evidence_note": args.note}, validate_declared_removal)


def cmd_record_storage_link(args) -> int:
    return _append_declared(args, "store", {
        "type": "storage_link", "target_id": args.target, "location": args.location,
        "basis": {"kind": "human", "confirmed_by": args.confirmed_by, "note": args.note}}, validate_storage_link)


def cmd_record_group(args) -> int:
    if args.measurement:
        basis = {"kind": "measured", "measurement": json.loads(args.measurement), "measured_by": args.declared_by}
    else:
        basis = {"kind": "operator_statement", "declared_by": args.declared_by, "note": args.note}
    return _append_declared(args, "grp", {"type": "compatible_group", "members": args.members.split(","),
                                          "label": args.label, "basis": basis}, validate_compatible_group)


def cmd_import(args) -> int:
    store_path = resolve_store(args.session_dir, args.store)
    events = load_events(store_path)
    result = load_result(args.result_json)
    pending = observations_from_result(
        result,
        args.result_json,
        args.session or default_session(store_path),
        region=args.region,
        kit_sha256=sha256_file(args.kit) if args.kit else None,
        note=args.note,
        t_s=args.t_s,
    )
    imported = []
    skipped = []
    known_ids = {item["id"] for item in events}
    by_evidence = {
        evidence_key(item["evidence"]): item["id"]
        for item in events
        if item["type"] == "observation" and isinstance(item.get("evidence"), dict)
    }
    for event in pending:
        duplicate_of = by_evidence.get(evidence_key(event["evidence"]))
        if duplicate_of is not None:
            skipped.append(duplicate_of)
            continue
        event["id"] = next_id(events, "obs")
        event["timestamp"] = utc_now()
        validate_observation(event, known_ids)
        append_event(store_path, event)
        events.append(event)
        known_ids.add(event["id"])
        by_evidence[evidence_key(event["evidence"])] = event["id"]
        imported.append(event)
    print(json.dumps({
        "pair_id": result.get("pair_id"),
        "status": result.get("status"),
        "imported": imported,
        "skipped": skipped,
    }, indent=2))
    return 0


def cmd_where(args) -> int:
    store_path = resolve_store(args.session_dir, args.store)
    events = load_events(store_path)
    if args.observation:
        answer = queries.where_observation(events, args.observation)
    else:
        kit, _sha = queries.load_kit(args.kit)
        answer = queries.where_tray(events, kit, args.sheet, args.tray)
    print(json.dumps(answer, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory", description="ANASTYLOS assembly memory core")
    parser.add_argument("--session-dir", default=None, help="session folder holding memory.jsonl")
    parser.add_argument("--store", default=None, help="event log path (overrides --session-dir)")
    parser.add_argument("--session", default=None, help="session name (defaults to the folder name)")
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="append an event")
    record_sub = record.add_subparsers(dest="record", required=True)

    obs = record_sub.add_parser("observation", help="record a removal observation")
    obs.add_argument("--view", required=True, help="source view, e.g. fixture_back")
    obs.add_argument("--bbox", required=True, help="x,y,w,h in mm, e.g. 52.0,40.5,6,6")
    obs.add_argument("--centroid", required=True, help="x,y in mm")
    obs.add_argument("--result-json", required=True, help="change-detection result.json")
    obs.add_argument("--pair-id", required=True)
    obs.add_argument("--region", required=True, help="region label, e.g. R1")
    obs.add_argument("--kit", default=None, help="kit config (records its sha256 when given)")
    obs.add_argument("--note", default=None)
    obs.add_argument("--supersedes", default=None, help="earlier observation id this corrects")
    obs.set_defaults(func=cmd_record_observation)

    link = record_sub.add_parser("tray-link", help="link an observation to a tray")
    link.add_argument("--observation", required=True, help="observation id, e.g. obs-0001")
    link.add_argument("--sheet", required=True, help="tray sheet, e.g. surface_a3")
    link.add_argument("--tray", required=True, type=int, help="tray cell number")
    link.add_argument("--kit", default=str(DEFAULT_KIT), help="kit config for tray lookup")
    link.add_argument("--evidence-result-json", default=None)
    link.add_argument("--evidence-pair-id", default=None)
    link.add_argument("--evidence-region", default=None)
    link.add_argument("--confirmed-by", default=None, help="human confirmer")
    link.add_argument("--note", default=None)
    link.add_argument("--supersedes", default=None, help="earlier link id this corrects")
    link.set_defaults(func=cmd_record_tray_link)

    phase = record_sub.add_parser("phase", help="start a pose phase (orders a complete disassembly)")
    phase.add_argument("--label", required=True)
    phase.add_argument("--pose-note", default=None)
    phase.add_argument("--starts-after", default=None, help="previous phase id")
    phase.add_argument("--supersedes", default=None)
    phase.set_defaults(func=cmd_record_phase)

    dec = record_sub.add_parser("declared-removal", help="operator-declared removal without image evidence")
    dec.add_argument("--label", required=True, help="the operator's own words for the part")
    dec.add_argument("--source", required=True, help="where it came from, in the operator's words or a site id")
    dec.add_argument("--phase", required=True, help="phase id")
    dec.add_argument("--declared-by", required=True)
    dec.add_argument("--t-s", type=float, default=None, help="video time, if known")
    dec.add_argument("--observation", default=None, help="automatic observation this declaration refers to")
    dec.add_argument("--note", default=None, help="evidence note (e.g. witness video)")
    dec.add_argument("--supersedes", default=None)
    dec.set_defaults(func=cmd_record_declared_removal)

    store = record_sub.add_parser("storage-link", help="where a removed part is stored (cell or any other place)")
    store.add_argument("--target", required=True, help="observation or declared_removal id")
    store.add_argument("--location", required=True, help="cell-N or free text such as OUT-A")
    store.add_argument("--confirmed-by", required=True)
    store.add_argument("--note", default=None)
    store.add_argument("--supersedes", default=None)
    store.set_defaults(func=cmd_record_storage_link)

    grp = record_sub.add_parser("group", help="interchangeable parts (no per-part identity)")
    grp.add_argument("--members", required=True, help="comma-separated observation/declared_removal ids")
    grp.add_argument("--label", default=None)
    grp.add_argument("--declared-by", required=True)
    grp.add_argument("--measurement", default=None, help="JSON object; makes the basis 'measured'")
    grp.add_argument("--note", default=None)
    grp.add_argument("--supersedes", default=None)
    grp.set_defaults(func=cmd_record_group)

    imp = sub.add_parser("import", help="import a change-detection result.json as observations")
    imp.add_argument("--result-json", required=True, help="change-detection result.json")
    imp.add_argument("--region", default=None, help="import one region label only, e.g. R1")
    imp.add_argument("--kit", default=None, help="kit config (records its sha256 when given)")
    imp.add_argument("--note", default=None)
    imp.add_argument("--t-s", type=float, default=None, help="video time of the removal, if known (orders install steps)")
    imp.set_defaults(func=cmd_import)

    where = sub.add_parser("where", help='"where does this go?" (original source location)')
    target = where.add_mutually_exclusive_group(required=True)
    target.add_argument("--observation", default=None, help="observation id")
    target.add_argument("--tray", default=None, type=int, help="tray cell number")
    where.add_argument("--sheet", default="surface_a3", help="tray sheet (with --tray)")
    where.add_argument("--kit", default=str(DEFAULT_KIT), help="kit config (with --tray)")
    where.set_defaults(func=cmd_where)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "record", None) == "tray-link":
        has_evidence = bool(args.evidence_result_json or args.evidence_pair_id or args.evidence_region)
        if has_evidence and not (args.evidence_result_json and args.evidence_pair_id and args.evidence_region):
            raise SystemExit("tray-link evidence basis needs all of --evidence-result-json, --evidence-pair-id, --evidence-region")
        if has_evidence and args.confirmed_by:
            raise SystemExit("tray-link takes one basis: evidence or --confirmed-by, not both")
        if not has_evidence and not args.confirmed_by:
            raise SystemExit("tray-link needs evidence or --confirmed-by; unsupported links are rejected")
    return args.func(args)
