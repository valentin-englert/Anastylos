"""Deterministic tests for the retrospective custody matcher.

Synthetic logic tests only: they validate the matching rules, NOT
real-world physical correspondence.
"""

import json
import tempfile
import unittest
from pathlib import Path

from match import Matcher, main as match_main
from validate import (
    ObservationError,
    check_event,
    load_observation_file,
    load_sync_manifest,
)

CLOCK = "cam-a"


def make_event(
    event_id,
    kind,
    state,
    start_ms,
    end_ms,
    clock=CLOCK,
    status="observed",
    camera="cam-a",
    epoch=None,
    frame_index=0,
    limitations=(),
):
    pts = start_ms if start_ms == end_ms else (start_ms + end_ms) / 2
    return {
        "schema_version": "anastylos-observation-v1",
        "event_id": event_id,
        "kind": kind,
        "status": status,
        "time": {"clock_id": clock, "start_ms": start_ms, "end_ms": end_ms},
        "location": None
        if camera is None
        else {
            "camera_id": camera,
            "xy_px": [10.0, 20.0],
            "uncertainty_px": 2.0,
            "pose_epoch": epoch,
            "frame": "display",
        },
        "state": state,
        "evidence": {
            "camera_id": camera,
            "frame_index": frame_index,
            "pts_ms": pts,
            "source_ref": f"{camera or 'review'}-src",
            "source_sha256": None,
        },
        "limitations": list(limitations),
    }


def source_state(site, presence, verified=True, appearance=None):
    return {
        "site_ref": site,
        "presence": presence,
        "verified": verified,
        "appearance": appearance,
    }


def interaction(actor, action, target=None, contact=True, handoff=None):
    state = {
        "actor": actor,
        "action": action,
        "target_ref": target,
        "contact_verified": contact,
        "handoff_from": None,
        "handoff_to": None,
    }
    if action == "handoff":
        state["handoff_from"] = handoff[0]
        state["handoff_to"] = handoff[1]
    return state


def tray(change, tray="tray-1", cell="A1", supported=True, appearance=None):
    return {
        "tray_ref": tray,
        "cell_ref": cell,
        "change": change,
        "supported": supported,
        "appearance": appearance,
    }


def gap(scope, reason="occlusion", scope_ref=None):
    return {"scope": scope, "scope_ref": scope_ref, "reason": reason}


def pose_change(device, from_epoch, to_epoch, links=()):
    return {
        "device_ref": device,
        "from_epoch": from_epoch,
        "to_epoch": to_epoch,
        "site_links": [
            {"site_ref": site, "from_epoch": from_epoch, "to_epoch": to_epoch}
            for site in links
        ],
    }


def decide(events, offsets=()):
    return Matcher(events, list(offsets)).decide()


def validated_offset(from_clock, to_clock, low=0.0, high=0.0):
    return {
        "from_clock": from_clock,
        "to_clock": to_clock,
        "offset_min_ms": low,
        "offset_max_ms": high,
        "validated": True,
        "basis_ref": "test",
    }


SCREW = {
    "component_class": "screw",
    "head_drive": "phillips",
    "length_mm": None,
    "diameter_mm": None,
    "marks": None,
}


def full_chain(prefix, site, base=0, deposit_cell="A1", appearance=None):
    """Present -> grasp -> absent -> supported addition."""
    return [
        make_event(f"{prefix}-present", "source_state",
                   source_state(site, "present", True, appearance), base, base + 100),
        make_event(f"{prefix}-grasp", "tool_interaction",
                   interaction("hand:right", "grasp", site), base + 200, base + 300),
        make_event(f"{prefix}-absent", "source_state",
                   source_state(site, "absent", True), base + 400, base + 500),
        make_event(f"{prefix}-deposit", "tray_state",
                   tray("addition", cell=deposit_cell, appearance=appearance),
                   base + 600, base + 700),
    ]


