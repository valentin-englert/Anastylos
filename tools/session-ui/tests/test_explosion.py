"""Session-UI explosion view: read-only views, whitelisted bytes, operator layout actions, no paths from the browser (offline)."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))

import explosion  # noqa: E402
import drawings  # noqa: E402
from adapter import AdapterError  # noqa: E402

POLYGONS = {"front-1": "M 5,5 L 65,5 L 65,45 L 5,45 Z", "pcb-1": "M 10,8 L 90,8 L 50,58 Z"}


def drawing_svg(d: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="70mm" viewBox="0 0 100 70"><g id="observed_outline">'
            f'<path data-line-role="outline" d="{d}"/></g></svg>')


class _Done:
    def __init__(self, returncode=0, stderr=""):
        self.returncode, self.stderr, self.stdout = returncode, stderr, ""


class ExplosionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.session = self.root / "s1"
        (self.session / "drawings" / "jobs").mkdir(parents=True)
        (self.session / "agent-session.json").write_text("{}", encoding="utf-8")
        for name, d in POLYGONS.items():
            job = self.session / "drawings" / "jobs" / name
            job.mkdir()
            (job / "progress.json").write_text(json.dumps({"state": "done", "result": {
                "status": "NEEDS_CONFIRMATION", "spec": {"part": name.split("-")[0], "scope": "assembly", "view_side": "interior"}}}), encoding="utf-8")
            (job / "drawing.svg").write_text(drawing_svg(d), encoding="utf-8")
            (job / "overlay.jpg").write_bytes(b"\xff\xd8overlay")
            (job / "chosen_rectified.png").write_bytes(b"\x89PNGphoto")
        (self.session / "drawings" / "jobs" / "stray").mkdir()  # no progress.json: not a job

    def snapshot(self) -> dict:
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.session.rglob("*") if p.is_file()}

    def test_view_without_layout_offers_the_drawn_jobs_and_writes_nothing(self):
        before = self.snapshot()
        view = explosion.explosion_view(self.root, "s1")
        self.assertIsNone(view["layout"])
        self.assertEqual(sorted(view["proposable_jobs"]), ["front-1", "pcb-1"])
        self.assertEqual(self.snapshot(), before)

    def test_operator_flow_propose_confirm_reorder(self):
        added = explosion.layout_action(self.root, "s1", {"action": "propose"})
        self.assertEqual(sorted(added["added"]), ["front-1", "pcb-1"])
        self.assertEqual([l["job"] for l in added["layout"]["layers"]], ["front-1", "pcb-1"])
        self.assertTrue(all(l["status"] == "unconfirmed" for l in added["manifest"]["layers"]))
        confirmed = explosion.layout_action(self.root, "s1", {"action": "confirm", "job": "front-1", "label": "Front shell", "flip_horizontal": False})
        self.assertTrue(confirmed["layout"]["layers"][0]["confirmed_by_operator"])
        self.assertEqual(confirmed["manifest"]["layers"][0]["status"], "confirmed")  # one click binds the drawing too
        moved = explosion.layout_action(self.root, "s1", {"action": "reorder", "jobs": ["pcb-1", "front-1"]})
        self.assertEqual([l["job"] for l in moved["layout"]["layers"]], ["pcb-1", "front-1"])
        self.assertFalse(moved["layout"]["layers"][1]["confirmed_by_operator"])  # moved: confirmation withdrawn
        self.assertEqual(moved["files"]["pcb-1"], ["drawing.svg", "overlay.jpg", "rectified.png"])
        self.assertFalse((self.session / "memory").exists())

    def test_saved_drawing_binding_and_layout_survive_a_fresh_view(self):
        drawings.bind(self.root, "s1", "front-1", "confirm", "synthetic test binding")
        explosion.layout_action(self.root, "s1", {"action": "propose"})
        explosion.layout_action(self.root, "s1", {"action": "confirm", "job": "front-1", "label": "Front shell",
                                                    "view_side": "interior"})

        reloaded = explosion.explosion_view(self.root, "s1")

        self.assertEqual([layer["job"] for layer in reloaded["layout"]["layers"]], ["front-1", "pcb-1"])
        self.assertTrue(reloaded["layout"]["layers"][0]["confirmed_by_operator"])
        self.assertEqual(reloaded["manifest"]["layers"][0]["binding"], "confirmed")
        self.assertEqual(reloaded["manifest"]["layers"][0]["status"], "confirmed")

    def test_bad_actions_and_bodies_are_refused_with_a_status(self):
        cases = [({"action": "delete"}, 400), ({"action": "confirm", "job": "front-1"}, 409),  # no layout yet
                 ({"action": "reorder", "jobs": "front-1"}, 400), ({"action": "confirm", "job": 5}, 400)]
        for body, status in cases:
            with self.assertRaises(AdapterError, msg=str(body)) as ctx:
                explosion.layout_action(self.root, "s1", body)
            self.assertEqual(ctx.exception.status, status, str(body))
        explosion.layout_action(self.root, "s1", {"action": "propose"})
        for body in ({"action": "confirm", "job": "front-1", "flip_horizontal": "yes"}, {"action": "confirm", "job": "nope-1"},
                     {"action": "confirm", "job": "front-1", "view_side": ""}, {"action": "reorder", "jobs": ["front-1"]}):
            with self.assertRaises(AdapterError, msg=str(body)):
                explosion.layout_action(self.root, "s1", body)

    def test_composed_svg_and_job_files_are_served_from_a_whitelist(self):
        explosion.layout_action(self.root, "s1", {"action": "propose"})
        body, kind = explosion.explosion_bytes(self.root, "s1", "explosion.svg", "", "assembled")
        self.assertEqual(kind, "image/svg+xml")
        self.assertIn(b'data-mode="assembled"', body)
        self.assertEqual(body.count(b'class="ax-part"'), 2)
        self.assertEqual(explosion.explosion_bytes(self.root, "s1", "rectified.png", "front-1"), (b"\x89PNGphoto", "image/png"))
        self.assertEqual(explosion.explosion_bytes(self.root, "s1", "overlay.jpg", "front-1")[1], "image/jpeg")
        self.assertEqual(explosion.explosion_bytes(self.root, "s1", "drawing.svg", "pcb-1")[1], "image/svg+xml")

    def test_no_paths_unknown_names_or_unknown_sessions(self):
        explosion.layout_action(self.root, "s1", {"action": "propose"})
        refused = [("s1", "progress.json", "front-1"), ("s1", "drawing.svg", "../s1"), ("s1", "drawing.svg", "front-1/.."),
                   ("s1", "drawing.svg", "stray"), ("s1", "drawing.svg", "gone-1"), ("s1", "../x", ""), ("s1", "drawing.svg", ""),
                   ("..", "explosion.svg", ""), ("nosuch", "explosion.svg", ""), ("s1", "rectified.png", "stray")]
        for session, name, job in refused:
            with self.assertRaises(AdapterError, msg=f"{session}/{name}/{job}"):
                explosion.explosion_bytes(self.root, session, name, job)
        with self.assertRaises(AdapterError):
            explosion.explosion_view(self.root, "..")

    def test_composed_svg_without_a_layout_is_a_409_not_a_guess(self):
        with self.assertRaises(AdapterError) as ctx:
            explosion.explosion_bytes(self.root, "s1", "explosion.svg")
        self.assertEqual(ctx.exception.status, 409)

    def test_a_corrupt_spec_is_reported_in_the_view(self):
        (self.session / "drawings" / "explosion.json").write_text("{broken", encoding="utf-8")
        view = explosion.explosion_view(self.root, "s1")
        self.assertIn("not valid JSON", view["error"])
        self.assertIsNone(view["manifest"])

    def test_rotation_turns_the_drawing_and_clears_the_confirmation(self):
        explosion.layout_action(self.root, "s1", {"action": "propose"})
        view = explosion.layout_action(self.root, "s1", {"action": "confirm", "job": "front-1", "view_side": "inner"})
        before = next(l for l in view["manifest"]["layers"] if l["job"] == "front-1")
        self.assertEqual(before["outline_size_mm"], [60.0, 40.0])
        view = explosion.layout_action(self.root, "s1", {"action": "rotate", "job": "front-1"})
        after = next(l for l in view["manifest"]["layers"] if l["job"] == "front-1")
        self.assertEqual((after["rotation_deg"], after["outline_size_mm"]), (90, [40.0, 60.0]))
        self.assertFalse(after["confirmed_by_operator"])
        self.assertIn("rotate(90 ", explosion.explosion_bytes(self.root, "s1", "explosion.svg")[0].decode("utf-8"))

    def test_find_outlines_reports_what_it_would_not_draw(self):
        (self.root / "s1" / "agent-session.json").write_text(json.dumps({"phases": [{"video": "v.mp4"}]}), encoding="utf-8")
        done = _Done()
        done.stdout = json.dumps({"jobs": [], "not_drawn": [{"part": "part 5", "reason": "SAM mask failed checks"}]})
        view = explosion.find_outlines(self.root, "s1", run=lambda *a, **k: done)
        self.assertEqual(view["not_drawn"][0]["part"], "part 5")

    def test_find_outlines_reports_a_tool_failure_and_needs_a_video(self):
        with self.assertRaises(AdapterError) as missing:
            explosion.find_outlines(self.root, "s1", run=lambda *a, **k: _Done())
        self.assertEqual(missing.exception.status, 409)
        (self.root / "s1" / "agent-session.json").write_text(json.dumps({"phases": [{"video": "v.mp4"}]}), encoding="utf-8")
        with self.assertRaises(AdapterError) as failed:
            explosion.find_outlines(self.root, "s1", run=lambda *a, **k: _Done(1, "no frame shows parts"))
        self.assertIn("no frame shows parts", str(failed.exception))


if __name__ == "__main__":
    unittest.main()
