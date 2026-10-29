#!/usr/bin/env python3
"""Validate the SNES-01 benchmark bundle. Stdlib-only.

Checks the four JSON artifacts for schema and consistency errors:
pending statuses, event count, unique ids, frame references,
frame<->time mapping (t = N-1), crop bounds, and the ground-truth
linkage back to the manifest bytes. Optionally verifies that
referenced frame files and the contact sheet exist on disk.

Exit 0 when all checks pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

FRAME_RE = re.compile(r"^frame_(\d{4})$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
FRAME_W, FRAME_H = 2160, 3840  # extracted stills, rotation applied

EXPECT_SCHEMAS = {
    "manifest": "snes01-benchmark/manifest@1",
    "stable": "snes01-benchmark/stable-states@1",
    "events": "snes01-benchmark/event-candidates@1",
    "ground": "snes01-benchmark/ground-truth@1",
}


class Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.passes = 0

    def check(self, ok: bool, message: str) -> None:
        if ok:
            self.passes += 1
        else:
            self.errors.append(message)

    def report(self) -> int:
        for message in self.errors:
            print(f"FAIL {message}")
        print(f"{self.passes} passed, {len(self.errors)} failed")
        return 1 if self.errors else 0


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def frame_number(ref: str) -> int | None:
    match = FRAME_RE.match(ref)
    return int(match.group(1)) if match else None


def check_manifest(checker: Checker, doc: dict, path: str) -> None:
    checker.check(
        doc.get("schema") == EXPECT_SCHEMAS["manifest"],
        f"{path}: schema must be {EXPECT_SCHEMAS['manifest']}",
    )
    video = doc.get("source_video", {})
    sha = video.get("sha256", "")
    checker.check(
        isinstance(sha, str) and SHA_RE.match(sha.lower()) is not None,
        f"{path}: source_video.sha256 must be a 64-hex string",
    )
    frames = doc.get("frames", {})
    files = frames.get("files", [])
    checker.check(
        frames.get("count") == len(files) and len(files) > 0,
        f"{path}: frames.count must equal len(frames.files) and be > 0",
    )
    names = [entry.get("name") for entry in files]
    checker.check(
        names == sorted(names),
        f"{path}: frames.files must be sorted by name",
    )
    for entry in files:
        entry_sha = entry.get("sha256", "")
        checker.check(
            isinstance(entry_sha, str)
            and SHA_RE.match(entry_sha.lower()) is not None,
            f"{path}: {entry.get('name')}: sha256 must be a 64-hex string",
        )
        checker.check(
            entry.get("width") == FRAME_W and entry.get("height") == FRAME_H,
            f"{path}: {entry.get('name')}: expected {FRAME_W}x{FRAME_H}",
        )


def check_stable(checker: Checker, doc: dict, path: str) -> None:
    checker.check(
        doc.get("schema") == EXPECT_SCHEMAS["stable"],
        f"{path}: schema must be {EXPECT_SCHEMAS['stable']}",
    )
    states = doc.get("stable_states", [])
    checker.check(len(states) >= 1, f"{path}: need at least one stable state")
    for state in states:
        for key in ("id", "label", "frame_start", "frame_end", "time_s"):
            checker.check(
                key in state, f"{path}: state {state.get('id')}: missing {key}"
            )
        start = frame_number(state.get("frame_start", ""))
        end = frame_number(state.get("frame_end", ""))
        checker.check(
            start is not None and end is not None and start <= end,
            f"{path}: state {state.get('id')}: bad frame range",
        )
        if start is not None and end is not None:
            checker.check(
                state.get("time_s") == [start - 1, end - 1],
                f"{path}: state {state.get('id')}: time_s must be "
                f"[{start - 1}, {end - 1}] under frame_N<->t=N-1",
            )
    for interval in doc.get("occluded_intervals", []):
        start = frame_number(interval.get("frame_start", ""))
        end = frame_number(interval.get("frame_end", ""))
        checker.check(
            start is not None and end is not None and start <= end,
            f"{path}: occluded interval {interval.get('id')}: bad frame range",
        )


def check_events(checker: Checker, doc: dict, path: str) -> list[str]:
    checker.check(
        doc.get("schema") == EXPECT_SCHEMAS["events"],
        f"{path}: schema must be {EXPECT_SCHEMAS['events']}",
    )
    events = doc.get("events", [])
    checker.check(
        1 <= len(events) <= 8,
        f"{path}: event count must be 1..8, got {len(events)}",
    )
    ids = [event.get("id") for event in events]
    checker.check(len(ids) == len(set(ids)), f"{path}: event ids must be unique")
    for event in events:
        eid = event.get("id")
        checker.check(
            event.get("status") == "candidate",
            f"{path}: event {eid}: status must be 'candidate'",
        )
        checker.check(
            isinstance(event.get("uncertainty"), str)
            and len(event.get("uncertainty", "")) >= 20,
            f"{path}: event {eid}: uncertainty must be a non-trivial note",
        )
        before = frame_number(event.get("evidence_before", ""))
        after = frame_number(event.get("evidence_after", ""))
        checker.check(
            before is not None and after is not None and before < after,
            f"{path}: event {eid}: need evidence_before < evidence_after",
        )
        interval = event.get("frame_interval", [])
        checker.check(
            (
                isinstance(interval, list)
                and len(interval) == 2
                and frame_number(interval[0]) is not None
                and frame_number(interval[1]) is not None
            ),
            f"{path}: event {eid}: frame_interval must be [frame, frame]",
        )
        if before is not None and after is not None and len(interval) == 2:
            lo = frame_number(interval[0])
            hi = frame_number(interval[1])
            checker.check(
                lo is not None
                and hi is not None
                and lo <= before
                and after <= hi,
                f"{path}: event {eid}: interval must contain before/after",
            )
            checker.check(
                event.get("time_interval_s") == [lo - 1, hi - 1]
                if lo is not None and hi is not None
                else False,
                f"{path}: event {eid}: time_interval_s must follow "
                "frame_N<->t=N-1",
            )
        tiles = event.get("contact_tile", {})
        for side in ("before", "after"):
            tile = tiles.get(side, {})
            checker.check(
                tile.get("frame")
                == event.get(f"evidence_{side}", ""),
                f"{path}: event {eid}: contact_tile.{side}.frame must match "
                f"evidence_{side}",
            )
            crop = tile.get("crop", [])
            checker.check(
                (
                    isinstance(crop, list)
                    and len(crop) == 4
                    and all(isinstance(v, int) and v >= 0 for v in crop)
                    and crop[0] + crop[2] <= FRAME_W
                    and crop[1] + crop[3] <= FRAME_H
                    and crop[2] > 0
                    and crop[3] > 0
                ),
                f"{path}: event {eid}: contact_tile.{side}.crop must fit "
                f"inside {FRAME_W}x{FRAME_H}",
            )
    return [str(i) for i in ids]


def check_ground(
    checker: Checker,
    doc: dict,
    path: str,
    manifest_path: str,
    event_ids: list[str],
) -> None:
    checker.check(
        doc.get("schema") == EXPECT_SCHEMAS["ground"],
        f"{path}: schema must be {EXPECT_SCHEMAS['ground']}",
    )
    checker.check(
        doc.get("status") == "pending",
        f"{path}: top-level status must be 'pending'",
    )
    with open(manifest_path, "rb") as handle:
        actual = hashlib.sha256(handle.read()).hexdigest()
    checker.check(
        doc.get("source_manifest_sha256") == actual,
        f"{path}: source_manifest_sha256 must match {manifest_path} bytes",
    )
    entries = doc.get("events", [])
    checker.check(
        sorted(str(e.get("id")) for e in entries) == sorted(event_ids),
        f"{path}: event entries must cover exactly the candidate event ids",
    )
    for entry in entries:
        eid = entry.get("id")
        checker.check(
            entry.get("status") == "pending",
            f"{path}: event {eid}: status must be 'pending'",
        )
        checker.check(
            entry.get("verdict") is None,
            f"{path}: event {eid}: verdict must be null until review",
        )
        checker.check(
            entry.get("confirmed_by") is None,
            f"{path}: event {eid}: confirmed_by must be null until review",
        )


def check_files_exist(
    checker: Checker,
    frames_dir: str,
    refs: list[str],
    contact_sheet: str | None,
) -> None:
    for ref in refs:
        full = os.path.join(frames_dir, ref + ".jpg")
        checker.check(os.path.exists(full), f"missing frame file: {full}")
    if contact_sheet is not None:
        checker.check(
            os.path.exists(contact_sheet),
            f"missing contact sheet: {contact_sheet}",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the SNES-01 benchmark bundle."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--stable", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--ground", required=True)
    parser.add_argument(
        "--frames-dir", default=None, help="Verify referenced frames exist."
    )
    parser.add_argument(
        "--contact-sheet", default=None, help="Verify the contact sheet exists."
    )
    args = parser.parse_args(argv)

    checker = Checker()
    try:
        manifest = load_json(args.manifest)
        stable = load_json(args.stable)
        events_doc = load_json(args.events)
        ground = load_json(args.ground)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL cannot load inputs: {exc}")
        return 1

    check_manifest(checker, manifest, args.manifest)
    check_stable(checker, stable, args.stable)
    event_ids = check_events(checker, events_doc, args.events)
    check_ground(checker, ground, args.ground, args.manifest, event_ids)

    if args.frames_dir is not None:
        refs: list[str] = []
        for state in stable.get("stable_states", []):
            refs += [state.get("frame_start", ""), state.get("frame_end", "")]
        for event in events_doc.get("events", []):
            refs += [
                event.get("evidence_before", ""),
                event.get("evidence_after", ""),
            ]
        check_files_exist(checker, args.frames_dir, refs, args.contact_sheet)
    elif args.contact_sheet is not None:
        check_files_exist(checker, "", [], args.contact_sheet)

    return checker.report()


if __name__ == "__main__":
    raise SystemExit(main())
