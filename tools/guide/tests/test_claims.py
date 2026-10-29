import hashlib
import json
import sys
import unittest
from pathlib import Path

GUIDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GUIDE))
sys.path.insert(0, str(GUIDE / "guide02"))

from claims import NOT_PROVEN, UNKNOWN_SPEECH, check, load_package, map_answer, verify  # noqa: E402
from run import meets_acceptance, score  # noqa: E402

FIXTURES = GUIDE / "guide02" / "fixtures"
V21 = (FIXTURES / "session.v21.json").read_bytes()
V21_SHA = "1b84d4c8079c001c96c0b29979276abdf5a2f3a4b89919fa7d871e84d46cc83e"
E2 = (FIXTURES / "session.e2.json").read_bytes()
CASES = json.loads((GUIDE / "guide02" / "cases.dev.json").read_text(encoding="utf-8"))
ANSWERS = {case["id"]: case["answer"] for case in CASES["cases"]}


def _claim(relation, subject, target=None, basis_word=None):
    return {"relation": relation, "subject": subject, "object": target, "basis_word": basis_word}


def _doc(*claims, insufficient=False):
    return {"schema": "anastylos-guide-claims@1", "claims": list(claims), "insufficient_evidence": insufficient}


def _claims(*claims):
    return check(V21, V21_SHA, document=_doc(*claims))


def _answer(text):
    return check(V21, V21_SHA, answer=text)


def _reasons(verdict):
    return verdict.findings + [r for c in verdict.claims for r in c["reasons"]]


class Assertions(unittest.TestCase):
    def assertRejectedFor(self, verdict, prefix):
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.speech, NOT_PROVEN)
        self.assertTrue(any(r.startswith(prefix) for r in _reasons(verdict)), _reasons(verdict))

    def assertAccepted(self, verdict):
        self.assertTrue(verdict.accepted, _reasons(verdict))
        self.assertNotEqual(verdict.speech, NOT_PROVEN)


class IdentityTest(Assertions):  # C1
    def test_dev_answers_naming_automatic_parts_by_colour_or_name_are_rejected(self):
        for case in ("C1-a", "C1-b", "C1-c", "C1-d"):
            with self.subTest(case):
                self.assertRejectedFor(_answer(ANSWERS[case]), "identity:")

    def test_colour_word_in_a_claim_about_an_automatic_part_is_rejected(self):
        self.assertRejectedFor(_claims(_claim("stored_in", "obs-0001", "cell-3", "grüner Knopf")), "identity:")

    def test_declared_label_must_be_said_as_the_operators_label(self):
        self.assertRejectedFor(_answer("In Zelle 7 liegt das D-Pad [dec-0005, store-0005]."), "identity:")


class CertaintyTest(Assertions):  # C2
    def test_dev_answers_with_certainty_words_are_rejected(self):
        for case in ("C2-a", "C2-b", "C2-c"):
            with self.subTest(case):
                self.assertRejectedFor(_answer(ANSWERS[case]), "certainty:")

    def test_certainty_basis_word_is_rejected(self):
        self.assertRejectedFor(_claims(_claim("basis", "obs-0002", "automatic", "sicher erkannt")), "certainty:")

    def test_observed_is_not_a_basis_word_for_a_link(self):
        self.assertRejectedFor(_answer("Das Teil in Zelle 7 wurde beobachtet [dec-0005, store-0005]."), "certainty:")


class SourceTest(Assertions):  # C3
    def test_dev_answers_with_swapped_sources_are_rejected(self):
        for case in ("C3-a", "C3-b"):
            with self.subTest(case):
                self.assertRejectedFor(_answer(ANSWERS[case]), "source:")

    def test_part_at_another_parts_source_is_rejected(self):
        self.assertRejectedFor(_claims(_claim("removed_from", "obs-0001", "obs-0002")), "source:")

    def test_members_of_one_interchangeable_group_share_their_sources(self):
        self.assertAccepted(_claims(_claim("removed_from", "dec-0001", "dec-0012")))

    def test_site_must_be_in_the_recorded_source(self):
        self.assertAccepted(_claims(_claim("removed_from", "dec-0012", "H2")))
        self.assertRejectedFor(_claims(_claim("removed_from", "dec-0012", "H3")), "source:")