class MatcherBehaviorTests(unittest.TestCase):
    def test_01_single_unambiguous_removal_and_deposit(self):
        report = decide(full_chain("t1", "site-1"))
        self.assertEqual(report["counts"], {"deposits": 1, "proposed": 1,
                                            "ambiguous": 0, "unknown": 0})
        deposit = report["deposits"][0]
        self.assertEqual(deposit["decision"], "PROPOSED")
        self.assertEqual(deposit["proposed_site_ref"], "site-1")
        self.assertTrue(deposit["requires_confirmation"])
        for cited in ("t1-present", "t1-grasp", "t1-absent", "t1-deposit"):
            self.assertIn(cited, deposit["cited_event_ids"])

    def test_02_tool_touch_without_removal_is_not_a_removal(self):
        events = [
            make_event("t2-present", "source_state", source_state("site-1", "present"),
                       0, 100),
            make_event("t2-contact", "tool_interaction",
                       interaction("tool:driver-1", "contact", "site-1"), 200, 300),
            make_event("t2-still", "source_state", source_state("site-1", "present"),
                       400, 500),
            make_event("t2-deposit", "tray_state", tray("addition"), 600, 700),
        ]
        report = decide(events)
        deposit = report["deposits"][0]
        self.assertEqual(deposit["decision"], "UNKNOWN")
        hypothesis = deposit["hypotheses"][0]
        self.assertEqual(hypothesis["verdict"], "INSUFFICIENT")
        self.assertIn("SOURCE_ABSENCE_UNVERIFIED", hypothesis["reasons"])

    def test_03_loosened_without_removal_is_rejected(self):
        events = [
            make_event("t3-present", "source_state", source_state("site-1", "present"),
                       0, 100),
            make_event("t3-torque", "tool_interaction",
                       interaction("tool:driver-1", "torque", "site-1"), 200, 300),
            make_event("t3-loose", "source_state", source_state("site-1", "loosened"),
                       800, 900),
            make_event("t3-deposit", "tray_state", tray("addition"), 600, 700),
        ]
        report = decide(events)
        deposit = report["deposits"][0]
        self.assertEqual(deposit["decision"], "UNKNOWN")
        hypothesis = deposit["hypotheses"][0]
        self.assertEqual(hypothesis["verdict"], "REJECTED")
        self.assertIn("SOURCE_NOT_EMPTIED_LOOSENED_ONLY", hypothesis["reasons"])

    def test_04_two_sources_one_destination_is_ambiguous(self):
        events = full_chain("t4a", "site-1", deposit_cell="A1")[:3]
        events += full_chain("t4b", "site-2", base=50, deposit_cell="A1")[:3]
        events.append(make_event("t4-deposit", "tray_state", tray("addition"),
                                 600, 700))
        report = decide(events)
        deposit = report["deposits"][0]
        self.assertEqual(deposit["decision"], "AMBIGUOUS")
        self.assertEqual(deposit["ambiguity_set"], ["site-1", "site-2"])
        self.assertIn("MULTIPLE_VIABLE_SOURCES", deposit["reasons"])
        self.assertIsNone(deposit["proposed_site_ref"])

    def test_05_two_sources_two_destinations_assign_in_order(self):
        events = full_chain("t5a", "site-1", base=0, deposit_cell="A1")
        # Site 2 is verifiably still present when deposit 1 lands.
        events += [
            make_event("t5b-present", "source_state", source_state("site-2", "present"),
                       800, 900),
            make_event("t5b-grasp", "tool_interaction",
                       interaction("hand:right", "grasp", "site-2"), 1200, 1300),
            make_event("t5b-absent", "source_state", source_state("site-2", "absent"),
                       1400, 1500),
            make_event("t5b-deposit", "tray_state", tray("addition", cell="A2"),
                       1600, 1700),
        ]
        report = decide(events)
        self.assertEqual(report["counts"]["proposed"], 2)
        first, second = report["deposits"]
        self.assertEqual((first["decision"], first["proposed_site_ref"]),
                         ("PROPOSED", "site-1"))
        self.assertEqual((second["decision"], second["proposed_site_ref"]),
                         ("PROPOSED", "site-2"))
        # Site 2 was rejected for the first deposit (still present), and
        # site 1 was consumed before the second deposit.
        self.assertIn("SOURCE_STILL_PRESENT", first["hypotheses"][1]["reasons"])
        self.assertIn("SOURCE_ALREADY_ATTRIBUTED", second["hypotheses"][0]["reasons"])

    def test_06_delayed_source_visibility_still_supports(self):
        events = [
            make_event("t6-present", "source_state", source_state("site-1", "present"),
                       0, 100),
            make_event("t6-grasp", "tool_interaction",
                       interaction("hand:right", "grasp", "site-1"), 200, 300),
            make_event("t6-deposit", "tray_state", tray("addition"), 600, 700),
            # Absence verified long after the deposit.
            make_event("t6-absent", "source_state", source_state("site-1", "absent"),
                       5000, 5100),
        ]
        report = decide(events)
        deposit = report["deposits"][0]
        self.assertEqual(deposit["decision"], "PROPOSED")
        self.assertEqual(deposit["proposed_site_ref"], "site-1")

    def test_07_temporary_deposit_then_repickup_transfers(self):
        events = [
            make_event("t7-present", "source_state", source_state("site-1", "present"),
                       0, 100),
            make_event("t7-grasp", "tool_interaction",
                       interaction("hand:right", "grasp", "site-1"), 200, 300),
            make_event("t7-absent", "source_state", source_state("site-1", "absent"),
                       400, 500),
            make_event("t7-deposit1", "tray_state", tray("addition", cell="A1"),
                       600, 700),
            make_event("t7-pickup", "tray_state", tray("removal", cell="A1"),
                       800, 900),
            make_event("t7-deposit2", "tray_state", tray("addition", cell="B2"),
                       1000, 1100),
        ]
        report = decide(events)
        first, second = report["deposits"]
        self.assertEqual(first["decision"], "PROPOSED")
        self.assertEqual(first["superseded_by"], "t7-pickup")
        self.assertEqual(second["decision"], "PROPOSED")
        self.assertEqual(second["proposed_site_ref"], "site-1")
        self.assertEqual(second["custody_chain"], ["t7-deposit1", "t7-pickup"])

    def test_08_handoff_between_hands(self):
        base = [
            make_event("t8-present", "source_state", source_state("site-1", "present"),
                       0, 100),
            make_event("t8-grasp-a", "tool_interaction",
                       interaction("hand:left", "grasp", "site-1"), 200, 250),
            make_event("t8-grasp-b", "tool_interaction",
                       interaction("hand:right", "grasp", "site-1"), 290, 340),
            make_event("t8-absent", "source_state", source_state("site-1", "absent"),
                       400, 500),
            make_event("t8-deposit", "tray_state", tray("addition"), 600, 700),
        ]
        without_handoff = decide(base)
        self.assertEqual(without_handoff["deposits"][0]["decision"], "UNKNOWN")
        self.assertIn(
            "HANDOFF_UNVERIFIED",
            without_handoff["deposits"][0]["hypotheses"][0]["reasons"],
        )
        with_handoff = decide(base + [
            make_event("t8-handoff", "tool_interaction",
                       interaction("hand:left", "handoff", "site-1",
                                 handoff=("hand:left", "hand:right")), 260, 280),
        ])
        self.assertEqual(with_handoff["deposits"][0]["decision"], "PROPOSED")

    def test_09_uncertain_timestamps_abstain(self):
        events = [
            # Wide interval overlaps the deposit: order unknowable.
            make_event("t9-present", "source_state", source_state("site-1", "present"),
                       0, 5000),
            make_event("t9-grasp", "tool_interaction",
                       interaction("hand:right", "grasp", "site-1"), 200, 300),
            make_event("t9-absent", "source_state", source_state("site-1", "absent"),
                       400, 500),
            make_event("t9-deposit", "tray_state", tray("addition"), 600, 700),
        ]
        report = decide(events)
        deposit = report["deposits"][0]
        self.assertEqual(deposit["decision"], "UNKNOWN")
        self.assertIn("TEMPORAL_ORDER_UNVERIFIED", deposit["reasons"])

    def test_10_cross_camera_order_needs_validated_sync(self):
        source_side = [
            make_event("t10-present", "source_state", source_state("site-1", "present"),
                       0, 100, clock="cam-a", camera="cam-a"),
            make_event("t10-grasp", "tool_interaction",
                       interaction("hand:right", "grasp", "site-1"),
                       200, 300, clock="cam-a", camera="cam-a"),
            make_event("t10-absent", "source_state", source_state("site-1", "absent"),
                       400, 500, clock="cam-a", camera="cam-a"),
        ]
        deposit = make_event("t10-deposit", "tray_state", tray("addition"),
                             600, 700, clock="cam-b", camera="cam-b")
        without_sync = decide(source_side + [deposit])
        self.assertEqual(without_sync["deposits"][0]["decision"], "UNKNOWN")
        self.assertIn("CROSS_CAMERA_ORDER_UNVERIFIED",
                      without_sync["deposits"][0]["reasons"])
        with_sync = decide(
            source_side + [deposit],
            [validated_offset("cam-a", "cam-b", -5.0, 5.0)],
        )
        self.assertEqual(with_sync["deposits"][0]["decision"], "PROPOSED")

    def test_11_pose_epoch_change_needs_site_link(self):
        base = [
            make_event("t11-present", "source_state", source_state("site-1", "present"),
                       0, 100, epoch="epoch-1"),
            make_event("t11-grasp", "tool_interaction",
                       interaction("hand:right", "grasp", "site-1"), 200, 300,
                       epoch="epoch-2"),
            make_event("t11-absent", "source_state", source_state("site-1", "absent"),
                       400, 500, epoch="epoch-2"),
            make_event("t11-deposit", "tray_state", tray("addition"), 600, 700),
        ]
        without_link = decide(base)
        self.assertEqual(without_link["deposits"][0]["decision"], "UNKNOWN")
        self.assertIn("POSE_EPOCH_UNMAPPED", without_link["deposits"][0]["reasons"])
        with_link = decide(base + [
            make_event("t11-pose", "pose_change",
                       pose_change("unit-1", "epoch-1", "epoch-2", ["site-1"]),
                       150, 180),
        ])
        self.assertEqual(with_link["deposits"][0]["decision"], "PROPOSED")

    def test_12_identical_screws_stay_ambiguous(self):
        first = full_chain("t12a", "site-1", appearance=dict(SCREW))
        second = full_chain("t12b", "site-2", base=50, appearance=dict(SCREW))
        events = first[:3] + second[:3] + [
            make_event("t12-deposit", "tray_state",
                       tray("addition", appearance=dict(SCREW)), 600, 700),
        ]
        report = decide(events)
        deposit = report["deposits"][0]
        self.assertEqual(deposit["decision"], "AMBIGUOUS")
        self.assertEqual(deposit["ambiguity_set"], ["site-1", "site-2"])
        # Control: distinguishing appearance filters, never promotes alone.
        torx = dict(SCREW, head_drive="torx")
        control = full_chain("t12c", "site-1", appearance=dict(SCREW))[:3]
        control += full_chain("t12d", "site-2", base=50, appearance=dict(torx))[:3]
        control.append(make_event("t12-deposit2", "tray_state",
                                  tray("addition", appearance=dict(SCREW)), 600, 700))
        control_report = decide(control)
        control_deposit = control_report["deposits"][0]
        self.assertEqual(control_deposit["decision"], "PROPOSED")
        self.assertEqual(control_deposit["proposed_site_ref"], "site-1")

    def test_unsupported_deposit_is_unknown_without_hypotheses(self):
        events = full_chain("t13", "site-1")[:3]
        events.append(make_event("t13-deposit", "tray_state",
                                 tray("addition", supported=False), 600, 700))
        report = decide(events)
        deposit = report["deposits"][0]
        self.assertEqual(deposit["decision"], "UNKNOWN")
        self.assertEqual(deposit["reasons"], ["DEPOSIT_UNSUPPORTED"])
        self.assertEqual(deposit["hypotheses"], [])

    def test_occluded_transit_window_downgrades_to_ambiguous(self):
        events = full_chain("t14", "site-1")
        events.append(make_event("t14-gap", "visibility_gap",
                                 gap("source", "occlusion", "site-1"), 250, 650))
        report = decide(events)
        deposit = report["deposits"][0]
        self.assertEqual(deposit["decision"], "AMBIGUOUS")
        self.assertIn("OCCLUDED_TRANSIT_WINDOW", deposit["reasons"])

    def test_never_emits_verified(self):
        report = decide(full_chain("t15", "site-1"))
        for deposit in report["deposits"]:
            self.assertNotEqual(deposit["decision"], "VERIFIED")
            for hypothesis in deposit["hypotheses"]:
                self.assertNotEqual(hypothesis["verdict"], "VERIFIED")


