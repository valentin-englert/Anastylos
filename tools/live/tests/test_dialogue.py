"""Dialogue agent: catalog, personas, wake word, Muse executor (fake model), the one-re-ask rule."""

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (path setup)

from catalog import selector  # noqa: E402
from dialogue import settings, wake  # noqa: E402
from dialogue.agent import DialogueAgent  # noqa: E402
from dialogue.executor import Executor, MuseClient, extract_by_rule, ground  # noqa: E402
from meta import TransportFailure, TransportTimeout  # noqa: E402

REASK = "I didn't quite catch that—could you please repeat it?"
DEFER = "Why don't we resolve this later on the desktop? I'll make a note of the uncertainty."


class FakeTransport:
    """Answers every call with one scripted output (dict), exception or HTTP status."""

    def __init__(self, reply):
        self.reply, self.posts = reply, 0

    def post(self, url, headers, body, timeout_s):
        self.posts += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        if isinstance(self.reply, int):
            return self.reply, b"{}"
        response = {"status": "completed", "model": "m", "usage": {"input_tokens": 5, "output_tokens": 2},
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(self.reply)}]}]}
        return 200, json.dumps(response).encode()


def client(reply, **kw):
    return MuseClient(transport=FakeTransport(reply), environ={"MODEL_API_KEY": "k-test"}, **kw)


class CatalogTests(unittest.TestCase):
    def test_every_type_and_persona_has_four_to_six_variants_with_known_slots(self):
        for kind in (*selector.QUESTION_TYPES, "ack"):
            for persona in selector.PERSONAS:
                found = selector.variants(kind, persona)
                self.assertTrue(len(found) >= (12 if kind in ("recorded", "cell_open") else 4), (kind, persona))
                slots = {n: "x" for n in selector.CATALOG["slots"][kind]}
                for index in range(len(found)):
                    text, _ = selector.render(kind, persona, f"e{index}", slots)
                    self.assertNotIn("{", text)

    def test_selection_is_deterministic_and_avoids_recent_variants(self):
        first = selector.pick_index("ev-1", "part_removed", "playful")
        self.assertEqual(first, selector.pick_index("ev-1", "part_removed", "playful"))
        self.assertNotEqual(first, selector.pick_index("ev-1", "part_removed", "playful", recent=(first,)))
        every = tuple(range(5))
        self.assertEqual(first, selector.pick_index("ev-1", "part_removed", "playful", recent=every))

    def test_unknown_cell_is_the_sheet_never_a_guess(self):
        for cell in (None, "", "banana", "cell-0", "cell-x", 0):
            self.assertEqual(selector.where_words(cell), "the sheet")
        self.assertEqual(selector.where_words("cell-4"), "cell 4")
        self.assertEqual(selector.where_words(7), "cell 7")
        text, _ = selector.render("part_removed", "serious", "e", {"where": selector.where_words(None)})
        self.assertIn("the sheet", text)

    def test_missing_slot_fails_closed(self):
        with self.assertRaises(KeyError):
            selector.render("read_back", "serious", "e", {})

    def test_fixed_lines_keep_their_meaning_in_every_persona(self):
        core = "I will record: “back shell” came from option 1 (left) and is in cell 4. Is that correct? Answer yes or no."
        for persona in selector.PERSONAS:
            for index, template in enumerate(selector.variants("read_back", persona)):
                self.assertIn(core, selector.render("read_back", persona, f"e{index}", {"core": core})[0])
            for line in selector.variants("defer_to_desktop", persona):
                self.assertIn(DEFER, line)
            for line in selector.variants("unclear_answer", persona):
                self.assertIn("could you please repeat it?", line.lower().replace("could", "could"))
        self.assertEqual(selector.variants("unclear_answer", "serious")[0], REASK)