class OrderTest(Assertions):  # C4
    def test_dev_answer_with_wrong_install_order_is_rejected(self):
        self.assertRejectedFor(_answer(ANSWERS["C4-a"]), "order:")

    def test_install_before_follows_install_order_steps(self):
        self.assertRejectedFor(_claims(_claim("install_before", "obs-0001", "obs-0002", "umgekehrte Entnahmereihenfolge")),
                               "order:")
        verdict = _claims(_claim("install_before", "obs-0002", "obs-0001", "umgekehrte Entnahmereihenfolge"))
        self.assertAccepted(verdict)
        self.assertIn("(Schritt 4) vor dem Teil aus Zelle 3 (Schritt 5)", verdict.speech)

    def test_parts_of_one_group_step_are_not_before_each_other(self):
        self.assertRejectedFor(_claims(_claim("install_before", "dec-0001", "dec-0002")), "order:")


class BasisTest(Assertions):  # C5
    def test_dev_answer_presenting_a_declared_part_as_system_assigned_is_rejected(self):
        self.assertRejectedFor(_answer(ANSWERS["C5-a"]), "basis:")

    def test_basis_must_match_the_package(self):
        self.assertRejectedFor(_claims(_claim("basis", "dec-0005", "automatic")), "basis:")
        self.assertRejectedFor(_claims(_claim("stored_in", "dec-0005", "cell-7", "vom System zugeordnet")), "basis:")
        self.assertRejectedFor(_claims(_claim("stored_in", "obs-0001", "cell-3", "laut Operator")), "basis:")
        self.assertAccepted(_claims(_claim("stored_in", "store-0005", "cell-7", "laut Operator")))

    def test_basis_word_outside_guide_rules_is_rejected(self):
        self.assertRejectedFor(_claims(_claim("stored_in", "obs-0001", "cell-3", "per Barcode")), "basis:")


class DependencyTest(Assertions):  # C6
    def test_dev_answer_with_invented_dependency_is_rejected(self):
        self.assertRejectedFor(_answer(ANSWERS["C6-a"]), "dependency:")

    def test_dependency_absent_from_the_package_is_rejected(self):
        self.assertRejectedFor(_claims(_claim("dependency", "dec-0005", "dec-0004")), "dependency:")

    def test_confirmed_dependency_is_accepted(self):
        raw = json.loads(V21)
        raw["dependencies"]["confirmed"] = [{"before": "dec-0005", "after": "dec-0004"}]
        data = json.dumps(raw).encode("utf-8")
        verdict = check(data, hashlib.sha256(data).hexdigest(), document=_doc(_claim("dependency", "dec-0005", "dec-0004")))
        self.assertAccepted(verdict)


class LaterObservationTest(Assertions):  # C7
    def test_dev_answer_using_a_current_photo_as_history_is_rejected(self):
        self.assertRejectedFor(_answer(ANSWERS["C7-a"]), "later_observation:")

    def test_later_observation_as_basis_word_is_rejected(self):
        self.assertRejectedFor(_claims(_claim("removed_from", "obs-0001", None, "aktuelles Foto")), "later_observation:")


class ControlTest(Assertions):
    def test_dev_controls_are_accepted(self):
        for case in ("K1", "K2", "K3", "K4", "K5"):
            with self.subTest(case):
                self.assertAccepted(_answer(ANSWERS[case]))

    def test_unknown_and_insufficient_evidence_are_always_allowed(self):
        self.assertEqual(_claims(_claim("unknown", "anything")).speech, UNKNOWN_SPEECH)
        self.assertEqual(check(V21, V21_SHA, document=_doc(insufficient=True)).speech, UNKNOWN_SPEECH)

    def test_speech_comes_from_templates_not_from_the_answer(self):
        verdict = _answer(ANSWERS["K1"])
        self.assertNotIn("Ausschluss", verdict.speech)
        self.assertEqual(verdict.speech, "In Zelle 3 liegt das Teil obs-0001, vom System zugeordnet "
                                         "(candidate_by_elimination), nicht beobachtet [obs-0001, link-0001].")

    def test_operator_label_is_spoken_as_a_label(self):
        self.assertIn("(laut Operator „eigentliches D-Pad“), laut Operator", _answer(ANSWERS["K2"]).speech)


