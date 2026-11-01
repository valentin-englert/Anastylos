"""Composer: three selectable groups, layer order, unconfirmed marking, hashes, no invented geometry (synthetic, offline)."""

from __future__ import annotations

import hashlib
import json
import unittest
from xml.etree import ElementTree

import support
from support import SessionCase, sha

import compose
import explosion_layout as layout

NS = "{http://www.w3.org/2000/svg}"


def parts(svg: str) -> list[ElementTree.Element]:
    root = ElementTree.fromstring(svg)
    return [el for el in root.iter(NS + "g") if "ax-part" in (el.get("class") or "")]


class ComposeTest(SessionCase):
    def confirmed_three(self):
        self.three_jobs()
        for name in ("front-1", "pcb-1", "back-1"):
            self.bind(name, "confirm")
        return self.spec(self.layer("front-1", "Front shell"), self.layer("pcb-1", "PCB"), self.layer("back-1", "Back shell", "exterior", flip=True))

    def test_three_selectable_groups_in_spec_order(self):
        self.confirmed_three()
        result = compose.compose(self.session)
        groups = parts(result.svg)
        self.assertEqual(len(groups), 3)
        self.assertEqual(len({g.get("id") for g in groups}), 3)
        by_layer = {int(g.get("data-layer")): g for g in groups}
        self.assertEqual([by_layer[i].get("data-job") for i in range(3)], ["front-1", "pcb-1", "back-1"])
        self.assertEqual([l["job"] for l in result.manifest["layers"]], ["front-1", "pcb-1", "back-1"])
        ys = [l["placement"]["exploded_center"][1] for l in result.manifest["layers"]]
        self.assertEqual(ys, sorted(ys))  # top layer at the top of the sheet
        self.assertEqual(len(ys), len(set(ys)))
        for group in groups:
            self.assertEqual(group.get("data-status"), "confirmed")
            self.assertEqual(group.get("role"), "button")

    def test_reordering_the_spec_reorders_the_stack(self):
        self.confirmed_three()
        self.spec(self.layer("back-1"), self.layer("front-1"), self.layer("pcb-1"))
        result = compose.compose(self.session)
        self.assertEqual([l["job"] for l in result.manifest["layers"]], ["back-1", "front-1", "pcb-1"])
        top = next(g for g in parts(result.svg) if g.get("data-layer") == "0")
        self.assertEqual(top.get("data-job"), "back-1")

    def test_unbound_or_unconfirmed_layers_are_drawn_but_marked(self):
        self.three_jobs()
        self.bind("front-1", "confirm")
        self.bind("pcb-1", "confirm")  # bound but the layer itself is not confirmed
        self.spec(self.layer("front-1"), self.layer("pcb-1", confirmed=False), self.layer("back-1"))  # back-1 has no binding
        result = compose.compose(self.session)
        status = {g.get("data-job"): g.get("data-status") for g in parts(result.svg)}
        self.assertEqual(status, {"front-1": "confirmed", "pcb-1": "unconfirmed", "back-1": "unconfirmed"})
        self.assertIn("UNCONFIRMED", result.svg)
        self.assertIn('stroke-dasharray="2 1.2"', result.svg)
        reasons = {l["job"]: l["reasons"] for l in result.manifest["layers"]}
        self.assertIn("drawing not bound by operator", reasons["back-1"])
        self.assertIn("layer, order and view side not confirmed", reasons["pcb-1"])
        self.assertFalse(result.manifest["all_confirmed"])

    def test_missing_view_side_is_unconfirmed(self):
        self.three_jobs()
        self.bind("front-1", "confirm")
        self.spec(self.layer("front-1", view_side=None))
        layer = compose.compose(self.session).manifest["layers"][0]
        self.assertEqual(layer["status"], "unconfirmed")
        self.assertIn("view side not set", layer["reasons"])

    def test_binding_goes_stale_when_the_drawing_changes(self):
        self.job("front-1")
        self.bind("front-1", "confirm", drawing_sha="0" * 64)
        self.spec(self.layer("front-1"))
        layer = compose.compose(self.session).manifest["layers"][0]
        self.assertEqual((layer["binding"], layer["status"]), ("stale", "unconfirmed"))

    def test_rejected_or_unusable_drawings_are_not_drawn_and_nothing_is_invented(self):
        self.three_jobs()
        self.bind("front-1", "reject")
        self.job("broken-1", svg="<svg xmlns='http://www.w3.org/2000/svg'/>")
        self.job("failed-1", state="failed")
        self.spec(self.layer("front-1"), self.layer("pcb-1"), self.layer("broken-1"), self.layer("failed-1"), self.layer("gone-1"))
        result = compose.compose(self.session)
        self.assertEqual([g.get("data-job") for g in parts(result.svg)], ["pcb-1"])
        self.assertEqual({e["job"] for e in result.manifest["excluded"]}, {"front-1", "broken-1", "failed-1", "gone-1"})
        self.assertIn("Not drawn", result.svg)
        self.assertEqual(result.svg.count('class="ax-outline"'), 1)

    def test_outlines_are_copied_verbatim_at_uniform_scale(self):
        self.confirmed_three()
        result = compose.compose(self.session)
        source_d = {name: ElementTree.fromstring((self.session / "drawings" / "jobs" / name / "drawing.svg").read_text())
                    .find(f".//{NS}g[@id='observed_outline']/{NS}path").get("d") for name in ("front-1", "pcb-1", "back-1")}
        for group in parts(result.svg):
            self.assertEqual(group.find(f".//{NS}path").get("d"), source_d[group.get("data-job")])
        sizes = {l["job"]: l["outline_size_mm"] for l in result.manifest["layers"]}
        self.assertEqual(sizes, {"front-1": [60.0, 40.0], "pcb-1": [60.0, 40.0], "back-1": [80.0, 50.0]})
        self.assertEqual(result.manifest["scale"]["svg_units_per_mm"], 1.0)
        self.assertNotIn("scale(0", result.svg)

    def test_flip_mirrors_only_the_outline(self):
        self.confirmed_three()
        groups = {g.get("data-job"): g for g in parts(compose.compose(self.session).svg)}
        shape = lambda job: groups[job].find(f"{NS}g[@class='ax-shape']")  # noqa: E731
        self.assertIn("scale(-1 1)", shape("back-1").get("transform"))
        self.assertIsNone(shape("front-1").get("transform"))

    def test_manifest_carries_sha256_of_every_input(self):
        self.confirmed_three()
        result = compose.compose(self.session)
        spec_path = self.session / "drawings" / "explosion.json"
        self.assertEqual(result.manifest["layout_spec"]["sha256"], sha(spec_path))
        for entry in result.manifest["layers"]:
            job_dir = self.session / "drawings" / "jobs" / entry["job"]
            for name in ("drawing.svg", "overlay.jpg", "binding.json", "progress.json"):
                self.assertEqual(entry["inputs"][name]["sha256"], sha(job_dir / name), name)
            self.assertEqual(entry["inputs"]["rectified.png"]["sha256"], sha(job_dir / "chosen_rectified.png"))
            self.assertEqual(entry["inputs"]["rectified.png"]["path"], f"jobs/{entry['job']}/chosen_rectified.png")
        self.assertEqual(result.manifest["svg_sha256"], hashlib.sha256(result.svg.encode("utf-8")).hexdigest())
        group = parts(result.svg)[0]
        self.assertEqual(group.get("data-sha256"), sha(self.session / "drawings" / "jobs" / group.get("data-job") / "drawing.svg"))

    def test_deterministic_and_read_only(self):
        self.confirmed_three()
        before = {p: sha(p) for p in self.session.rglob("*") if p.is_file()}
        first = compose.compose(self.session)
        again = compose.compose(self.session)
        self.assertEqual((first.svg, first.manifest), (again.svg, again.manifest))
        self.assertEqual(before, {p: sha(p) for p in self.session.rglob("*") if p.is_file()})
        manifest = compose.write_outputs(self.session)
        out = self.session / "drawings" / "explosion"
        written = (out / "explosion.svg").read_bytes()
        compose.write_outputs(self.session)
        self.assertEqual(written, (out / "explosion.svg").read_bytes())
        self.assertEqual(json.loads((out / "manifest.json").read_text()), manifest)
        self.assertEqual(manifest["svg_sha256"], hashlib.sha256(written).hexdigest())

    def test_assembled_mode_centres_layers_on_one_point(self):
        self.confirmed_three()
        result = compose.compose(self.session, mode="assembled")
        self.assertIn('data-mode="assembled"', result.svg)
        centres = {tuple(l["placement"]["assembled_center"]) for l in result.manifest["layers"]}
        self.assertEqual(len(centres), 1)
        self.assertEqual(len({tuple(l["placement"]["exploded_center"]) for l in result.manifest["layers"]}), 3)
        group = parts(result.svg)[0]
        self.assertIn("data-exploded-translate", group.attrib)

    def test_hostile_input_cannot_inject_markup(self):
        self.job("evil-1", svg=support.drawing_svg(support.SHAPES["rect"]).replace("M 5.00", "M 5.00 <script>x</script> L"))
        self.job("evil-2", svg='<!DOCTYPE svg [<!ENTITY a "aaaa">]>' + support.drawing_svg(support.SHAPES["rect"]))
        self.job("evil-3", part='x"><script>alert(1)</script>')
        self.spec(self.layer("evil-1"), self.layer("evil-2"), self.layer("evil-3", label='<script>alert(1)</script>'))
        result = compose.compose(self.session)
        self.assertNotIn("<script", result.svg)
        ElementTree.fromstring(result.svg)  # still well-formed
        self.assertEqual({e["job"] for e in result.manifest["excluded"]}, {"evil-1", "evil-2"})

    def test_drawing_not_in_mm_is_not_drawn(self):
        self.job("px-1", svg=support.drawing_svg(support.SHAPES["rect"], units="px"))
        self.spec(self.layer("px-1"))
        result = compose.compose(self.session)
        self.assertEqual(result.manifest["layers"], [])
        self.assertIn("millimetre", result.manifest["excluded"][0]["reason"])

    def test_view_side_disagreement_is_a_warning_not_a_silent_override(self):
        self.job("front-1", view_side="interior")
        self.bind("front-1", "confirm")
        self.spec(self.layer("front-1", view_side="exterior"))
        result = compose.compose(self.session)
        self.assertEqual(len(result.manifest["warnings"]), 1)
        self.assertEqual(result.manifest["layers"][0]["view_side"], "exterior")

    def test_missing_spec_is_an_explicit_error_and_preview_spec_is_hashed(self):
        self.job("front-1")
        with self.assertRaises(layout.LayoutError):
            compose.compose(self.session)
        preview = compose.compose(self.session, {"schema": layout.SCHEMA, "layers": [self.layer("front-1", confirmed=False)]})
        self.assertEqual(len(preview.manifest["layout_spec"]["sha256"]), 64)
        self.assertFalse((self.session / "drawings" / "explosion.json").exists())


if __name__ == "__main__":
    unittest.main()
