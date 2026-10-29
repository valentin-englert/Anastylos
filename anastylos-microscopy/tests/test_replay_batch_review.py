"""Deterministic replay, schema validation, batch, capture manifest, review."""

from __future__ import annotations

import json
import unittest

from _support import CLOCK, StubOCR, cv2, resistor_image, text_panel, tmpdir, write_png

from anastylos_microscopy import cli, schema
from anastylos_microscopy.orchestrator import inspect_image
from anastylos_microscopy.review import write_review
from anastylos_microscopy.runner import inspect_directory, inspect_manifest, replay
from anastylos_microscopy.util import read_json, sha256_file


def _strip_run(path):
    data = read_json(path)
    data.pop("run")
    return data


class ReplayTest(unittest.TestCase):
    def test_replay_reproduces_digest_including_gallery_match(self):
        from anastylos_fingerprints.synthetic import render

        base = tmpdir(self)
        gallery = base / "gallery"
        enroll = write_png(base / "enroll.png", render("ic", seed=1))
        inspect_image(enroll, base / "enroll_out", mode="fingerprints", clock=CLOCK,
                      options={"gallery": str(gallery), "fingerprint_action": "enroll",
                               "enroll_id": "ic-01"})
        query = write_png(base / "query.png", render("ic", rotation_deg=30, seed=2))
        record, path = inspect_image(query, base / "a", mode="fingerprints", clock=CLOCK,
                                     options={"gallery": str(gallery)})
        self.assertEqual(record["recognizers"][0]["status"], "ok")
        result = replay(path, base / "b")
        self.assertTrue(result["identical"], result)
        self.assertEqual(_strip_run(path), _strip_run(result["replay_path"]))

    def test_replay_detects_changed_input(self):
        base = tmpdir(self)
        src = write_png(base / "res.png", resistor_image())
        _, path = inspect_image(src, base / "a", mode="resistor", clock=CLOCK)
        write_png(src, resistor_image(("yellow", "violet", "red", "gold")))
        result = replay(path, base / "b")
        self.assertFalse(result["identical"])
        self.assertIn("image", result["differing_sections"])

    def test_cli_validate_detects_tampering(self):
        base = tmpdir(self)
        src = write_png(base / "res.png", resistor_image())
        record, path = inspect_image(src, base / "a", mode="resistor", clock=CLOCK)
        self.assertEqual(cli.main(["validate", "--kind", "inspection", str(path)]), 0)
        record["candidates"][0]["value"] = "47 kOhm"
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(cli.main(["validate", "--kind", "inspection", str(path)]), 1)


class BatchManifestTest(unittest.TestCase):
    def test_batch_continues_past_bad_files(self):
        base = tmpdir(self)
        folder = base / "imgs"
        write_png(folder / "a_good.png", resistor_image())
        (folder / "b_broken.png").write_bytes(b"\x89PNG\r\n\x1a\nbroken")
        write_png(folder / "c_smd.png", text_panel("472"))
        batch, path = inspect_directory(folder, base / "out", mode="auto", hypothesis="smd_resistor",
                                        clock=CLOCK, ocr_backend=StubOCR(["472"]))
        self.assertEqual(batch["count"], 3)
        statuses = [e["overall_status"] for e in batch["entries"]]
        self.assertEqual(statuses[1], "input_error")
        self.assertEqual(statuses[2], "candidate")
        for entry in batch["entries"]:
            record = read_json(path.parent / entry["inspection_json"])
            self.assertEqual(record["source"]["origin"], "batch")
            self.assertEqual(schema.validate_named(record, "inspection"), [])

    def test_capture_manifest_intake(self):
        base = tmpdir(self)
        frames = base / "node" / "frames"
        frames.mkdir(parents=True)
        ok, buffer = cv2.imencode(".jpg", resistor_image())
        (frames / "f1.jpg").write_bytes(buffer.tobytes())
        (frames / "f2.jpg").write_bytes(buffer.tobytes())
        good = sha256_file(frames / "f1.jpg")
        manifest = {
            "schema": "anastylos-microscopy-capture-manifest/1", "node_id": "test-node",
            "session_id": "s-1", "validation_class": "synthetic",
            "source": {"camera_id": "mustool-test", "optical_profile": "test-profile"},
            "calibration": {"schema": "anastylos-microscopy-calibration/1", "pixels_per_mm": 50.0,
                            "image_resolution": [900, 320], "provenance": "TEST FIXTURE"},
            "frames": [
                {"file": "frames\\f1.jpg", "sha256": good, "seq": 1,
                 "captured_utc": "2026-10-29T12:00:01.000Z", "orientation_cw_deg": 90},
                {"file": "frames/f2.jpg", "sha256": "0" * 64, "seq": 2},
                {"file": "../outside.jpg", "sha256": good, "seq": 3},
            ]}
        mpath = base / "node" / "capture-manifest.json"
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(schema.validate_named(manifest, "capture_manifest"), [])
        batch, path = inspect_manifest(mpath, base / "out", mode="resistor", clock=CLOCK)
        first, second, third = batch["entries"]
        self.assertEqual(first["overall_status"], "candidate")
        record = read_json(path.parent / first["inspection_json"])
        self.assertEqual(record["source"]["camera_id"], "mustool-test")
        self.assertEqual(record["source"]["capture_utc"], "2026-10-29T12:00:01.000Z")
        self.assertEqual(record["orientation"]["declared_display_rotation_cw_deg"], 90)
        self.assertEqual(record["calibration"]["status"], "valid")
        self.assertEqual(record["session_id"], "s-1")
        self.assertEqual(second["overall_status"], "input_error")
        self.assertIn("traverse", third["error"])


class ReviewTest(unittest.TestCase):
    def test_review_is_separate_and_requires_identification(self):
        base = tmpdir(self)
        src = write_png(base / "res.png", resistor_image())
        _, path = inspect_image(src, base / "a", mode="resistor", clock=CLOCK)
        before = sha256_file(path)
        with self.assertRaises(ValueError):
            write_review(path, operator="Valentin", decision="confirmed")
        review = write_review(path, operator="Valentin", decision="confirmed",
                              identification="1 kOhm axial resistor (test fixture)",
                              evidence=["multimeter reading photo (hypothetical)"], clock=CLOCK)
        self.assertEqual(sha256_file(path), before, "inspection must not be edited by a review")
        doc = read_json(review)
        self.assertEqual(doc["inspection_sha256"], before)
        self.assertEqual(schema.validate_named(doc, "review"), [])


if __name__ == "__main__":
    unittest.main()