class MappingTest(Assertions):
    def test_unmappable_sentence_is_rejected(self):
        self.assertRejectedFor(_answer("Das passt schon so [obs-0001]."), "unmapped:")

    def test_uncited_statement_is_rejected(self):
        self.assertRejectedFor(_answer("In Zelle 3 liegt ein Teil."), "uncited:")

    def test_question_alone_is_rejected_and_a_question_may_not_carry_ids(self):
        self.assertRejectedFor(_answer("Liegt die Membran unter dem D-Pad?"), "unmapped:")
        self.assertRejectedFor(_answer("Das wurde nicht beobachtet. Ist es Zelle 3?"), "unmapped:")

    def test_one_bad_sentence_rejects_the_whole_answer(self):
        verdict = _answer(ANSWERS["K1"] + " " + ANSWERS["C4-a"])
        self.assertRejectedFor(verdict, "order:")

    def test_words_outside_the_vocabulary_reject_the_sentence(self):
        # Development probes written while building the gate; they are not evidence of coverage.
        probes = ["In Zelle 3 liegt das türkise Teil [obs-0001, link-0001].",
                  "Das Teil aus Zelle 10 sitzt unter dem aus Zelle 7 [dec-0004, dec-0005].",
                  "Das Teil aus Zelle 6 ist hundertprozentig richtig zugeordnet [obs-0002, link-0002].",
                  "Auf dem neuen Bild ist die Stelle leer, also stammt das Teil aus Zelle 3 von dort [obs-0001].",
                  "Das Teil aus Zelle 3 gehört dorthin, wo das Teil aus Zelle 6 war [obs-0001, obs-0002]."]
        for probe in probes:
            with self.subTest(probe):
                self.assertRejectedFor(_answer(probe), "unmapped: words")

    def test_operator_label_words_are_vocabulary(self):
        self.assertAccepted(_answer("Laut Operator liegt die „L-Taste“ in Zelle 8 [dec-0013, store-0013]."))

    def test_mapper_reads_install_order_from_cell_mentions(self):
        package, _ = load_package(V21, V21_SHA)
        claims = map_answer(ANSWERS["K5"], package).claims
        self.assertIn({"relation": "install_before", "subject": "obs-0002", "object": "obs-0001",
                       "basis_word": "umgekehrte Entnahmereihenfolge"}, claims)


class EntityAndSchemaTest(Assertions):
    def test_unknown_ids_and_cells_are_rejected(self):
        self.assertRejectedFor(_claims(_claim("stored_in", "obs-0099", "cell-3")), "entity:")
        self.assertRejectedFor(_answer("In Zelle 9 liegt das Teil [obs-0001]."), "storage:")
        self.assertRejectedFor(_answer("In Zelle 3 liegt das Teil [obs-0099]."), "entity:")

    def test_schema_violations_are_rejected_without_speech(self):
        bad = [
            {"claims": [], "insufficient_evidence": False},
            {**_doc(), "schema": "anastylos-guide-claims@2"},
            {**_doc(), "prose": "Der grüne Knopf"},
            _doc({"relation": "looks_like", "subject": "obs-0001", "object": None, "basis_word": None}),
            _doc({"relation": "stored_in", "subject": "obs-0001", "object": "cell-3"}),
            _doc(*[_claim("unknown", "x")] * 13),
            {**_doc(), "insufficient_evidence": "no"},
            None,
            [],
        ]
        for document in bad:
            with self.subTest(document=document):
                self.assertRejectedFor(check(V21, V21_SHA, document=document), "schema:")


class PackageTest(Assertions):
    def test_hash_mismatch_rejects_before_any_claim_is_read(self):
        verdict = check(V21 + b" ", V21_SHA, document=_doc(_claim("unknown", "x")))
        self.assertFalse(verdict.accepted)
        self.assertTrue(verdict.findings[0].startswith("package: sha256"))
        self.assertFalse(check(V21, None, document=_doc()).accepted)

    def test_old_e2_package_is_an_unsupported_schema_not_a_crash(self):
        verdict = check(E2, hashlib.sha256(E2).hexdigest(), answer=ANSWERS["K1"])
        self.assertFalse(verdict.accepted)
        self.assertTrue(verdict.findings[0].startswith("unsupported package schema"), verdict.findings)

    def test_malformed_packages_are_rejected_without_crashing(self):
        for data in (b"\xff\xfe", b"not json", b"[]", b'{"schema": "anastylos-session-package@1"}',
                     b'{"schema": "anastylos-session-package@1", "parts": [1], "install_order": {"steps": []},'
                     b' "dependencies": {"confirmed": []}}'):
            with self.subTest(data=data):
                verdict = check(data, hashlib.sha256(data).hexdigest(), document=_doc())
                self.assertTrue(verdict.findings[0].startswith("unsupported package schema"), verdict.findings)


class DevRunTest(unittest.TestCase):
    def test_dev_set_scores_and_meets_acceptance(self):
        summary = score(CASES, V21)["summary"]
        self.assertEqual(summary, {"adversarial": 13, "adversarial_accepted": 0, "controls": 5, "controls_rejected": 0})
        self.assertTrue(meets_acceptance(summary))

    def test_fixture_is_the_package_the_dev_set_names(self):
        self.assertEqual(CASES["package"]["sha256"], V21_SHA)
        self.assertEqual(hashlib.sha256(V21).hexdigest(), V21_SHA)


if __name__ == "__main__":
    unittest.main()
