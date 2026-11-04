"""Reader invocation through the Core against the REAL reader code."""

from __future__ import annotations

import unittest

from _support import (CLOCK, FailingOCR, MissingTesseract, StubOCR, np, resistor_image, text_panel,
                      tmpdir, write_png)

from anastylos_microscopy import schema
from anastylos_microscopy.adapters import REGISTRY
from anastylos_microscopy.orchestrator import inspect_image, select_readers


class SingleReaderTest(unittest.TestCase):
    def test_resistor_native_result_equals_direct_reader_call(self):
        from anastylos_resistor.vision import analyze

        base = tmpdir(self)
        image = resistor_image()
        src = write_png(base / "res.png", image)
        record, _ = inspect_image(src, base / "out", mode="resistor", clock=CLOCK)
        block = record["recognizers"][0]
        native = {k: v for k, v in block["native_result"].items() if k not in ("image", "roi")}
        self.assertEqual(native, analyze(image, None), "Core must not alter reader output")
        self.assertEqual(block["status"], "ok")
        self.assertTrue(block["matches_pinned_revision"])
        self.assertEqual(record["interpretations"][0]["resistance_ohms"], 1000.0)
        self.assertFalse(record["candidates"][0]["confirmed"])
        self.assertEqual(record["uncertainty"]["overall_status"], "candidate")
        self.assertEqual(schema.validate_named(record, "inspection"), [])

    def test_smd_end_to_end_with_stub_engine(self):
        base = tmpdir(self)
        src = write_png(base / "smd.png", text_panel("472"))
        record, _ = inspect_image(src, base / "out", mode="smd", clock=CLOCK,
                                  ocr_backend=StubOCR(["472"]))
        block = record["recognizers"][0]
        self.assertEqual(block["status"], "ok")
        self.assertEqual(block["ocr_backend"]["name"], "stub-ocr")
        self.assertEqual(record["observed_evidence"][0]["raw_text"], "472")
        self.assertEqual(record["interpretations"][0]["resistance_ohms_exact"], "4700")
        self.assertIsNone(record["interpretations"][0]["tolerance_percent"])
        self.assertIn("conditional", record["candidates"][0]["identification"])

    def test_pcb_ocr_and_markings_paths(self):
        base = tmpdir(self)
        src = write_png(base / "ic.png", text_panel("LM358N", size=(480, 140)))
        record, _ = inspect_image(src, base / "out", mode="multi", readers=["pcb_ocr", "markings"],
                                  clock=CLOCK, ocr_backend=StubOCR(["LM358N", "R12"]))
        statuses = {b["reader"]: b["status"] for b in record["recognizers"]}
        self.assertIn(statuses["markings"], ("ok", "ambiguous"))
        designators = [i["normalized"] for i in record["interpretations"] if i["reader"] == "pcb_ocr"]
        self.assertIn("R12", designators)
        for cand in (c for c in record["candidates"] if c["reader"] == "markings"):
            self.assertIsNone(cand["verified_spec"])
            self.assertFalse(cand["resolved"])

    def test_missing_ocr_engine_reports_unavailable(self):
        base = tmpdir(self)
        src = write_png(base / "smd.png", text_panel("472"))
        record, _ = inspect_image(src, base / "out", mode="multi",
                                  readers=["smd", "pcb_ocr", "markings"], clock=CLOCK,
                                  ocr_backend=MissingTesseract())
        self.assertEqual({b["status"] for b in record["recognizers"]}, {"unavailable"})
        self.assertEqual(record["uncertainty"]["overall_status"], "unavailable")
        self.assertEqual(record["candidates"], [])
        self.assertEqual(len(record["uncertainty"]["unavailable_readers"]), 3)