class WakeAndSettingsTests(unittest.TestCase):
    def test_default_names_and_custom_name(self):
        self.assertEqual(wake.asr_keywords(""), ["Muse", "Meta"])
        self.assertEqual(wake.asr_keywords("Herbert"), ["Herbert"])
        self.assertIn("hubert", wake.mishearings("Herbert"))

    def test_wake_word_is_stripped_including_mishearings(self):
        self.assertEqual(wake.strip_wake("Hey Herbert, it's a screw", "Herbert"), (True, "it's a screw"))
        self.assertEqual(wake.strip_wake("Hubert what is that", "Herbert"), (True, "what is that"))
        self.assertEqual(wake.strip_wake("Muse next question", ""), (True, "next question"))
        self.assertEqual(wake.strip_wake("it's a screw", "Herbert"), (False, "it's a screw"))
        self.assertFalse(wake.strip_wake("Herbert", "Muse")[0])

    def test_settings_are_stored_per_workspace_and_validated(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            self.assertEqual(settings.load(Path(one))["persona"], "serious")
            settings.save(Path(one), persona="unhinged", agent_name="Herbert")
            self.assertEqual(settings.load(Path(one))["persona"], "unhinged")
            self.assertEqual(settings.load(Path(two))["persona"], "serious")
            view = settings.public_view(Path(one))
            self.assertEqual(view["asr_keywords"], ["Herbert"])
            with self.assertRaises(settings.SettingsError):
                settings.save(Path(one), persona="angry")
            with self.assertRaises(settings.SettingsError):
                settings.save(Path(one), agent_name="Bad<script>")
            self.assertEqual(settings.save(Path(one), agent_name="")["agent_name"], "")
            (Path(one) / settings.FILE_NAME).write_text("{broken", encoding="utf-8")
            self.assertEqual(settings.load(Path(one))["persona"], "serious")


class ExecutorTests(unittest.TestCase):
    def test_rule_extraction(self):
        said = extract_by_rule("It's a back shell from the left side in cell four")
        self.assertEqual((said["part_name"], said["origin"], said["storage"]), ("back shell", "the left side", "cell four"))
        self.assertEqual(extract_by_rule("yes")["intent"], "yes")
        self.assertEqual(extract_by_rule("I don't know")["intent"], "unsure")
        self.assertEqual(extract_by_rule("")["intent"], "unclear")
        self.assertEqual(extract_by_rule("D-pad")["part_name"], "D-pad")

    def test_only_words_the_user_said_survive(self):
        fields, dropped = ground({"intent": "answer", "part_name": "trigger spring", "origin": "left", "storage": None},
                                 "the left one")
        self.assertEqual((fields["part_name"], fields["origin"], dropped), (None, "left", ["part_name"]))
        fields, _ = ground({"intent": "answer", "part_name": "magic", "origin": None, "storage": None}, "hmm")
        self.assertEqual(fields["intent"], "unclear")

    def test_muse_extraction_and_choice_are_validated(self):
        good = {"intent": "answer", "part_name": "back shell", "origin": None, "storage": None, "count": None}
        fields, source, _ = Executor(client(good)).extract("well I think that is the back shell really", "part_removed")
        self.assertEqual((source, fields["part_name"]), ("muse", "back shell"))
        self.assertEqual(Executor(client({"question_type": "need_photo"})).choose({}, ["part_removed"]), ("part_removed", "rule"))
        self.assertEqual(Executor(client({"question_type": "none"})).choose({}, ["part_removed"]), ("none", "muse"))

    def test_the_model_is_asked_only_where_the_rules_cannot_tell(self):
        transport = FakeTransport({"intent": "answer", "part_name": "x", "origin": None, "storage": None, "count": None})
        muse = MuseClient(transport=transport, environ={"MODEL_API_KEY": "k"})
        fields, source, _ = Executor(muse).extract("it's a back shell", "part_removed")
        self.assertEqual((source, fields["part_name"], transport.posts), ("rule", "back shell", 0))

    def test_a_stated_count_must_be_in_the_transcript(self):
        said = {"intent": "answer", "part_name": "screws", "origin": None, "storage": None, "count": 5}
        self.assertEqual(ground(dict(said), "five screws")[0]["count"], 5)
        self.assertIsNone(ground(dict(said), "some screws")[0]["count"])

    def test_every_failure_falls_back_to_the_rule(self):
        for reply in (TransportTimeout("t"), TransportFailure("f"), 503, {"intent": "maybe"}):
            fields, source, _ = Executor(client(reply)).extract("the trigger", "part_removed")
            self.assertEqual((source, fields["part_name"]), ("rule", "trigger"))
        fields, source, _ = Executor(MuseClient(transport=FakeTransport({}), environ={})).extract("the trigger", "x")
        self.assertEqual(source, "rule")

    def test_call_cap_and_usage_log_without_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "usage.jsonl"
            muse = client({"intent": "yes", "part_name": None, "origin": None, "storage": None, "count": None}, max_calls=1,
                          usage_log=log)
            self.assertEqual(muse.call("extract_fields", {"answer_text": "yes"}, "none")["status"], "ok")
            self.assertEqual(muse.call("extract_fields", {"answer_text": "yes"}, "none")["status"], "budget_exhausted")
            text = log.read_text(encoding="utf-8")
            self.assertEqual(len(text.splitlines()), 2)
            self.assertNotIn("k-test", text)
            self.assertIn('"input_tokens": 5', text)

    def test_invented_part_name_from_muse_is_dropped(self):
        invented = {"intent": "answer", "part_name": "trigger spring", "origin": None, "storage": None, "count": None}
        fields, source, _ = Executor(client(invented)).extract("uh the thing", "part_removed")
        self.assertEqual((source, fields["intent"], fields["part_name"]), ("muse", "unclear", None))


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.items = Path(self.tmp.name) / "open.jsonl"
        self.now = [100.0]

    def tearDown(self):
        self.tmp.cleanup()

    def agent(self, executor=None, persona="serious"):
        return DialogueAgent(persona, executor or Executor(None), open_items_path=self.items, clock=lambda: self.now[0])

    def test_catalog_question_needs_no_model_call(self):
        transport = FakeTransport({"question_type": "part_removed"})
        agent = self.agent(Executor(MuseClient(transport=transport, environ={"MODEL_API_KEY": "k"})))
        said = agent.next_question({"id": "ev-1", "type": "part_removed", "cell": "cell-4"})
        self.assertEqual(transport.posts, 0)
        self.assertIn("cell 4", said["speak"])
        unknown = self.agent().next_question({"id": "ev-2", "type": "origin_unseen"})
        self.assertEqual(unknown["type"], "origin_unseen")
        self.assertEqual(self.agent().next_question({"id": "ev-3", "type": "part_removed"})["speak"].count("the sheet"), 1)

    def test_muse_may_choose_to_ask_nothing_right_after_a_question(self):
        agent = self.agent(Executor(client({"question_type": "none"})))
        self.assertIsNotNone(agent.next_question({"id": "a", "type": "part_removed", "cell": 1})["speak"])
        self.now[0] += 2
        self.assertEqual(agent.next_question({"id": "b", "type": "part_removed", "cell": 2})["type"], "none")
        self.now[0] += 20
        self.assertIsNotNone(agent.next_question({"id": "c", "type": "part_removed", "cell": 3})["speak"])

    def test_understood_answer_is_forwarded_not_written(self):
        agent = self.agent()
        agent.next_question({"id": "ev", "type": "part_removed", "cell": 4})
        acks = []
        result = agent.on_answer("ev", "it's a back shell from the left side", ack=acks.append)
        self.assertEqual(len(acks), 1)
        self.assertEqual(result["forward"], "it's a back shell from the left side")
        self.assertEqual(result["operator_said"]["part_name"], "back shell")
        self.assertEqual(result["observed"], {"event_type": "part_removed", "cell": 4})
        self.assertFalse(result["memory_write"])
        self.assertFalse(self.items.exists())

    def test_one_reask_then_defer_with_open_item_and_no_loop(self):
        agent = self.agent()
        agent.next_question({"id": "ev", "type": "part_removed", "cell": 4})
        first = agent.on_answer("ev", "mmm")
        self.assertEqual((first["speak"], first["forward"], first["closed"]), (REASK, None, False))
        second = agent.on_answer("ev", "uhh")
        self.assertEqual(second["speak"], DEFER)
        self.assertTrue(second["closed"] and not second["memory_write"] and second["forward"] is None)
        items = [json.loads(line) for line in self.items.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["event_id"], items[0]["observed"]), ("ev", {"cell": 4}))
        for _ in range(3):
            again = agent.on_answer("ev", "still nothing")
            self.assertTrue(again["closed"])
            self.assertIsNone(again["speak"])
        self.assertEqual(len(self.items.read_text(encoding="utf-8").splitlines()), 1)
        self.assertIsNone(agent.next_question({"id": "ev", "type": "part_removed", "cell": 4})["speak"])

    def test_muse_invention_counts_as_not_understood(self):
        invented = {"intent": "answer", "part_name": "trigger spring", "origin": None, "storage": None, "count": None}
        agent = self.agent(Executor(client(invented)))
        agent.next_question({"id": "ev", "type": "part_removed", "cell": 4})
        self.assertEqual(agent.on_answer("ev", "uh the thing")["speak"], REASK)

    def test_unsure_defers_at_once_without_reask(self):
        agent = self.agent()
        agent.next_question({"id": "ev", "type": "origin_unseen"})
        result = agent.on_answer("ev", "I don't know")
        self.assertEqual(result["speak"], DEFER)
        self.assertEqual(json.loads(self.items.read_text(encoding="utf-8"))["reason"], "operator unsure")

    def test_timing_stages_and_speed_target_with_fake_model(self):
        agent = self.agent()
        agent.next_question({"id": "ev", "type": "part_removed", "cell": 4})
        self.now[0] = 110.0
        timing = agent.on_answer("ev", "d-pad", marks={"speech_end": 109.0, "transcript": 109.8})["timing"]
        self.assertEqual(timing["speech_end_to_transcript_ms"], 800)
        self.assertEqual(timing["speech_end_to_reply_ms"], 1000)
        self.assertLess(timing["speech_end_to_reply_ms"], 3000)
        self.assertEqual(set(timing), {"speech_end_to_transcript_ms", "transcript_to_ack_ms", "muse_ms", "speech_end_to_reply_ms"})

    def test_personas_differ_but_read_back_keeps_the_core(self):
        core = "I will record: “x” came from option 1 (left) and is in cell 4. Is that correct? Answer yes or no."
        lines = {p: self.agent(persona=p).read_back_line("ev", core) for p in selector.PERSONAS}
        self.assertTrue(all(core in line for line in lines.values()))
        stored = {p: self.agent(persona=p).next_question({"id": f"s{i}", "type": "part_stored", "cell": 2, "name": "D-pad"})["speak"]
                  for i, p in enumerate(selector.PERSONAS)}
        self.assertTrue(all("D-pad" in s and "cell 2" in s for s in stored.values()))


if __name__ == "__main__":
    unittest.main()
