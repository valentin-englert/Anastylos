"""Layout spec: validation, proposal from existing jobs, operator confirmation, atomic writes (synthetic, offline)."""

from __future__ import annotations

import json
import unittest

from support import SessionCase

import explosion_layout as layout


class LayoutTest(SessionCase):
    def spec_file(self):
        return self.session / "drawings" / "explosion.json"

    def test_propose_writes_an_unconfirmed_skeleton_front_to_back(self):
        self.job("back-shell-20261101-010101", "tri", part="back shell of the controller", view_side="exterior")
        self.job("pcb-20261101-010101", "hex", part="pcb with cable")
        self.job("front-shell-20261101-010101", "rect", part="front shell of the controller")
        result = layout.propose(self.session)
        self.assertTrue(result["written"])
        doc = json.loads(self.spec_file().read_text())
        self.assertEqual([l["job"] for l in doc["layers"]], ["front-shell-20261101-010101", "pcb-20261101-010101", "back-shell-20261101-010101"])
        self.assertTrue(all(l["confirmed_by_operator"] is False for l in doc["layers"]))
        self.assertEqual(doc["layers"][2]["view_side"], "exterior")
        self.assertEqual(list(self.session.joinpath("drawings").glob("*.tmp-*")), [])

    def test_reruns_of_one_part_collapse_and_a_bound_run_wins(self):
        self.job("front-shell-20261101-010101", part="front shell")
        self.job("front-shell-20261101-020202", part="front shell")
        self.job("front-shell-20261101-030303", part="front shell")
        self.bind("front-shell-20261101-020202", "confirm")
        layers = layout.propose(self.session)["layout"]["layers"]
        self.assertEqual([l["job"] for l in layers], ["front-shell-20261101-020202"])

    def test_without_a_bound_run_the_newest_run_is_proposed(self):
        self.job("front-shell-20261101-010101", part="front shell")
        self.job("front-shell-20261101-020202", part="front shell")
        self.assertEqual(layout.propose(self.session)["layout"]["layers"][0]["job"], "front-shell-20261101-020202")

    def test_jobs_without_a_drawing_or_with_a_rejected_binding_are_not_proposed(self):
        self.job("failed-1", part="a", state="failed")
        self.job("nodrawing-1", part="b", svg="<svg/>")
        self.job("rejected-1", part="c", binding="reject")
        self.job("ok-1", part="d")
        self.assertEqual([l["job"] for l in layout.propose(self.session)["layout"]["layers"]], ["ok-1"])

    def test_propose_is_idempotent_and_keeps_confirmations(self):
        self.job("front-1", part="front")
        layout.propose(self.session)
        layout.confirm_layer(self.session, "front-1", label="Front shell", now="2026-11-01T12:00:00")
        written = self.spec_file().read_bytes()
        again = layout.propose(self.session)
        self.assertEqual((again["written"], again["added"]), (False, []))
        self.assertEqual(self.spec_file().read_bytes(), written)
        self.job("pcb-1", "hex", part="pcb")
        grown = layout.propose(self.session)
        self.assertEqual(grown["added"], ["pcb-1"])
        self.assertEqual([(l["job"], l["confirmed_by_operator"]) for l in grown["layout"]["layers"]], [("front-1", True), ("pcb-1", False)])

    def test_confirm_needs_a_view_side_and_records_the_decision(self):
        self.job("front-1", part="front", view_side="")
        layout.propose(self.session)
        with self.assertRaises(layout.LayoutError):
            layout.confirm_layer(self.session, "front-1")
        doc = layout.confirm_layer(self.session, "front-1", view_side="exterior", flip_horizontal=True, now="2026-11-01T12:00:00")
        layer = doc["layers"][0]
        self.assertEqual((layer["view_side"], layer["flip_horizontal"], layer["confirmed_by_operator"], layer["confirmed_at"]),
                         ("exterior", True, True, "2026-11-01T12:00:00"))
        undone = layout.confirm_layer(self.session, "front-1", confirmed=False)["layers"][0]
        self.assertFalse(undone["confirmed_by_operator"])
        self.assertNotIn("confirmed_at", undone)

    def test_confirm_unknown_layer_or_missing_layout_is_refused(self):
        with self.assertRaises(layout.LayoutError):
            layout.confirm_layer(self.session, "front-1")
        self.job("front-1")
        layout.propose(self.session)
        with self.assertRaises(layout.LayoutError):
            layout.confirm_layer(self.session, "other-1")

    def test_reorder_drops_confirmation_of_moved_layers_only(self):
        self.job("front-1", part="front")
        self.job("pcb-1", part="pcb")
        self.job("back-1", part="back")
        layout.propose(self.session)
        for job in ("front-1", "pcb-1", "back-1"):
            layout.confirm_layer(self.session, job)
        doc = layout.reorder_layers(self.session, ["front-1", "back-1", "pcb-1"])
        self.assertEqual([(l["job"], l["confirmed_by_operator"]) for l in doc["layers"]],
                         [("front-1", True), ("back-1", False), ("pcb-1", False)])
        for bad in (["front-1"], ["front-1", "back-1", "pcb-1", "x"], ["front-1", "front-1", "pcb-1"]):
            with self.assertRaises(layout.LayoutError):
                layout.reorder_layers(self.session, bad)

    def test_validation_refuses_bad_specs(self):
        good = self.layer("front-1")
        bad_docs = [
            {"schema": "other", "layers": []},
            {"schema": layout.SCHEMA, "layers": [good, good]},
            {"schema": layout.SCHEMA, "layers": [{**good, "job": "../escape"}]},
            {"schema": layout.SCHEMA, "layers": [{**good, "job": "a/b"}]},
            {"schema": layout.SCHEMA, "layers": [{**good, "confirmed_by_operator": "yes"}]},
            {"schema": layout.SCHEMA, "layers": [{**good, "flip_horizontal": None}]},
            {"schema": layout.SCHEMA, "layers": [{**good, "label": ""}]},
            {"schema": layout.SCHEMA, "layers": [{**good, "color": "red"}]},
            {"schema": layout.SCHEMA, "layers": "front-1"},
        ]
        for doc in bad_docs:
            with self.assertRaises(layout.LayoutError, msg=str(doc)):
                layout.validate(doc)

    def test_a_corrupt_spec_file_is_reported_not_overwritten(self):
        self.job("front-1")
        self.spec_file().write_text("{not json", encoding="utf-8")
        with self.assertRaises(layout.LayoutError):
            layout.propose(self.session)
        self.assertEqual(self.spec_file().read_text(), "{not json")


if __name__ == "__main__":
    unittest.main()