class MultiReaderTest(unittest.TestCase):
    def test_all_ten_readers_run_without_crashing(self):
        base = tmpdir(self)
        src = write_png(base / "res.png", resistor_image())
        record, _ = inspect_image(src, base / "out", mode="multi", readers=list(REGISTRY),
                                  clock=CLOCK, ocr_backend=MissingTesseract())
        statuses = {b["reader"]: b["status"] for b in record["recognizers"]}
        self.assertEqual(set(statuses), set(REGISTRY))
        self.assertEqual(statuses["screw_measure"], "input_error")  # no --view
        self.assertEqual(statuses["washer"], "input_error")
        self.assertEqual(statuses["fingerprints"], "unavailable")  # no gallery
        self.assertEqual(statuses["resistor"], "ok")
        self.assertEqual(schema.validate_named(record, "inspection"), [])

    def test_reader_failure_is_isolated(self):
        base = tmpdir(self)
        src = write_png(base / "res.png", resistor_image())
        record, _ = inspect_image(src, base / "out", mode="multi", readers=["pcb_ocr", "resistor"],
                                  clock=CLOCK, ocr_backend=FailingOCR())
        statuses = {b["reader"]: b for b in record["recognizers"]}
        self.assertEqual(statuses["pcb_ocr"]["status"], "reader_error")
        self.assertIn("simulated OCR engine crash", statuses["pcb_ocr"]["reason"])
        self.assertEqual(statuses["resistor"]["status"], "ok")

    def test_conflicting_interpretations(self):
        base = tmpdir(self)
        src = write_png(base / "res.png", resistor_image())  # bands read as 1 kOhm
        record, _ = inspect_image(src, base / "out", mode="multi", readers=["resistor", "smd"],
                                  clock=CLOCK, ocr_backend=StubOCR(["472"]))  # 4.7 kOhm
        self.assertEqual(record["uncertainty"]["overall_status"], "conflict")
        domains = [c["domain"] for c in record["reconciliation"]["conflicts"]]
        self.assertIn("resistance_ohms", domains)

    def test_agreeing_readers_stay_candidates(self):
        base = tmpdir(self)
        src = write_png(base / "res.png", resistor_image())
        record, _ = inspect_image(src, base / "out", mode="multi", readers=["resistor", "smd"],
                                  clock=CLOCK, ocr_backend=StubOCR(["102"]))
        self.assertEqual(record["uncertainty"]["overall_status"], "candidate")
        self.assertEqual(record["reconciliation"]["agreements"][0]["domain"], "component_category")
        self.assertIsNone(record["human_review"]["confirmed_identification"])

    def test_unknown_component_abstains(self):
        base = tmpdir(self)
        src = write_png(base / "blank.png", np.full((200, 300, 3), 128, np.uint8))
        record, _ = inspect_image(src, base / "out", mode="auto", clock=CLOCK,
                                  ocr_backend=StubOCR([]))
        self.assertEqual(record["selection"]["readers"], ["pcb_ocr", "markings"])
        self.assertEqual(record["uncertainty"]["overall_status"], "unknown")
        self.assertEqual(record["candidates"], [])
        self.assertIn("uniform_image", [f["flag"] for f in record["quality"]["flags"]])

    def test_quality_flag_holds_reader_ok_as_ambiguous(self):
        base = tmpdir(self)
        src = write_png(base / "blank.png", np.full((140, 360, 3), 30, np.uint8))
        record, _ = inspect_image(src, base / "out", mode="smd", clock=CLOCK,
                                  ocr_backend=StubOCR(["472"]))  # engine "reads" a blank crop
        self.assertEqual(record["recognizers"][0]["native_status"], "ok")
        self.assertEqual(record["uncertainty"]["overall_status"], "ambiguous")


class SelectionTest(unittest.TestCase):
    def test_auto_selection_rules(self):
        self.assertEqual(select_readers("auto", None, "smd_resistor", {})["readers"], ["smd"])
        self.assertEqual(select_readers("auto", None, None, {})["readers"], ["pcb_ocr", "markings"])
        screw = select_readers("auto", None, "screw", {})
        self.assertEqual(screw["readers"], ["screw"])
        self.assertEqual(screw["skipped"][0]["reader"], "screw_measure")
        self.assertEqual(select_readers("auto", None, "screw", {"view": "top"})["readers"],
                         ["screw", "screw_measure"])
        self.assertIn("fingerprints", select_readers("auto", None, "ic", {"gallery": "g"})["readers"])
        self.assertEqual(select_readers("auto", None, "ic", {})["derived_options"],
                         {"polarity_family": "ic"})
        with self.assertRaises(ValueError):
            select_readers("multi", ["tray_counter"], None, {})


if __name__ == "__main__":
    unittest.main()
