"""Eval, CLI, and annotation checks on synthetic fixtures. Needs the venv.

Proves harness wiring and reproducibility; says nothing about
real-photo matching.

Run from the connector-matching directory:

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

from anastylos_connectors.annotate import annotate
from anastylos_connectors.cli import main
from anastylos_connectors.eval import run_eval
from anastylos_connectors.match import match_gallery
from anastylos_connectors.observe import observe
from anastylos_connectors.store import load_gallery, save_record
from anastylos_connectors.synthetic import render


def build_gallery(folder: Path) -> Path:
    gallery = folder / "gallery"
    save_record(gallery, observe(render("socket", count=4, keying="right", seed=1),
                                 record_id="sock4R", source={"image": "sock4R.png"}))
    save_record(gallery, observe(render("socket", count=4, keying="left", seed=1),
                                 record_id="sock4L", source={"image": "sock4L.png"}))
    return gallery


class EvalTest(unittest.TestCase):
    def _labels(self, folder: Path):
        images = folder / "images"
        images.mkdir()
        cv2.imwrite(str(images / "plug_q.png"),
                    render("plug", count=4, keying="left", rotation_deg=20, seed=40))
        cv2.imwrite(str(images / "side_q.png"), render("side", seed=41))
        labels = folder / "labels.json"
        labels.write_text(json.dumps({"images": [
            {"file": "plug_q.png", "expect_id": "sock4R"},
            {"file": "side_q.png", "expect_id": None}]}), encoding="utf-8")
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
            self.assertTrue((folder / "out" / "plug_q_annotated.png").exists())

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
                                                      "expect_id": "sock4R"}]}),
                              encoding="utf-8")
            with self.assertRaises(ValueError):
                run_eval(labels, folder, build_gallery(folder), folder / "out")

    def test_eval_missing_gallery_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            labels, images = self._labels(folder)
            report = run_eval(labels, images, folder / "nope", folder / "out")
            self.assertEqual(report["status"], "unavailable")


class CliTest(unittest.TestCase):
    def test_observe_and_match_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cv2.imwrite(str(folder / "part.png"),
                        render("socket", count=4, keying="right", seed=1))
            cv2.imwrite(str(folder / "query.png"),
                        render("plug", count=4, keying="left", rotation_deg=25, seed=2))
            gallery = folder / "gallery"
            with contextlib.redirect_stdout(io.StringIO()) as seen:
                code = main(["observe", "--image", str(folder / "part.png"),
                             "--id", "sock-01", "--gallery", str(gallery),
                             "--marking", "X1", "--out", str(folder / "obs_out")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(seen.getvalue())["status"], "observed")
            self.assertTrue((gallery / "sock-01.json").exists())
            with contextlib.redirect_stdout(io.StringIO()) as matched:
                code = main(["match", "--image", str(folder / "query.png"),
                             "--gallery", str(gallery), "--marking", "X1",
                             "--out", str(folder / "match_out")])
            self.assertEqual(code, 0)
            result = json.loads(matched.getvalue())
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["candidates"][0]["id"], "sock-01")
            self.assertTrue((folder / "match_out" / "annotated.png").exists())

    def test_observe_without_gallery(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cv2.imwrite(str(folder / "part.png"), render("plug", count=4, seed=1))
            with contextlib.redirect_stdout(io.StringIO()) as seen:
                code = main(["observe", "--image", str(folder / "part.png"),
                             "--id", "plug-01", "--out", str(folder / "out")])
            self.assertEqual(code, 0)
            result = json.loads(seen.getvalue())
            self.assertIsNone(result["gallery"])
            self.assertEqual(result["observation"]["role"], "plug")

    def test_observe_rejects_poor_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cv2.imwrite(str(folder / "flat.png"), render("blank", seed=1))
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["observe", "--image", str(folder / "flat.png"),
                             "--id", "flat", "--gallery", str(folder / "gallery"),
                             "--out", str(folder / "out")])
            self.assertEqual(code, 2)

    def test_match_missing_gallery(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cv2.imwrite(str(folder / "query.png"), render("plug", seed=1))
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
            cv2.imwrite(str(folder / "query.png"), render("plug", seed=1))
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
            cv2.imwrite(str(images / "plug_q.png"),
                        render("plug", count=4, keying="left", seed=2))
            labels = folder / "labels.json"
            labels.write_text(json.dumps({"images": [{"file": "plug_q.png",
                                                      "expect_id": "sock4R"}]}),
                              encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["eval", "--labels", str(labels),
                             "--images-dir", str(images),
                             "--gallery", str(build_gallery(folder)),
                             "--out", str(folder / "out")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buffer.getvalue())["exact"], 1)


class AnnotateTest(unittest.TestCase):
    def test_annotate_draws_contacts_and_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            gallery = load_gallery(build_gallery(Path(tmp)))
        image = render("plug", count=4, keying="left", rotation_deg=20, seed=40)
        query = observe(image)
        result = match_gallery(query, gallery["records"])
        marked = annotate(image, result, None, query)
        self.assertEqual(marked.shape, image.shape)
        self.assertTrue((marked != image).any())

    def test_annotate_handles_unknown(self):
        image = render("side", seed=1)
        query = observe(image)
        result = match_gallery(query, [])
        marked = annotate(image, result, None, query)
        self.assertEqual(marked.shape, image.shape)


if __name__ == "__main__":
    unittest.main()
