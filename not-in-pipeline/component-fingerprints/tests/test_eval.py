"""Eval, CLI, and annotation checks on synthetic fixtures. Needs the venv.

Proves harness wiring and reproducibility; says nothing about
real-photo retrieval.

Run from the component-fingerprints directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2

from anastylos_fingerprints.annotate import annotate
from anastylos_fingerprints.cli import main
from anastylos_fingerprints.describe import fingerprint
from anastylos_fingerprints.eval import run_eval
from anastylos_fingerprints.match import match_gallery
from anastylos_fingerprints.store import load_gallery, save_record
from anastylos_fingerprints.synthetic import render


def build_gallery(folder: Path) -> Path:
    gallery = folder / "gallery"
    for kind in ("ic", "resistor", "screw"):
        save_record(gallery, fingerprint(render(kind, seed=1), record_id=kind,
                                         source={"image": f"{kind}.png"}))
    return gallery


class EvalTest(unittest.TestCase):
    def _labels(self, folder: Path):
        images = folder / "images"
        images.mkdir()
        cv2.imwrite(str(images / "ic_q.png"), render("ic", rotation_deg=20, seed=40))
        cv2.imwrite(str(images / "new_q.png"), render("capacitor", seed=41))
        labels = folder / "labels.json"
        labels.write_text(json.dumps({"images": [
            {"file": "ic_q.png", "expect_id": "ic"},
            {"file": "new_q.png", "expect_id": None}]}), encoding="utf-8")
        return labels, images

    def test_eval_exact_and_novel(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            labels, images = self._labels(folder)
            report = run_eval(labels, images, build_gallery(folder), folder / "out")
            self.assertEqual(report["images"], 2)
            self.assertEqual(report["exact"], 2)
            self.assertEqual(report["false_confident"], 0)
            self.assertEqual(report["retrieval"], {"expected": 1, "top1": 1,
                                                   "topk": 1, "k": 5})
            self.assertTrue((folder / "out" / "eval_report.json").exists())
            self.assertTrue((folder / "out" / "ic_q_annotated.png").exists())

    def test_eval_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            labels, images = self._labels(folder)
            gallery = build_gallery(folder)
            first = run_eval(labels, images, gallery, folder / "out_a")
            second = run_eval(labels, images, gallery, folder / "out_b")
            self.assertEqual(first, second)

    def test_eval_missing_image_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            labels = folder / "labels.json"
            labels.write_text(json.dumps({"images": [{"file": "gone.png",
                                                      "expect_id": "ic"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                run_eval(labels, folder, build_gallery(folder), folder / "out")


class CliTest(unittest.TestCase):
    def test_enroll_and_match_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cv2.imwrite(str(folder / "part.png"), render("ic", seed=1))
            cv2.imwrite(str(folder / "query.png"), render("ic", rotation_deg=25, seed=2))
            gallery = folder / "gallery"
            with contextlib.redirect_stdout(io.StringIO()) as enrolled:
                code = main(["enroll", "--image", str(folder / "part.png"),
                             "--id", "ic-01", "--gallery", str(gallery),
                             "--marking", "358", "--out", str(folder / "enroll_out")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(enrolled.getvalue())["status"], "enrolled")
            self.assertTrue((gallery / "ic-01.json").exists())
            with contextlib.redirect_stdout(io.StringIO()) as matched:
                code = main(["match", "--image", str(folder / "query.png"),
                             "--gallery", str(gallery), "--marking", "358",
                             "--out", str(folder / "match_out")])
            self.assertEqual(code, 0)
            result = json.loads(matched.getvalue())
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["candidates"][0]["id"], "ic-01")
            self.assertTrue((folder / "match_out" / "annotated.png").exists())

    def test_enroll_rejects_poor_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cv2.imwrite(str(folder / "flat.png"), render("blank", seed=1))
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["enroll", "--image", str(folder / "flat.png"),
                             "--id", "flat", "--gallery", str(folder / "gallery"),
                             "--out", str(folder / "out")])
            self.assertEqual(code, 2)

    def test_enroll_rejects_duplicate_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cv2.imwrite(str(folder / "part.png"), render("ic", seed=1))
            args = ["enroll", "--image", str(folder / "part.png"), "--id", "ic-01",
                    "--gallery", str(folder / "gallery"), "--out", str(folder / "out")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 2)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([*args, "--force"]), 0)

    def test_match_missing_gallery(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cv2.imwrite(str(folder / "query.png"), render("ic", seed=1))
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["match", "--image", str(folder / "query.png"),
                             "--gallery", str(folder / "nope"),
                             "--out", str(folder / "out")])
            self.assertEqual(code, 1)

    def test_match_missing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["match", "--image", str(Path(tmp) / "nope.png"),
                             "--gallery", str(Path(tmp) / "gallery"),
                             "--out", str(Path(tmp) / "out")])
            self.assertEqual(code, 1)

    def test_match_bad_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cv2.imwrite(str(folder / "query.png"), render("ic", seed=1))
            weights = folder / "weights.json"
            weights.write_text(json.dumps({"telepathy": 1.0}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["match", "--image", str(folder / "query.png"),
                             "--gallery", str(folder / "gallery"),
                             "--weights", str(weights), "--out", str(folder / "out")])
            self.assertEqual(code, 2)

    def test_cli_eval_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            images = folder / "images"
            images.mkdir()
            cv2.imwrite(str(images / "ic_q.png"), render("ic", seed=2))
            labels = folder / "labels.json"
            labels.write_text(json.dumps({"images": [{"file": "ic_q.png",
                                                      "expect_id": "ic"}]}), encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["eval", "--labels", str(labels),
                             "--images-dir", str(images),
                             "--gallery", str(build_gallery(folder)),
                             "--out", str(folder / "out")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buffer.getvalue())["exact"], 1)


class AnnotateTest(unittest.TestCase):
    def test_annotate_draws_silhouette_and_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            gallery = load_gallery(build_gallery(Path(tmp)))
        image = render("ic", rotation_deg=20, seed=40)
        result = match_gallery(fingerprint(image), gallery["records"])
        marked = annotate(image, result)
        self.assertEqual(marked.shape, image.shape)
        self.assertTrue((marked != image).any())

    def test_annotate_handles_rejection(self):
        image = render("blank", seed=1)
        result = match_gallery(fingerprint(image), [])
        marked = annotate(image, result)
        self.assertEqual(marked.shape, image.shape)


if __name__ == "__main__":
    unittest.main()