class CliIntegrationTests(unittest.TestCase):
    def test_cli_reads_files_and_emits_cited_decisions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            obs_path = root / "obs.json"
            obs_path.write_text(json.dumps({
                "schema_version": "anastylos-observation-v1",
                "session_ref": "cli-test",
                "events": full_chain("cli", "site-9"),
            }), encoding="utf-8")
            out_dir = root / "out"
            code = match_main(["--obs", str(obs_path), "--out", str(out_dir)])
            self.assertEqual(code, 0)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], "anastylos-correspondence-v1")
            self.assertEqual(report["counts"]["proposed"], 1)
            deposit = report["deposits"][0]
            self.assertEqual(deposit["proposed_site_ref"], "site-9")
            self.assertIn("cli-grasp", deposit["cited_event_ids"])
            self.assertEqual(len(report["inputs"]), 1)
            self.assertEqual(len(report["inputs"][0]["sha256"]), 64)

    def test_cli_rejects_duplicate_event_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = full_chain("dup", "site-1")
            first = root / "a.json"
            second = root / "b.json"
            first.write_text(json.dumps({
                "schema_version": "anastylos-observation-v1",
                "session_ref": "", "events": events[:2],
            }), encoding="utf-8")
            second.write_text(json.dumps({
                "schema_version": "anastylos-observation-v1",
                "session_ref": "", "events": events[1:3],
            }), encoding="utf-8")
            code = match_main(["--obs", str(first), "--obs", str(second),
                               "--out", str(root / "out")])
            self.assertEqual(code, 2)


