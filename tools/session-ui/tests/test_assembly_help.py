"""Assembly help: part link, origin vs storage, coarse origin follow-up, intent matcher (offline, synthetic memory)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))

import adapter  # noqa: E402
import assembly_help  # noqa: E402
import part_intent  # noqa: E402
from adapter import AdapterError  # noqa: E402
from state import SessionState  # noqa: E402

SESSION = "memory"
EVIDENCE = {"result_json": "C:\\data\\runs\\P3\\evidence\\results\\E-0092_SRC-14\\result.json", "pair_id": "E-0092_SRC-14",
            "region": "R10", "result_sha256": "f" * 64}

EVENTS = [
    {"type": "phase", "id": "phase-0001", "session": SESSION, "timestamp": "2026-10-29T09:54:42Z", "label": "P1",
     "pose_note": None, "starts_after": None, "supersedes": None},
    {"type": "observation", "id": "obs-0001", "session": SESSION, "timestamp": "2026-10-29T10:01:48Z",
     "source_view": "surface_a3", "bbox_mm": [236.5, 137.5, 12.5, 12.25], "centroid_mm": [242.28, 143.58],
     "evidence": EVIDENCE, "kit_sha256": "a" * 64, "note": "source region region_about_part_size: covers 1 candidate",
     "t_s": 51.191, "supersedes": None},
    {"type": "declared_removal", "id": "dec-0001", "session": SESSION, "timestamp": "2026-11-01T15:17:50Z",
     "operator_label": "back screw", "source": "the back shell", "phase": "phase-0001", "t_s": 25.6,
     "observation_id": None, "declared_by": "operator (test)", "evidence_note": "typed answer Q-1", "supersedes": None},
    {"type": "storage_link", "id": "store-0001", "session": SESSION, "timestamp": "2026-11-01T15:17:51Z",
     "target_id": "dec-0001", "location": "cell-1", "basis": {"kind": "human", "confirmed_by": "operator (test)"},
     "supersedes": None},
    {"type": "declared_removal", "id": "dec-0002", "session": SESSION, "timestamp": "2026-11-01T15:20:00Z",
     "operator_label": "ribbon cable", "source": "the ribbon connector J2 on the main board", "phase": "phase-0001",
     "t_s": 40.0, "observation_id": None, "declared_by": "operator (test)", "evidence_note": "typed answer Q-2",
     "supersedes": None},
    {"type": "storage_link", "id": "store-0002", "session": SESSION, "timestamp": "2026-11-01T15:20:01Z",
     "target_id": "dec-0002", "location": "cell-2", "basis": {"kind": "human", "confirmed_by": "operator (test)"},
     "supersedes": None},
    {"type": "declared_removal", "id": "dec-0003", "session": SESSION, "timestamp": "2026-11-01T15:22:00Z",
     "operator_label": "spacer", "source": None, "phase": "phase-0001", "t_s": 51.0, "observation_id": "obs-0001",
     "declared_by": "operator (test)", "evidence_note": "typed answer Q-3", "supersedes": None},
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class HelpCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "workspace"
        self.session_dir = self.root / "s1"
        state = SessionState.create(self.session_dir, "controller", "operator (test)")
        self.memory = state.memory_dir / "memory.jsonl"
        self.memory.write_text("".join(json.dumps(e) + "\n" for e in EVENTS), encoding="utf-8")
        self.build = adapter.guide_build(self.root, "s1")["guide_build"]

    def job(self, name: str, part_text: str, svg: str = "<svg>front</svg>", status: str = "drawn_unconfirmed") -> Path:
        job = self.session_dir / "drawings" / "jobs" / name
        job.mkdir(parents=True)
        progress = {"state": "done", "pid": os.getpid(), "heartbeat_at": time.time(), "done": 7, "total": 7, "stage_s": {},
                    "result": {"status": status, "reasons": [], "spec": {"part": part_text, "scope": "assembly"}}}
        (job / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
        (job / "drawing.svg").write_text(svg, encoding="utf-8")
        (job / "overlay.jpg").write_bytes(b"\xff\xd8overlay")
        return job

    def link(self, job: str, part: str, **kwargs) -> dict:
        return assembly_help.link_part(self.root, "s1", job, part, **kwargs)

    def ask(self, target: str, question: str) -> dict:
        return assembly_help.ask_about_part(self.root, "s1", target, question)


class IntentTest(unittest.TestCase):
    def test_origin_and_storage_phrasings(self):
        origin = ["Where does this part belong?", "where does it go", "Where does this go back?", "where was this",
                  "Where did this come from?", "Please tell me where does that part belong", "where was it taken from"]
        storage = ["where did I put it", "Where did I put this part?", "where is it", "where's this part now",
                   "Hey, where did you put it?"]
        for text in origin:
            with self.subTest(text=text):
                self.assertEqual(part_intent.match(text)["intent"], "origin")
        for text in storage:
            with self.subTest(text=text):
                self.assertEqual(part_intent.match(text)["intent"], "storage")

    def test_everything_else_is_not_matched(self):
        for text in ["", "   ", "where is cell 8", "what comes before this", "where does this part belong and how heavy is it",
                     "tell me a story", "where did I put the soldering iron", "where"]:
            with self.subTest(text=text):
                self.assertIsNone(part_intent.match(text))

    def test_supported_examples_all_match(self):
        for text in part_intent.SUPPORTED:
            self.assertIsNotNone(part_intent.match(text), text)


class LinkTest(HelpCase):
    def test_candidates_are_proposed_not_linked(self):
        job = self.job("front-1", "back screw of the controller")
        before = snapshot(self.session_dir)
        view = assembly_help.link_candidates(self.root, "s1", "front-1")
        self.assertEqual(snapshot(self.session_dir), before)
        self.assertFalse((job / "part-link.json").exists())
        self.assertEqual((view["link_state"], view["link"], view["auto_link"]), ("none", None, False))
        self.assertEqual(view["candidates"][0]["part_id"], "dec-0001")
        self.assertTrue(view["candidates"][0]["suggested"])
        self.assertEqual(view["candidates"][0]["matched_tokens"], ["back", "screw"])
        self.assertEqual(sorted(c["part_id"] for c in view["candidates"]), ["dec-0001", "dec-0002", "dec-0003"])
        self.assertTrue(all(not c["suggested"] for c in view["candidates"][1:]))

    def test_no_label_match_still_lists_every_part_unsuggested(self):
        self.job("front-1", "front shell with membranes")
        view = assembly_help.link_candidates(self.root, "s1", "front-1")
        self.assertEqual(len(view["candidates"]), 3)
        self.assertFalse(any(c["suggested"] for c in view["candidates"]))

    def test_link_records_operator_decision_once(self):
        job = self.job("front-1", "back screw")
        first = self.link("front-1", "dec-0001", note="that is the screw")
        self.assertEqual(first["schema"], assembly_help.LINK_SCHEMA)
        self.assertEqual((first["job"], first["part"], first["decided_by"]),
                         ("front-1", {"id": "dec-0001", "label": "back screw"}, "operator"))
        self.assertEqual(first["drawing_sha256"], sha(job / "drawing.svg"))
        self.assertEqual(first["memory_sha256"], sha(self.memory))
        self.assertFalse(first["memory_written"])
        on_disk = json.loads((job / "part-link.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, first)
        self.assertFalse(list(job.glob("*.partial")))
        again = self.link("front-1", "dec-0001")
        self.assertTrue(again["already_recorded"])
        self.assertEqual(json.loads((job / "part-link.json").read_text(encoding="utf-8")), first)

    def test_first_decision_wins_until_explicit_supersede(self):
        job = self.job("front-1", "back screw")
        first = self.link("front-1", "dec-0001")
        with self.assertRaisesRegex(AdapterError, "already linked") as caught:
            self.link("front-1", "dec-0002")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(json.loads((job / "part-link.json").read_text(encoding="utf-8")), first)
        second = self.link("front-1", "dec-0002", supersede=True, note="wrong part before")
        self.assertEqual(second["part"]["id"], "dec-0002")
        self.assertEqual(second["seq"], 2)
        self.assertEqual(second["supersedes"], {"seq": 1, "part_id": "dec-0001", "drawing_sha256": first["drawing_sha256"]})
        self.assertEqual(second["history"], [{k: v for k, v in first.items() if k != "history"}])

    def test_link_refuses_unknown_part_rejected_or_unfinished_drawing(self):
        job = self.job("front-1", "back screw")
        with self.assertRaises(AdapterError):
            self.link("front-1", "dec-9999")
        with self.assertRaises(AdapterError):
            self.link("front-1", "obs-0001")
        with self.assertRaises(AdapterError) as missing:
            self.link("front-1", "")
        self.assertEqual(missing.exception.status, 400)
        (job / "binding.json").write_text(json.dumps({"decision": "reject"}), encoding="utf-8")
        with self.assertRaisesRegex(AdapterError, "rejected"):
            self.link("front-1", "dec-0001")
        self.job("front-2", "x", status="NEEDS_CAPTURE_REVIEW")
        with self.assertRaisesRegex(AdapterError, "cannot be linked"):
            self.link("front-2", "dec-0001")
        with self.assertRaises(AdapterError):
            self.link("../front-1", "dec-0001")

    def test_changed_drawing_makes_the_link_stale_and_needs_supersede(self):
        job = self.job("front-1", "back screw")
        self.link("front-1", "dec-0001")
        (job / "drawing.svg").write_text("<svg>redrawn</svg>", encoding="utf-8")
        self.assertEqual(assembly_help.link_candidates(self.root, "s1", "front-1")["link_state"], "stale_drawing")
        with self.assertRaisesRegex(AdapterError, "already linked"):
            self.link("front-1", "dec-0001")
        renewed = self.link("front-1", "dec-0001", supersede=True)
        self.assertEqual(renewed["drawing_sha256"], sha(job / "drawing.svg"))
        self.assertEqual(assembly_help.link_candidates(self.root, "s1", "front-1")["link_state"], "linked")


class AskTest(HelpCase):
    def setUp(self):
        super().setUp()
        self.job_dir = self.job("front-1", "back screw")
        self.link("front-1", "dec-0001")

    def test_unlinked_drawing_asks_for_a_link_with_candidates(self):
        self.job("loose-1", "ribbon cable")
        result = self.ask("loose-1", "where does this part belong?")
        self.assertEqual(result["status"], "needs_link")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["candidates"][0]["part_id"], "dec-0002")
        self.assertIn("not linked", result["message"])
        self.assertFalse((self.session_dir / "drawings" / "jobs" / "loose-1" / "part-link.json").exists())

    def test_front_shell_with_no_current_memory_record_stays_unlinked(self):
        job = self.job("front-shell-1", "front shell of the controller")
        result = self.ask("front-shell-1", "where does this part belong?")
        self.assertEqual(result["status"], "needs_link")
        self.assertIsNone(result["answer"])
        self.assertIn("if any", result["message"])
        self.assertFalse(any(candidate["suggested"] for candidate in result["candidates"]))
        self.assertFalse((job / "part-link.json").exists())

    def test_coarse_origin_gives_follow_up_and_no_install_point(self):
        result = self.ask("front-1", "where does this part belong?")
        self.assertEqual(result["status"], "coarse_origin")
        self.assertEqual(result["origin"]["precision"], "coarse_place")
        self.assertFalse(result["origin"]["concrete_install_point"])
        self.assertFalse(result["origin"]["install_point_verified"])
        self.assertIn("not a specific screw boss", result["message"])
        self.assertIn("the back shell", result["answer"]["text"])
        follow_up = result["follow_up"]
        self.assertEqual((follow_up["kind"], follow_up["recorded"]), ("install_point", False))
        self.assertIn("Which screw boss, connector or mounting point", follow_up["text"])
        self.assertIn("“the back shell”", follow_up["text"])
        kinds = [e["kind"] for e in result["evidence_to_show"]]
        self.assertEqual(kinds, ["drawing_overlay"])
        self.assertEqual(result["evidence_to_show"][0]["sha256"], sha(self.job_dir / "overlay.jpg"))
        for forbidden in ("boss", "J2"):
            self.assertNotIn(forbidden, result["answer"]["text"])

    def test_origin_and_storage_are_separate(self):
        result = self.ask("front-1", "where did I put it?")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["intent"], "storage")
        self.assertEqual(result["storage"]["location"], "cell-1")
        self.assertEqual(result["storage"]["record_id"], "store-0001")
        self.assertEqual(result["origin"]["description"], "the back shell")
        self.assertEqual(result["origin"]["record_id"], "dec-0001")
        self.assertIn("cell 1", result["answer"]["text"])
        self.assertNotIn("back shell", result["answer"]["text"])
        origin_text = result["answers"]["origin"]["text"]
        self.assertIn("back shell", origin_text)
        # The renderer names the part by its tray cell ("The part from cell 1"); the stated place is the origin only.
        self.assertNotIn("goes back to “cell", origin_text)
        origin = self.ask("front-1", "where was this?")
        self.assertEqual(origin["intent"], "origin")
        self.assertIn("goes back to “the back shell”", origin["answer"]["text"])
        self.assertNotIn("goes back to “cell", origin["answer"]["text"])
        self.assertNotIn("back shell", result["answer"]["text"])
        self.assertEqual(origin["origin"]["phase"], {"id": "phase-0001", "label": "P1"})
        self.assertEqual(origin["origin"]["recorded_at"], "2026-11-01T15:17:50Z")
        self.assertEqual(origin["origin"]["declared_by"], "operator (test)")

    def test_answer_cites_package_and_memory_hash(self):
        for question in ("where does it go", "where did I put it"):
            result = self.ask("front-1", question)
            self.assertEqual(result["package_sha256"], self.build["package_sha256"])
            self.assertEqual(result["package_sha256"], sha(self.session_dir / "guide" / "session.json"))
            self.assertEqual(result["memory_sha256"], sha(self.memory))
            self.assertEqual(result["answer"]["package_sha256"], result["package_sha256"])
            self.assertTrue(result["answer"]["accepted"])
            self.assertEqual(result["answer"]["outcome"], "supported")
            self.assertEqual(result["answer"]["source"], "L0")

    def test_named_install_point_is_answered_but_stays_operator_words(self):
        self.job("cable-1", "ribbon cable")
        self.link("cable-1", "dec-0002")
        result = self.ask("cable-1", "where does it go")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["origin"]["precision"], "named_install_point")
        self.assertTrue(result["origin"]["concrete_install_point"])
        self.assertFalse(result["origin"]["install_point_verified"])
        self.assertIn("ribbon connector J2", result["answer"]["text"])
        self.assertIn("according to the operator", result["answer"]["text"])
        self.assertIsNone(result["follow_up"])

    def test_region_only_origin_with_evidence_refs(self):
        self.job("spacer-1", "spacer")
        self.link("spacer-1", "dec-0003")
        result = self.ask("spacer-1", "where was this")
        self.assertEqual(result["origin"]["precision"], "sheet_region")
        self.assertEqual(result["status"], "coarse_origin")
        self.assertEqual(result["origin"]["observation"]["id"], "obs-0001")
        self.assertEqual(result["origin"]["evidence_refs"],
                         [{"kind": "memory_evidence", "as_stored": True, "pair_id": "E-0092_SRC-14", "region": "R10",
                           "result_sha256": "f" * 64, "file": "result.json"}])
        self.assertEqual([e["kind"] for e in result["evidence_to_show"]], ["drawing_overlay", "memory_evidence"])
        self.assertEqual(result["origin"]["observation"]["source_view"], "surface_a3")
        storage = self.ask("spacer-1", "where did I put it")
        self.assertEqual(storage["status"], "storage_unknown")
        self.assertIsNone(storage["storage"]["location"])
        self.assertEqual(storage["follow_up"]["kind"], "storage_place")

    def test_part_id_can_be_asked_without_a_drawing(self):
        result = self.ask("dec-0002", "where did I put it")
        self.assertEqual((result["status"], result["target"]["kind"]), ("answered", "part"))
        self.assertEqual(result["storage"]["location"], "cell-2")
        with self.assertRaises(AdapterError) as unknown:
            self.ask("dec-0099", "where did I put it")
        self.assertEqual(unknown.exception.status, 404)

    def test_unsupported_question_is_not_guessed(self):
        result = self.ask("front-1", "how heavy is it")
        self.assertEqual(result["status"], "unsupported_question")
        self.assertIsNone(result["answer"])
        self.assertIn("where does this part belong", result["message"])
        with self.assertRaises(AdapterError):
            self.ask("front-1", "  ")

    def test_stale_guide_answers_nothing(self):
        with open(self.memory, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"type": "storage_link", "id": "store-0009", "session": SESSION,
                                     "timestamp": "2026-11-01T16:00:00Z", "target_id": "dec-0003", "location": "cell-3",
                                     "basis": {"kind": "human", "confirmed_by": "operator (test)"}, "supersedes": None}) + "\n")
        result = self.ask("front-1", "where did I put it")
        self.assertEqual(result["status"], "guide_unusable")
        self.assertIsNone(result["answer"])
        self.assertIn("guide build", result["message"])

    def test_superseded_part_makes_the_link_stale(self):
        with open(self.memory, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({**EVENTS[2], "id": "dec-0010", "operator_label": "back screw",
                                     "source": "the back shell, lower left boss", "supersedes": "dec-0001"}) + "\n")
        adapter.guide_build(self.root, "s1")
        result = self.ask("front-1", "where does it go")
        self.assertEqual(result["status"], "link_stale")
        self.assertIn("dec-0010", [c["part_id"] for c in result["candidates"]])
        self.assertNotIn("dec-0001", [c["part_id"] for c in result["candidates"]])

    def test_asking_writes_nothing(self):
        self.job("loose-1", "x")
        before = snapshot(self.session_dir)
        for question in ("where does this part belong?", "where did I put it", "how heavy is it"):
            self.ask("front-1", question)
        self.ask("loose-1", "where does it go")
        assembly_help.link_candidates(self.root, "s1", "loose-1")
        self.assertEqual(snapshot(self.session_dir), before)
        self.assertEqual(sha(self.memory), self.build["memory_sha256"])


if __name__ == "__main__":
    unittest.main()
