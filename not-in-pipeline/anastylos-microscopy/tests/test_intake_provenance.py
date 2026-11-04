"""Input validation, provenance, originals and Windows path handling."""

from __future__ import annotations

import json
import struct
import unittest

from _support import CLOCK, cv2, np, resistor_image, tmpdir, write_png

from anastylos_microscopy import capture, evidence
from anastylos_microscopy.orchestrator import inspect_image
from anastylos_microscopy.util import sha256_file


def _jpeg_with_exif_orientation(image, orientation: int) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    data = buffer.tobytes()
    tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 1) + \
        struct.pack("<HHII", 0x0112, 3, 1, orientation) + struct.pack("<I", 0)
    payload = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return data[:2] + app1 + data[2:]


class IntakeTest(unittest.TestCase):
    def test_provenance_and_original_preserved(self):
        base = tmpdir(self)
        src = write_png(base / "in" / "res.png", resistor_image())
        before = sha256_file(src)
        record, path = inspect_image(src, base / "out", mode="resistor", clock=CLOCK)
        self.assertEqual(record["image"]["sha256"], before)
        self.assertEqual(sha256_file(src), before, "source must never be modified")
        original = [a for a in record["artifacts"] if a["role"] == "original_copy"][0]
        copy = path.parent / original["path"]
        self.assertEqual(copy.read_bytes(), src.read_bytes())
        for artifact in record["artifacts"]:
            self.assertEqual(sha256_file(path.parent / artifact["path"]), artifact["sha256"])
        self.assertEqual(record["image"]["resolution"], {"width": 900, "height": 320})
        self.assertFalse(record["orientation"]["stored_pixels_modified"])

    def test_truncated_jpeg_is_rejected_but_preserved(self):
        base = tmpdir(self)
        ok, buffer = cv2.imencode(".jpg", resistor_image())
        src = base / "cut.jpg"
        src.write_bytes(buffer.tobytes()[:-500])
        record, path = inspect_image(src, base / "out", mode="resistor", clock=CLOCK)
        self.assertEqual(record["uncertainty"]["overall_status"], "input_error")
        self.assertEqual(record["input_error"]["code"], "integrity_failed")
        self.assertEqual(record["recognizers"], [])
        self.assertTrue(any(a["role"] == "original_copy" for a in record["artifacts"]))

    def test_unsupported_and_missing_files(self):
        base = tmpdir(self)
        text = base / "notes.jpg"
        text.write_text("not an image", encoding="utf-8")
        record, _ = inspect_image(text, base / "out", mode="resistor", clock=CLOCK)
        self.assertEqual(record["input_error"]["code"], "unsupported_format")
        record, _ = inspect_image(base / "absent.png", base / "out", mode="resistor", clock=CLOCK)
        self.assertEqual(record["input_error"]["code"], "file_not_found")
        self.assertNotIn("sha256", record["image"])

    def test_roi_validation_and_crop_artifact(self):
        base = tmpdir(self)
        src = write_png(base / "res.png", resistor_image())
        bad, _ = inspect_image(src, base / "out", mode="resistor", roi=[800, 0, 200, 50], clock=CLOCK)
        self.assertEqual(bad["input_error"]["code"], "roi_out_of_bounds")
        good, path = inspect_image(src, base / "out", mode="resistor", roi=[100, 60, 700, 200],
                                   clock=CLOCK)
        self.assertEqual(good["roi"]["xywh"], [100, 60, 700, 200])
        crop = cv2.imread(str(path.parent / "evidence" / "roi_crop.png"))
        self.assertEqual(crop.shape[:2], (200, 700))
        self.assertEqual(good["recognizers"][0]["native_result"]["roi"], [100, 60, 700, 200])

    def test_exif_orientation_recorded_not_applied(self):
        base = tmpdir(self)
        image = resistor_image()
        src = base / "exif.jpg"
        src.write_bytes(_jpeg_with_exif_orientation(image, 6))
        record, _ = inspect_image(src, base / "out", mode="resistor", clock=CLOCK)
        self.assertEqual(record["image"]["exif_orientation"], 6)
        self.assertFalse(record["orientation"]["applied_by_core"])
        # Readers saw the stored 900x320 grid, not a rotated 320x900 one.
        self.assertEqual(record["image"]["resolution"], {"width": 900, "height": 320})

    def test_windows_style_paths(self):
        base = tmpdir(self)
        src = write_png(base / "Platine Ä 01 (MSI).png", resistor_image())
        record, _ = inspect_image(src, base / "out", mode="resistor", clock=CLOCK)
        self.assertEqual(record["recognizers"][0]["status"], "ok")
        self.assertEqual(record["image"]["file_name"], "Platine Ä 01 (MSI).png")
        self.assertEqual(evidence.display_name(r"C:\Users\Valentin\Mikroskop\frame 1.jpg"),
                         "frame 1.jpg")
        self.assertEqual(str(capture.safe_relative(r"frames\frame_000001.jpg")),
                         "frames/frame_000001.jpg")
        for bad in (r"C:\frames\a.jpg", r"..\a.jpg", "/abs/a.jpg", "frames/../../a.jpg",
                    r"\\server\share\a.jpg"):
            with self.assertRaises(capture.ManifestError, msg=bad):
                capture.safe_relative(bad)


if __name__ == "__main__":
    unittest.main()