class ValidatorTests(unittest.TestCase):
    def test_rejects_unknown_kind(self):
        bad = make_event("v1", "source_state", source_state("s", "present"), 0, 10)
        bad["kind"] = "teleportation"
        with self.assertRaises(ObservationError):
            check_event(bad)

    def test_rejects_pts_outside_interval(self):
        bad = make_event("v2", "source_state", source_state("s", "present"), 0, 10)
        bad["evidence"]["pts_ms"] = 999.0
        with self.assertRaises(ObservationError):
            check_event(bad)

    def test_rejects_handoff_without_endpoints(self):
        bad = make_event("v3", "tool_interaction",
                         {"actor": "a", "action": "handoff", "target_ref": None,
                          "contact_verified": True, "handoff_from": None,
                          "handoff_to": "hand:right"}, 0, 10)
        with self.assertRaises(ObservationError):
            check_event(bad)

    def test_rejects_bad_sync_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sync.json"
            path.write_text(json.dumps({
                "schema_version": "anastylos-sync-v1",
                "offsets": [{"from_clock": "a", "to_clock": "b",
                             "offset_min_ms": 5.0, "offset_max_ms": 1.0,
                             "validated": True, "basis_ref": "x"}],
            }), encoding="utf-8")
            with self.assertRaises(ObservationError):
                load_sync_manifest(path)

    def test_rejects_wrong_file_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "obs.json"
            path.write_text(json.dumps({
                "schema_version": "something-else", "session_ref": "", "events": [],
            }), encoding="utf-8")
            with self.assertRaises(ObservationError):
                load_observation_file(path)


if __name__ == "__main__":
    unittest.main()
