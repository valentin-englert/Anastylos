"""Confirming a layer in the explosion view also links the drawing to its recorded part, so assembly help can answer."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))
sys.path.insert(0, str(UI_ROOT / "demo"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import adapter  # noqa: E402
import assembly_help  # noqa: E402
import explosion  # noqa: E402
import prepare_demo  # noqa: E402
from test_assembly_help import HelpCase  # noqa: E402
from test_explosion import POLYGONS, drawing_svg  # noqa: E402


def part_event(event_id: str, label: str, source: str | None = "back shell") -> dict:
    return {"type": "declared_removal", "id": event_id, "session": "memory", "timestamp": "2026-11-03T10:00:00Z",
            "operator_label": label, "source": source, "phase": "phase-0001", "t_s": None, "observation_id": None,
            "declared_by": "operator (test)", "evidence_note": "closing answer", "supersedes": None}


class LinkOnConfirmTest(HelpCase):
    def setUp(self):
        super().setUp()
        for name, polygon in POLYGONS.items():
            self.job(name, name.split("-")[0], svg=drawing_svg(polygon))
        explosion.layout_action(self.root, "s1", {"action": "propose"})

    def add_parts(self, *events: dict) -> None:
        with self.memory.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(json.dumps(e) + "\n" for e in events))
        adapter.guide_build(self.root, "s1")

    def confirm(self, label: str, job: str = "front-1") -> dict:
        return explosion.layout_action(self.root, "s1", {"action": "confirm", "job": job, "label": label, "view_side": "interior"})

    def link_file(self, job: str = "front-1") -> Path:
        return self.session_dir / "drawings" / "jobs" / job / assembly_help.LINK_FILE

    def test_one_clear_match_is_linked_and_assembly_help_answers_without_needs_link(self):
        self.add_parts(part_event("dec-0040", "front shell"))
        result = self.confirm("frontshell")
        self.assertEqual(result["memory"], {"status": "linked", "part": "dec-0040"})
        self.assertEqual(result["link"], {"status": "linked", "part": "dec-0040"})
        record = json.loads(self.link_file().read_text(encoding="utf-8"))
        self.assertEqual((record["part"]["id"], record["note"], record["decided_by"]),
                         ("dec-0040", "confirmed in the explosion view", "operator"))
        answer = assembly_help.ask_about_part(self.root, "s1", "front-1", "where does this part belong?")
        self.assertNotIn(answer["status"], ("needs_link", "link_stale"))
        self.assertEqual(answer["part_label"], "front shell")
        self.assertEqual(answer["link"]["part"]["id"], "dec-0040")

    def test_a_repeat_confirmation_keeps_the_link(self):
        self.add_parts(part_event("dec-0040", "front shell"))
        self.confirm("front shell")
        before = self.link_file().read_bytes()
        self.assertEqual(self.confirm("front shell")["link"]["status"], "already_linked")
        self.assertEqual(self.link_file().read_bytes(), before)

    def test_several_matches_link_nothing_and_the_panel_lists_candidates(self):
        self.add_parts(part_event("dec-0040", "front shell"), part_event("dec-0041", "Front Shell"))
        result = self.confirm("front shell")
        self.assertEqual(result["memory"]["status"], "ambiguous")
        self.assertNotIn("link", result)
        self.assertFalse(self.link_file().exists())
        candidates = assembly_help.link_candidates(self.root, "s1", "front-1")["candidates"]
        self.assertEqual([c["part_id"] for c in candidates if c["suggested"]], ["dec-0040", "dec-0041"])
        asked = assembly_help.ask_about_part(self.root, "s1", "front-1", "where does it go")
        self.assertEqual(asked["status"], "needs_link")
        self.assertEqual([c["part_id"] for c in asked["candidates"] if c["suggested"]], ["dec-0040", "dec-0041"])

    def test_a_different_existing_link_is_not_replaced(self):
        self.add_parts(part_event("dec-0040", "front shell"))
        assembly_help.link_part(self.root, "s1", "front-1", "dec-0002")
        before = self.link_file().read_bytes()
        result = self.confirm("front shell")
        self.assertEqual(result["link"]["status"], "not_linked")
        self.assertIn("already linked", result["link"]["message"])
        self.assertEqual(self.link_file().read_bytes(), before)
        self.assertEqual(result["memory"]["part"], "dec-0040")  # the confirmation itself still stands

    def test_a_stale_link_is_not_replaced_either(self):
        self.add_parts(part_event("dec-0040", "front shell"))
        self.confirm("front shell")
        (self.session_dir / "drawings" / "jobs" / "front-1" / "drawing.svg").write_text(drawing_svg(POLYGONS["pcb-1"]), encoding="utf-8")
        before = self.link_file().read_bytes()
        self.assertEqual(self.confirm("front shell")["link"]["status"], "not_linked")
        self.assertEqual(self.link_file().read_bytes(), before)

    def test_candidates_follow_the_layer_label_across_spacing_also_when_asking(self):
        self.add_parts(part_event("dec-0040", "front shell"), part_event("dec-0041", "pcb"))
        self.confirm("frontshell")
        self.link_file().unlink()  # the owner's case: confirmed, but no link yet
        candidates = assembly_help.link_candidates(self.root, "s1", "front-1")["candidates"]
        self.assertEqual((candidates[0]["part_id"], candidates[0]["suggested"], candidates[0]["score"]), ("dec-0040", True, 1.0))
        asked = assembly_help.ask_about_part(self.root, "s1", "front-1", "where does it go")
        self.assertEqual(asked["status"], "needs_link")
        self.assertEqual((asked["candidates"][0]["part_id"], asked["candidates"][0]["suggested"]), ("dec-0040", True))

    def test_a_longer_layer_name_still_suggests_the_contained_label(self):
        self.add_parts(part_event("dec-0040", "front shell"))
        memory = assembly_help.read_memory(adapter._load_state(self.root, "s1"))
        best = assembly_help._candidates("frontshell with membranes", memory)[0]
        self.assertEqual((best["part_id"], best["suggested"]), ("dec-0040", True))
        self.assertFalse(any(c["suggested"] for c in assembly_help._candidates("pcb", memory) if c["part_id"] == "dec-0040"))


class RecordedThenLinkedTest(unittest.TestCase):
    """The real path: a layer whose label matches no part is written through the coordinator, then linked, then asked."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "workspace"
        prepare_demo.make_fresh_session(self.root / "s1", phases=("P1",))
        self.session_dir = self.root / "s1"
        for name, polygon in POLYGONS.items():
            job = self.session_dir / "drawings" / "jobs" / name
            job.mkdir(parents=True)
            progress = {"state": "done", "result": {"status": "NEEDS_CONFIRMATION", "spec": {"part": name.split("-")[0], "scope": "assembly"}}}
            (job / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
            (job / "drawing.svg").write_text(drawing_svg(polygon), encoding="utf-8")
        explosion.layout_action(self.root, "s1", {"action": "propose"})

    def test_new_operator_stated_part_is_linked_and_ask_answers_after_the_guide_is_rebuilt(self):
        result = explosion.layout_action(self.root, "s1", {"action": "confirm", "job": "front-1", "label": "Front shell", "view_side": "interior"})
        self.assertEqual(result["memory"]["status"], "recorded")
        part = result["memory"]["part"]
        self.assertEqual(result["link"], {"status": "linked", "part": part})
        record = json.loads((self.session_dir / "drawings" / "jobs" / "front-1" / assembly_help.LINK_FILE).read_text(encoding="utf-8"))
        self.assertEqual((record["part"], record["decided_by"]), ({"id": part, "label": "Front shell"}, "operator"))
        built = adapter.guide_build(self.root, "s1")["guide_build"]  # the package is built from the memory that now holds the part
        asked = assembly_help.ask_about_part(self.root, "s1", "front-1", "where does this part belong?")
        self.assertNotIn(asked["status"], ("needs_link", "link_stale", "guide_unusable", "selection_refused"))
        self.assertEqual(asked["package_sha256"], built["package_sha256"])
        self.assertEqual(asked["part_label"], "Front shell")
        self.assertEqual(asked["storage"]["location"], explosion.WORK_AREA)
        again = explosion.layout_action(self.root, "s1", {"action": "confirm", "job": "front-1", "label": "Front shell", "view_side": "interior"})
        self.assertEqual((again["memory"]["status"], again["link"]["status"]), ("linked", "already_linked"))


if __name__ == "__main__":
    unittest.main()
