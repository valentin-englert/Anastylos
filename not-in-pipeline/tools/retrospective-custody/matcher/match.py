"""Deposit-triggered retrospective source-to-tray matcher.

Consumes anastylos-observation-v1 events (plus an optional
anastylos-sync-v1 manifest) and emits anastylos-correspondence-v1
decisions: PROPOSED / AMBIGUOUS / UNKNOWN per tray addition, with
per-hypothesis SUPPORTED / INSUFFICIENT / REJECTED verdicts.

Standard library only. Never emits VERIFIED. Never writes to
Assembly Memory. Tool contact alone never proves removal; tray
addition alone never proves origin.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from validate import (
    GRASP_ACTIONS,
    ObservationError,
    load_observation_file,
    load_sync_manifest,
)

CORRESPONDENCE_SCHEMA = "anastylos-correspondence-v1"
ENGINE_NAME = "retrospective-custody-matcher"
ENGINE_VERSION = "1.0.0"
DIMENSION_TOLERANCE_MM = 0.5
DIMENSION_TOLERANCE_RATIO = 0.10


# ---------------------------------------------------------------------------
# Clock / interval reasoning
# ---------------------------------------------------------------------------

def _find_offset(
    offsets: list[dict[str, Any]], from_clock: str, to_clock: str
) -> tuple[float, float] | None:
    """Return (min, max) such that t_to = t_from + d, or None if unmapped."""
    for entry in offsets:
        if not entry["validated"]:
            continue
        if entry["from_clock"] == from_clock and entry["to_clock"] == to_clock:
            return (entry["offset_min_ms"], entry["offset_max_ms"])
        if entry["from_clock"] == to_clock and entry["to_clock"] == from_clock:
            return (-entry["offset_max_ms"], -entry["offset_min_ms"])
    return None


def map_interval(
    time: dict[str, Any], target_clock: str, offsets: list[dict[str, Any]]
) -> tuple[float, float] | None:
    """Map a time interval into the target clock. None when unmapped."""
    if time["clock_id"] == target_clock:
        return (time["start_ms"], time["end_ms"])
    window = _find_offset(offsets, time["clock_id"], target_clock)
    if window is None:
        return None
    low, high = window
    return (time["start_ms"] + low, time["end_ms"] + high)


def definitely_before(
    first: dict[str, Any],
    second: dict[str, Any],
    offsets: list[dict[str, Any]],
) -> bool | None:
    """True when first ends strictly before second starts.

    Returns None when the clocks are unmapped (order unknown).
    Touching boundaries do not count as ordered.
    """
    mapped = map_interval(first["time"], second["time"]["clock_id"], offsets)
    if mapped is None:
        return None
    return mapped[1] < second["time"]["start_ms"]


def possibly_overlaps(
    first: dict[str, Any],
    start_ms: float,
    end_ms: float,
    clock_id: str,
    offsets: list[dict[str, Any]],
) -> bool | None:
    """True when the event possibly overlaps [start_ms, end_ms) in clock_id."""
    mapped = map_interval(first["time"], clock_id, offsets)
    if mapped is None:
        return None
    return mapped[0] < end_ms and mapped[1] > start_ms


# ---------------------------------------------------------------------------
# Appearance
# ---------------------------------------------------------------------------

def _dimension_conflict(source_mm: float | None, deposit_mm: float | None) -> bool:
    if source_mm is None or deposit_mm is None:
        return False
    tolerance = max(DIMENSION_TOLERANCE_MM, DIMENSION_TOLERANCE_RATIO * source_mm)
    return abs(source_mm - deposit_mm) > tolerance


def appearance_verdict(
    source: dict[str, Any] | None, deposit: dict[str, Any] | None
) -> tuple[str | None, str | None]:
    """Compare stated appearance. Returns (reject_reason, note)."""
    if source is None or deposit is None:
        return None, "APPEARANCE_UNOBSERVED"
    source_class = source.get("component_class")
    deposit_class = deposit.get("component_class")
    if source_class and deposit_class and source_class != deposit_class:
        return "CLASS_MISMATCH", None
    for key in ("head_drive", "marks"):
        if source.get(key) and deposit.get(key) and source[key] != deposit[key]:
            return "APPEARANCE_MISMATCH", None
    for key in ("length_mm", "diameter_mm"):
        if _dimension_conflict(source.get(key), deposit.get(key)):
            return "DIMENSION_MISMATCH", None
    return None, None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class Matcher:
    def __init__(self, events: list[dict[str, Any]], offsets: list[dict[str, Any]]):
        self.events = events
        self.offsets = offsets
        self.by_id = {event["event_id"]: event for event in events}
        self.epoch_links = self._collect_epoch_links()
        self.sites = sorted({
            event["state"]["site_ref"]
            for event in events
            if event["kind"] == "source_state" and event["state"] is not None
        })

    def _collect_epoch_links(self) -> set[tuple[str, str, str]]:
        links: set[tuple[str, str, str]] = set()
        for event in self.events:
            if event["kind"] != "pose_change" or event["state"] is None:
                continue
            if event["status"] != "observed":
                continue
            for link in event["state"]["site_links"]:
                pair = (link["site_ref"], link["from_epoch"], link["to_epoch"])
                links.add(pair)
                links.add((link["site_ref"], link["to_epoch"], link["from_epoch"]))
        return links

    def _epochs_linked(self, site: str, first: str | None, second: str | None) -> bool:
        if first is None or second is None or first == second:
            return True
        return (site, first, second) in self.epoch_links

    @staticmethod
    def _epoch(event: dict[str, Any]) -> str | None:
        location = event.get("location")
        return location.get("pose_epoch") if location else None

    # -- hypothesis evaluation -----------------------------------------

    def _source_states(self, site: str) -> list[dict[str, Any]]:
        return [
            event
            for event in self.events
            if event["kind"] == "source_state"
            and event["state"] is not None
            and event["state"]["site_ref"] == site
        ]

    def _evaluate_hypothesis(
        self,
        site: str,
        deposit: dict[str, Any],
        attributed: dict[str, str],
        deposit_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Evaluate one (site, deposit) hypothesis."""
        cited = [deposit["event_id"]]
        clock = deposit["time"]["clock_id"]
        states = self._source_states(site)
        observed = [event for event in states if event["status"] == "observed"]
        verified = [
            event
            for event in observed
            if event["state"]["verified"]
            and event["state"]["presence"] in ("present", "absent", "loosened")
        ]
        if not states:
            return self._verdict(
                site, "INSUFFICIENT", ["NO_SOURCE_OBSERVATIONS"],
                cited, ["any observation of " + site],
            )

        # 1. A verified present/loosened definitely after the deposit
        #    contradicts removal for this deposit. An overlapping interval
        #    means order unknown, not "still present".
        cross_clock_present = False
        for event in verified:
            if event["state"]["presence"] not in ("present", "loosened"):
                continue
            order = definitely_before(event, deposit, self.offsets)
            if order is None:
                cross_clock_present = True
                continue
            if not order:
                deposited_first = definitely_before(deposit, event, self.offsets)
                if deposited_first:
                    reason = (
                        "SOURCE_NOT_EMPTIED_LOOSENED_ONLY"
                        if event["state"]["presence"] == "loosened"
                        else "SOURCE_STILL_PRESENT"
                    )
                    return self._verdict(
                        site, "REJECTED", [reason],
                        sorted(set(cited + [event["event_id"]])), [],
                    )
                return self._verdict(
                    site, "INSUFFICIENT", ["TEMPORAL_ORDER_UNVERIFIED"],
                    sorted(set(cited + [event["event_id"]])),
                    [f"tighter timestamps placing {site} presence relative to the deposit"],
                )
        if cross_clock_present and not any(
            definitely_before(event, deposit, self.offsets)
            for event in verified
            if event["state"]["presence"] in ("present", "loosened")
        ):
            return self._verdict(
                site, "INSUFFICIENT", ["CROSS_CAMERA_ORDER_UNVERIFIED"],
                cited, [f"validated sync placing {site} presence before the deposit"],
            )

        # 2. Latest verified present definitely before the deposit.
        presents = [
            event
            for event in verified
            if event["state"]["presence"] == "present"
            and definitely_before(event, deposit, self.offsets)
        ]
        if not presents:
            missing = [f"verified present observation of {site} before the deposit"]
            if any(
                event["state"]["presence"] == "present" and not event["state"]["verified"]
                for event in observed
            ):
                missing.append(f"verification of the unverified {site} presence")
            return self._verdict(
                site, "INSUFFICIENT", ["SOURCE_PRESENT_UNVERIFIED"], cited, missing,
            )
        present = max(
            presents,
            key=lambda e: map_interval(e["time"], clock, self.offsets)[1],
        )
        cited.append(present["event_id"])
        present_end = map_interval(present["time"], clock, self.offsets)[1]

        # 3. Verified absent after that present (delayed verification allowed).
        absents = []
        for event in verified:
            if event["state"]["presence"] != "absent":
                continue
            mapped = map_interval(event["time"], clock, self.offsets)
            if mapped is None:
                continue
            if mapped[0] > present_end:
                absents.append((mapped[0], event))
        if not absents:
            unverified_absent = any(
                event["state"]["presence"] == "absent" and not event["state"]["verified"]
                for event in observed
            ) or any(
                event["state"]["presence"] == "absent"
                for event in states
                if event["status"] == "inferred"
            )
            missing = [f"verified absent observation of {site} after the present state"]
            if unverified_absent:
                missing.append(f"verification of the unverified {site} absence")
            return self._verdict(
                site, "INSUFFICIENT", ["SOURCE_ABSENCE_UNVERIFIED"], sorted(set(cited)), missing,
            )
        absent = min(absents, key=lambda item: item[0])[1]
        cited.append(absent["event_id"])

        # 4. Epoch consistency between present and absent.
        if not self._epochs_linked(site, self._epoch(present), self._epoch(absent)):
            return self._verdict(
                site, "INSUFFICIENT", ["POSE_EPOCH_UNMAPPED"], sorted(set(cited)),
                [f"observer site link for {site} across the pose epochs"],
            )

        # 5. Consumption: one site supplies one part per restock cycle.
        if site in attributed:
            earlier = deposit_by_id[attributed[site]]
            restocked = any(
                event["state"]["presence"] == "present"
                and definitely_before(earlier, event, self.offsets)
                for event in verified
            )
            if not restocked:
                return self._verdict(
                    site, "REJECTED", ["SOURCE_ALREADY_ATTRIBUTED"],
                    sorted(set(cited + [attributed[site]])), [],
                )

        # 6. Interaction chain inside the transit window.
        window_end = deposit["time"]["end_ms"]
        supporting: list[dict[str, Any]] = []
        competing = 0
        cross_clock_interaction = False
        for event in self.events:
            if event["kind"] != "tool_interaction" or event["state"] is None:
                continue
            overlap = possibly_overlaps(event, present_end, window_end, clock, self.offsets)
            if overlap is None:
                cross_clock_interaction = True
                continue
            if not overlap:
                continue
            target = event["state"]["target_ref"]
            if target is not None and target != site:
                competing += 1
                continue
            supporting.append(event)
        if not supporting:
            if cross_clock_interaction and competing == 0:
                return self._verdict(
                    site, "INSUFFICIENT", ["CROSS_CAMERA_ORDER_UNVERIFIED"],
                    sorted(set(cited)), ["validated sync placing an interaction in the transit window"],
                )
            reasons = ["MISSING_INTERACTION"]
            if competing:
                reasons.append("ONLY_COMPETING_INTERACTIONS")
            return self._verdict(
                site, "INSUFFICIENT", reasons, sorted(set(cited)),
                ["tool/hand interaction overlapping the present-to-deposit window"],
            )
        cited.extend(event["event_id"] for event in supporting)

        # 7. Grasp continuity: distinct grasp actors need a handoff link.
        grasp_actors = sorted({
            event["state"]["actor"]
            for event in supporting
            if event["state"]["action"] in GRASP_ACTIONS
        })
        if len(grasp_actors) > 1:
            handoffs = [
                event for event in supporting if event["state"]["action"] == "handoff"
            ]
            covered = set()
            for event in handoffs:
                covered.add(event["state"]["handoff_from"])
                covered.add(event["state"]["handoff_to"])
            if not set(grasp_actors) <= covered:
                return self._verdict(
                    site, "INSUFFICIENT", ["HANDOFF_UNVERIFIED"], sorted(set(cited)),
                    ["handoff linking the grasp actors in the transit window"],
                )

        # 8. Appearance compatibility where stated on both sides.
        source_appearance = present["state"]["appearance"] or absent["state"]["appearance"]
        reject, note = appearance_verdict(source_appearance, deposit["state"]["appearance"])
        if reject:
            return self._verdict(
                site, "REJECTED", [reject], sorted(set(cited)), [],
            )
        notes = [note] if note else []
        if any(event["state"]["target_ref"] is None for event in supporting):
            notes.append("INTERACTION_LOCATION_UNATTRIBUTED")
        return self._verdict(site, "SUPPORTED", [], sorted(set(cited)), [], notes)

    @staticmethod
    def _verdict(
        site: str,
        verdict: str,
        reasons: list[str],
        cited: list[str],
        missing: list[str],
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "site_ref": site,
            "verdict": verdict,
            "reasons": reasons,
            "cited_event_ids": sorted(set(cited)),
            "missing_evidence": missing,
            "notes": notes or [],
        }

    # -- deposit decisions ---------------------------------------------

    def _gap_hits_window(
        self, deposit: dict[str, Any], site: str, window_start: float | None
    ) -> list[str]:
        """Return ids of material gaps overlapping the transit window."""
        clock = deposit["time"]["clock_id"]
        window_end = deposit["time"]["end_ms"]
        start = window_start if window_start is not None else deposit["time"]["start_ms"]
        hits = []
        for event in self.events:
            if event["kind"] != "visibility_gap" or event["state"] is None:
                continue
            if event["status"] != "observed":
                continue
            state = event["state"]
            scope = state["scope"]
            if scope == "source" and state["scope_ref"] != site:
                continue
            if scope == "tray" and state["scope_ref"] != deposit["state"]["tray_ref"]:
                continue
            if scope == "camera":
                deposit_camera = (deposit.get("location") or {}).get("camera_id")
                if state["scope_ref"] != deposit_camera:
                    continue
            overlap = possibly_overlaps(event, start, window_end, clock, self.offsets)
            if overlap:
                hits.append(event["event_id"])
        return hits

    def _find_superseding_removal(self, deposit: dict[str, Any]) -> dict[str, Any] | None:
        tray = deposit["state"]["tray_ref"]
        cell = deposit["state"]["cell_ref"]
        for event in self.events:
            if event["kind"] != "tray_state" or event["state"] is None:
                continue
            if event["status"] != "observed" or event["state"]["change"] != "removal":
                continue
            if not event["state"]["supported"]:
                continue
            if event["state"]["tray_ref"] != tray or event["state"]["cell_ref"] != cell:
                continue
            if definitely_before(deposit, event, self.offsets):
                return event
        return None

    def decide(self) -> dict[str, Any]:
        deposits = [
            event
            for event in self.events
            if event["kind"] == "tray_state"
            and event["state"] is not None
            and event["state"]["change"] == "addition"
        ]
        deposits.sort(key=lambda e: (e["time"]["start_ms"], e["time"]["end_ms"], e["event_id"]))
        deposit_by_id = {event["event_id"]: event for event in deposits}
        attributed: dict[str, str] = {}
        superseded: dict[str, str] = {}
        results = []

        for deposit in deposits:
            state = deposit["state"]
            if deposit["status"] != "observed" or not state["supported"]:
                reason = (
                    "DEPOSIT_INFERRED"
                    if deposit["status"] != "observed"
                    else "DEPOSIT_UNSUPPORTED"
                )
                results.append({
                    "deposit_event_id": deposit["event_id"],
                    "decision": "UNKNOWN",
                    "proposed_site_ref": None,
                    "ambiguity_set": [],
                    "reasons": [reason],
                    "cited_event_ids": [deposit["event_id"]],
                    "requires_confirmation": True,
                    "superseded_by": None,
                    "custody_chain": [],
                    "hypotheses": [],
                    "unresolved_alternatives": [f"deposit {reason.lower()}"],
                })
                continue

            # A superseded earlier deposit releases its attribution for
            # later deposits (temporary deposit followed by re-deposit).
            active_attributed = dict(attributed)
            chain: list[str] = []
            for site, earlier_id in attributed.items():
                removal_id = superseded.get(earlier_id)
                if removal_id is None:
                    continue
                removal = self.by_id[removal_id]
                if definitely_before(removal, deposit, self.offsets):
                    del active_attributed[site]
                    chain = [earlier_id, removal_id]

            hypotheses = [
                self._evaluate_hypothesis(site, deposit, active_attributed, deposit_by_id)
                for site in self.sites
            ]
            supported = [h for h in hypotheses if h["verdict"] == "SUPPORTED"]
            insufficient = [h for h in hypotheses if h["verdict"] == "INSUFFICIENT"]

            gap_hits: list[str] = []
            if len(supported) == 1:
                present_end = None
                for event_id in supported[0]["cited_event_ids"]:
                    event = self.by_id[event_id]
                    if (
                        event["kind"] == "source_state"
                        and event["state"] is not None
                        and event["state"]["presence"] == "present"
                    ):
                        mapped = map_interval(
                            event["time"], deposit["time"]["clock_id"], self.offsets
                        )
                        if mapped is not None:
                            present_end = max(present_end or mapped[1], mapped[1])
                gap_hits = self._gap_hits_window(deposit, supported[0]["site_ref"], present_end)

            removal = self._find_superseding_removal(deposit)
            superseded_by = removal["event_id"] if removal else None

            if len(supported) == 1 and not insufficient and not gap_hits:
                site = supported[0]["site_ref"]
                attributed[site] = deposit["event_id"]
                if superseded_by:
                    superseded[deposit["event_id"]] = superseded_by
                results.append({
                    "deposit_event_id": deposit["event_id"],
                    "decision": "PROPOSED",
                    "proposed_site_ref": site,
                    "ambiguity_set": [],
                    "reasons": [],
                    "cited_event_ids": sorted(set(
                        supported[0]["cited_event_ids"] + ([superseded_by] if superseded_by else [])
                    )),
                    "requires_confirmation": True,
                    "superseded_by": superseded_by,
                    "custody_chain": chain,
                    "hypotheses": hypotheses,
                    "unresolved_alternatives": [],
                })
            elif supported:
                ambiguity = sorted(
                    [h["site_ref"] for h in supported + insufficient]
                )
                reasons = []
                if len(supported) > 1:
                    reasons.append("MULTIPLE_VIABLE_SOURCES")
                if insufficient:
                    reasons.append("UNOBSERVED_ALTERNATIVE")
                if gap_hits:
                    reasons.append("OCCLUDED_TRANSIT_WINDOW")
                cited = [deposit["event_id"]] + gap_hits
                for item in supported + insufficient:
                    cited.extend(item["cited_event_ids"])
                if superseded_by:
                    superseded[deposit["event_id"]] = superseded_by
                    cited.append(superseded_by)
                results.append({
                    "deposit_event_id": deposit["event_id"],
                    "decision": "AMBIGUOUS",
                    "proposed_site_ref": None,
                    "ambiguity_set": ambiguity,
                    "reasons": reasons,
                    "cited_event_ids": sorted(set(cited)),
                    "requires_confirmation": True,
                    "superseded_by": superseded_by,
                    "custody_chain": chain,
                    "hypotheses": hypotheses,
                    "unresolved_alternatives": [
                        f"{h['site_ref']}: {', '.join(h['reasons']) or 'viable'}"
                        for h in supported + insufficient
                    ],
                })
            else:
                reasons: list[str] = []
                for item in hypotheses:
                    for reason in item["reasons"]:
                        if reason not in reasons:
                            reasons.append(reason)
                if not reasons:
                    reasons = ["NO_SOURCE_OBSERVATIONS"]
                cited = [deposit["event_id"]]
                for item in hypotheses:
                    cited.extend(item["cited_event_ids"])
                if superseded_by:
                    superseded[deposit["event_id"]] = superseded_by
                    cited.append(superseded_by)
                results.append({
                    "deposit_event_id": deposit["event_id"],
                    "decision": "UNKNOWN",
                    "proposed_site_ref": None,
                    "ambiguity_set": [],
                    "reasons": reasons,
                    "cited_event_ids": sorted(set(cited)),
                    "requires_confirmation": True,
                    "superseded_by": superseded_by,
                    "custody_chain": chain,
                    "hypotheses": hypotheses,
                    "unresolved_alternatives": [
                        f"{h['site_ref']}: {', '.join(h['reasons'])}"
                        for h in hypotheses
                    ],
                })
        return {
            "schema_version": CORRESPONDENCE_SCHEMA,
            "engine": {"name": ENGINE_NAME, "version": ENGINE_VERSION},
            "produced_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "counts": {
                "deposits": len(results),
                "proposed": sum(r["decision"] == "PROPOSED" for r in results),
                "ambiguous": sum(r["decision"] == "AMBIGUOUS" for r in results),
                "unknown": sum(r["decision"] == "UNKNOWN" for r in results),
            },
            "deposits": results,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_match(
    obs_paths: list[Path],
    out_dir: Path,
    sync_path: Path | None,
) -> dict[str, Any]:
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise ObservationError("output directory must be absent or empty")
    events: list[dict[str, Any]] = []
    inputs = []
    seen: set[str] = set()
    for path in obs_paths:
        file_events, digest = load_observation_file(path)
        for event in file_events:
            if event["event_id"] in seen:
                raise ObservationError(f"duplicate event_id: {event['event_id']}")
            seen.add(event["event_id"])
        events.extend(file_events)
        inputs.append({"path": str(path), "sha256": digest})
    offsets, sync_digest = load_sync_manifest(sync_path)
    validated_pairs = sorted({
        f"{entry['from_clock']}->{entry['to_clock']}"
        for entry in offsets
        if entry["validated"]
    })

    report = Matcher(events, offsets).decide()
    report["inputs"] = inputs
    report["sync"] = {"manifest_sha256": sync_digest, "validated_pairs": validated_pairs}
    report["unusable_events"] = [
        {"event_id": event["event_id"], "reason": "NULL_STATE"}
        for event in events
        if event["state"] is None
    ]
    report["limitations"] = [
        "Decisions are proposals or abstentions only; VERIFIED never appears and no Memory writes occur.",
        "Pixel coordinates are never compared across cameras; v1 has no calibrated cross-camera transform.",
        "Cross-clock order needs a direct validated sync entry; otherwise ordering stays unknown.",
        "Deposits are processed in (start_ms, end_ms, event_id) order; one site supplies one part per restock cycle.",
        "Appearance compares only stated fields; unstated fields are neutral.",
        f"Dimension tolerance is {DIMENSION_TOLERANCE_MM} mm or {int(DIMENSION_TOLERANCE_RATIO * 100)}%, whichever is larger.",
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--obs", required=True, action="append", type=Path,
                        help="anastylos-observation-v1 file (repeatable)")
    parser.add_argument("--sync", type=Path, default=None,
                        help="optional anastylos-sync-v1 manifest")
    parser.add_argument("--out", required=True, type=Path,
                        help="new or empty directory for report.json")
    args = parser.parse_args(argv)
    try:
        for path in list(args.obs) + ([args.sync] if args.sync else []):
            if not path.is_file():
                raise ObservationError(f"input is not a file: {path}")
        report = run_match(args.obs, args.out, args.sync)
    except (ObservationError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "schema": CORRESPONDENCE_SCHEMA,
        "report": str((args.out / "report.json").resolve()),
        "counts": report["counts"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
