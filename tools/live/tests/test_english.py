"""English product language: interpretation tokens, read-back, rubric R1-R8 and the English wording gate.

The German regression (seed/h3r0.py rubric, German tokens) is unchanged and covered by the other suites.
"""

import unittest

import support  # noqa: F401  (path setup)

import english  # noqa: E402
from gate import wording_gate  # noqa: E402
from languages import ENGLISH, GERMAN, language  # noqa: E402

PROMPT = {"screen_options": [{"token": "A", "removal_event": "E-1", "description": "close to where the part in cell 3 came from",
                              "spoken_number": "one", "claimed_by": None},
                             {"token": "B", "removal_event": "E-2", "description": None, "spoken_number": None,
                              "claimed_by": "L button"}],
          "text": "x", "mode": "open_with_screen_options", "anchor_possible": False,
          "facts": {"phase_words": "while opening the housing", "cell": "2", "time": "0:54", "options": []}}


def typed(text):
    return {"source": "typed", "text": text}


class Interpretation(unittest.TestCase):
    def test_confirmation_tokens(self):
        for text in ("yes", "Yes.", "y", "correct", "That's right", "OK"):
            self.assertEqual(english.interpret_read_back(typed(text))["intent"], "confirm", text)
        for text in ("no", "No!", "wrong", "incorrect"):
            self.assertEqual(english.interpret_read_back(typed(text))["intent"], "reject", text)
        for text in ("ja", "nein", "maybe", "yes and no", ""):
            self.assertEqual(english.interpret_read_back(typed(text))["intent"], "unclear", text)
        self.assertEqual(english.interpret_read_back({"source": "screen_choice", "choice": "yes"})["intent"], "confirm")

    def test_answers(self):
        cases = {"skip": "skip", "ask me later": "skip", "I don't know": "unsure", "no idea": "unsure",
                 "yes": "unclear", "ja": "describe_place"}
        for text, intent in cases.items():
            self.assertEqual(english.interpret_answer(typed(text), "source_of_part", PROMPT)["intent"], intent, text)
        self.assertTrue(english.interpret_answer(typed("That's not a part"), "source_of_part", PROMPT)["not_a_part"])
        self.assertEqual(english.interpret_answer(typed("one"), "source_of_part", PROMPT)["choice"], "A")
        self.assertEqual(english.interpret_answer(typed("option b"), "source_of_part", PROMPT)["choice"], "B")
        chosen = english.interpret_answer(typed("R button from A"), "source_of_part", PROMPT)
        self.assertEqual((chosen["choice"], chosen["label_text"]), ("A", "R button"))
        free = english.interpret_answer(typed("Schraube from top-left screw hole H2"), "source_of_part", PROMPT)
        self.assertEqual((free["label_text"], free["place_text"]), ("Schraube", "top-left screw hole H2"))
        dest = english.interpret_answer(typed("Start button to cell 4"), "destination_of_removal", PROMPT)
        self.assertEqual(english.destination_value(dest["place_text"]), "cell-4")
        self.assertEqual(english.destination_value("outside the camera view"), "outside the camera view")

    def test_read_back_keeps_operator_words_verbatim(self):
        question = {"kind": "source_of_part", "cell": "cell-2"}
        answer = {"label": "weisse Gummi-Membranen", "choice": None, "source": "aus der Rückseite", "not_a_part": False}
        text = english.read_back(question, answer, PROMPT)
        self.assertEqual(text, "I will record: “weisse Gummi-Membranen” came from “aus der Rückseite” "
                               "and is in cell 2. Is that correct? Answer yes or no.")
        self.assertEqual(english.german_words(text), [])  # German only inside the quoted operator words


class Rubric(unittest.TestCase):
    def test_r2_uses_the_operator_phase_words_and_r8_rejects_german(self):
        good = "While opening the housing: where did the first part you put in cell 2 come from? (placed at about 0:54)"
        self.assertTrue(english.rubric(good, False, False, "while opening the housing")["pass"])
        german = "Beim Öffnen des Gehäuses: Woher kam das Teil in Zelle 2? (abgelegt bei etwa 0:54)"
        checks = english.rubric(german, False, False, "while opening the housing")
        self.assertFalse(checks["R2_phase_in_words"])
        self.assertFalse(checks["R8_english"])
        self.assertFalse(english.rubric("Was it from E-0035?", False, False, None)["pass"])

    def test_the_english_gate_needs_the_screen_word(self):
        output = {"text": "While opening the housing: where did the first part in cell 2 come from?", "facts_used": []}
        self.assertIsNone(wording_gate(output, PROMPT, ENGLISH)[0])
        output["text"] += " The spots are marked on the screen."
        self.assertIsNotNone(wording_gate(output, PROMPT, ENGLISH)[0])
        self.assertIsNone(wording_gate(output, PROMPT, GERMAN)[0])  # German rubric: no "beim"

    def test_unknown_language_is_refused(self):
        with self.assertRaises(ValueError):
            language("fr")


if __name__ == "__main__":
    unittest.main()
