"""EO-1 … EO-7: exactly-once memory writes, recovery, locking and fail-closed stops (cf. seed/probe.py X1–X6)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import (FIXTURES, LIVE, OPERATOR, SimulatedCrash, content, crash_at, die, in_process_memory_call, memory_lines,
                     open_coordinator, typed, without_session_id, workspace)

import states as st  # noqa: E402
from contracts import answers  # noqa: E402
from coordinator import Stopped  # noqa: E402
from journal import Journal  # noqa: E402
from lock import LockHeld  # noqa: E402
from memory_writer import STEPS, MemoryWriter, census  # noqa: E402

CRASH_POINTS = ["after_intent", "after_memory:declared_removal", "after_journal:declared_removal",
                "after_memory:storage_link", "after_journal:storage_link"]
ANSWER = "Schraube aus dem Loch oben links an der Rückplatte"


def clean(c: dict, declared: int) -> bool:
    return (c["declared_removal"] == declared and c["storage_link"] == declared and not c["orphan_declared_without_storage"]
            and not c["duplicate_op_keys"] and not c["observations_without_tray_link"])


def live_answers() -> list[dict]:
    """The FULL-01 operator answers (apply_input.jsonl) a live session could have asked for (no EXTRA)."""
    lines = (FIXTURES / "audit" / "apply_input.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip() and json.loads(line)["question"] != "EXTRA"]


class LiveCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ws = workspace(self.tmp)
        self.open = []

    def tearDown(self):
        for c in self.open:
            c.close()
        self._tmp.cleanup()

    def coordinator(self, **kwargs):
        c = open_coordinator(self.ws, **kwargs)
        self.open.append(c)
        return c

    def to_read_back(self, c, qid="Q-P1-01", text=ANSWER):
        op_key = c.ask(qid)["op_key"]
        reply = c.receive(typed(op_key, text))
        self.assertEqual(reply["status"], "read_back", reply)
        return op_key


class EO1CrashAfterEachStep(LiveCase):
    def test_crash_at_every_point_then_recovery_gives_a_clean_census(self):
        reference = None
        for point in CRASH_POINTS:
            with self.subTest(point=point):
                ws = workspace(self.tmp / point.replace(":", "_"))
                c = open_coordinator(ws, fault=crash_at(point))
                op_key = c.ask("Q-P1-01")["op_key"]
                c.receive(typed(op_key, ANSWER))
                with self.assertRaises(SimulatedCrash):
                    c.receive(typed(op_key, "ja"))
                die(c)
                again = open_coordinator(ws)
                self.open.append(again)
                self.assertIsNone(again.stopped)
                self.assertEqual(again.states[op_key].state, st.COMMITTED)
                self.assertTrue(clean(census(memory_lines(ws)), declared=1), census(memory_lines(ws)))
                written = without_session_id(memory_lines(ws)[7:], again.live_session_id)
                reference = reference or written
                self.assertEqual(written, reference)  # same events whichever step the crash hit
                recoveries = again.journal.of_kind("recovery")
                self.assertEqual(len(recoveries), 1)

    def test_crash_after_every_memory_call_of_the_full01_answers(self):
        """All 13 live FULL-01 answers; crash after memory call n, recover, continue: same census as answers.apply."""
        items = live_answers()
        questions, _ = answers.load_runs(self.ws["runs"])
        reference_ws = workspace(self.tmp / "reference")
        prepared = [{**a, "provenance": f"live op_key=live-test:{a['question']}:1; replayed"} for a in items]
        answers.apply(prepared, questions, reference_ws["memory_dir"], OPERATOR)
        reference = [content(e) for e in memory_lines(reference_ws)]
        calls = sum(len(STEPS) for a in prepared if "dismissed" not in answers.plan(a, questions))
        for n in range(1, calls + 1):
            with self.subTest(crash_after_memory_call=n):
                ws = workspace(self.tmp / f"x2-{n}")
                count = {"n": 0}

                def fault(at):
                    if at.startswith("after_memory:"):
                        count["n"] += 1
                        if count["n"] == n:
                            raise SimulatedCrash(at)
                journal = Journal(ws["run_dir"] / "journal.jsonl")
                writer = MemoryWriter(ws["memory_dir"], journal, OPERATOR, fault=fault, call=in_process_memory_call)
                with self.assertRaises(SimulatedCrash):
                    for a in prepared:
                        writer.write(a["provenance"].split("=")[1].split(";")[0], a["question"], a, answers.plan(a, questions))
                journal = Journal(ws["run_dir"] / "journal.jsonl")
                journal.load()
                writer = MemoryWriter(ws["memory_dir"], journal, OPERATOR, call=in_process_memory_call)
                done = {r["op_key"] for r in journal.of_kind("done")}
                started = {r["op_key"] for r in journal.of_kind("intent")}
                for intent in journal.of_kind("intent"):
                    if intent["op_key"] not in done:
                        writer.recover(intent)
                for a in prepared:
                    op_key = a["provenance"].split("=")[1].split(";")[0]
                    if op_key not in started:
                        writer.write(op_key, a["question"], a, answers.plan(a, questions))
                self.assertEqual(census(memory_lines(ws)), census(memory_lines(reference_ws)))
                self.assertEqual([content(e) for e in memory_lines(ws)], reference)


class EO2DuplicateDelivery(LiveCase):
    def test_the_same_input_twice_writes_once_also_after_a_restart(self):
        c = self.coordinator()
        op_key = self.to_read_back(c)
        confirm = typed(op_key, "ja")
        self.assertEqual(c.receive(confirm)["status"], "committed")
        self.assertEqual(c.receive(confirm)["status"], "duplicate")
        self.assertEqual(c.receive(typed(op_key, "ja"))["status"], "ignored")  # a new "yes" after the commit
        c.close()
        again = self.coordinator()
        self.assertEqual(again.receive(confirm)["status"], "duplicate")
        self.assertTrue(clean(census(memory_lines(self.ws)), declared=1))

    def test_a_duplicated_answer_does_not_count_as_a_read_back_reply(self):
        c = self.coordinator()
        op_key = c.ask("Q-P1-01")["op_key"]
        answer = typed(op_key, ANSWER)
        c.receive(answer)
        self.assertEqual(c.receive(answer)["status"], "duplicate")
        self.assertEqual(c.states[op_key].state, st.READ_BACK)


class EO3CrashBetweenMemoryAndJournal(LiveCase):
    def test_events_written_but_not_journaled_are_found_not_rewritten(self):
        for point in ("after_memory:declared_removal", "after_memory:storage_link"):
            with self.subTest(point=point):
                ws = workspace(self.tmp / point.replace(":", "_"))
                c = open_coordinator(ws, fault=crash_at(point))
                op_key = c.ask("Q-P1-01")["op_key"]
                c.receive(typed(op_key, ANSWER))
                with self.assertRaises(SimulatedCrash):
                    c.receive(typed(op_key, "ja"))
                die(c)
                before = memory_lines(ws)
                again = open_coordinator(ws)
                self.open.append(again)
                recovery = again.journal.of_kind("recovery")[0]
                if point == "after_memory:storage_link":
                    self.assertEqual(memory_lines(ws), before)  # nothing new: both steps were already in memory
                    self.assertEqual(recovery["completing"], [])
                else:
                    self.assertEqual(recovery["completing"], ["storage_link"])
                    self.assertEqual(len(memory_lines(ws)), len(before) + 1)
                self.assertIn("declared_removal", recovery["found"])
                scanned = [r for r in again.journal.of_kind("memory_result") if r.get("via") == "recovery_scan"]
                self.assertTrue(scanned)
                self.assertTrue(clean(census(memory_lines(ws)), declared=1))

    def test_an_acknowledged_event_missing_from_memory_stops_recovery(self):
        c = self.coordinator(fault=crash_at("after_journal:declared_removal"))
        op_key = self.to_read_back(c)
        with self.assertRaises(SimulatedCrash):
            c.receive(typed(op_key, "ja"))
        die(c)
        lines = (self.ws["memory_dir"] / "memory.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
        (self.ws["memory_dir"] / "memory.jsonl").write_text("".join(lines[:-1]), encoding="utf-8")  # e.g. lost without fsync
        again = self.coordinator()
        self.assertIn("memory lost or changed an acknowledged event", again.stopped)
        self.assertEqual(len(memory_lines(self.ws)), 7)


class EO4SecondCoordinatorRejected(LiveCase):
    def test_second_coordinator_in_this_process_is_rejected(self):
        self.coordinator()
        with self.assertRaises(LockHeld):
            open_coordinator(self.ws)

    def test_second_coordinator_in_another_process_is_rejected_until_the_first_closes(self):
        first = self.coordinator()
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); from lock import SessionLock, LockHeld\n"
                 "try:\n    SessionLock(sys.argv[2]).acquire({'probe': 1}); print('acquired')\n"
                 "except LockHeld: print('rejected')")
        run = lambda: subprocess.run([sys.executable, "-c", probe, str(LIVE), str(self.ws["memory_dir"])],
                                     capture_output=True, text=True).stdout.strip()
        self.assertEqual(run(), "rejected")
        first.close()
        self.assertEqual(run(), "acquired")

    def test_a_crashed_coordinator_leaves_no_stale_lock(self):
        c = self.coordinator()
        die(c)
        self.assertIsNone(self.coordinator().stopped)


class EO5TornLastLine(LiveCase):
    def test_torn_journal_line_stops_without_truncation(self):
        c = self.coordinator()
        self.to_read_back(c)
        c.close()
        path = self.ws["run_dir"] / "journal.jsonl"
        with path.open("ab") as fh:
            fh.write(b'{"schema": "anastylos-live-journal@1", "seq": 99, "kin')
        before = path.read_bytes()
        again = self.coordinator()
        self.assertIn("torn", again.stopped)
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(Stopped):
            again.ask("Q-P1-02")

    def test_torn_memory_line_stops_before_any_write(self):
        with (self.ws["memory_dir"] / "memory.jsonl").open("a", encoding="utf-8") as fh:
            fh.write('{"type": "declared_removal", "id": "dec-0099", "sess')
        before = (self.ws["memory_dir"] / "memory.jsonl").read_bytes()
        c = self.coordinator()
        self.assertIn("memory is unreadable", c.stopped)
        self.assertEqual((self.ws["memory_dir"] / "memory.jsonl").read_bytes(), before)


class EO6StaleRevision(LiveCase):
    def change_candidates(self):
        path = self.ws["runs"][1] / "questions.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["questions"][0]["candidates"] = data["questions"][0]["candidates"][:1]  # re-link left one candidate
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_confirmation_for_an_older_revision_is_not_committed(self):
        c = self.coordinator()
        old = self.to_read_back(c, "Q-P2-01", "L-Taste aus B")
        self.change_candidates()
        self.assertEqual(c.reload_questions(), ["Q-P2-01"])
        self.assertEqual(c.receive(typed(old, "ja"))["status"], "stale")
        self.assertEqual(c.states[old].state, st.SUPERSEDED)
        self.assertEqual(c.state_of("Q-P2-01").state, st.OPEN)
        self.assertTrue(c.op_key("Q-P2-01").endswith(":Q-P2-01:2"))
        self.assertEqual(len(memory_lines(self.ws)), 7)
        self.assertEqual(c.journal.of_kind("intent"), [])

    def test_revision_survives_a_restart(self):
        c = self.coordinator()
        old = self.to_read_back(c, "Q-P2-01", "L-Taste aus B")
        self.change_candidates()
        c.reload_questions()
        c.close()
        again = self.coordinator()
        self.assertEqual(again.receive(typed(old, "ja"))["status"], "stale")
        self.assertEqual(again.book.revision("Q-P2-01"), 2)


class EO7ImportWithoutTrayLink(LiveCase):
    def test_observation_without_tray_link_is_detected_and_stops(self):
        path = self.ws["memory_dir"] / "memory.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text("".join(l for l in lines if '"id": "link-0001"' not in l), encoding="utf-8")
        before = path.read_bytes()
        c = self.coordinator()
        self.assertIn("obs-0001", c.stopped)
        self.assertIn("tray link", c.stopped)
        self.assertEqual(census(memory_lines(self.ws))["observations_without_tray_link"], ["obs-0001"])
        self.assertEqual(path.read_bytes(), before)


class AnswersPathEquivalence(LiveCase):
    def test_live_write_equals_answers_apply_for_the_same_answer(self):
        c = self.coordinator()
        op_key = self.to_read_back(c, "Q-P2-01", "L-Taste aus B")
        answer = c.states[op_key].answer
        self.assertEqual(c.receive(typed(op_key, "ja"))["status"], "committed")
        reference_ws = workspace(self.tmp / "reference")
        answers.apply([answer], answers.load_runs(reference_ws["runs"])[0], reference_ws["memory_dir"], OPERATOR)
        self.assertEqual([content(e) for e in memory_lines(self.ws)], [content(e) for e in memory_lines(reference_ws)])
        self.assertIn("E-0049", memory_lines(self.ws)[7]["source"])


if __name__ == "__main__":
    unittest.main()
