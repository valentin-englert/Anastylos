"""Gallery store checks: atomic writes, safe reads, versions. Needs the venv.

Run from the component-fingerprints directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from anastylos_fingerprints.describe import DESCRIPTOR_VERSION, fingerprint
from anastylos_fingerprints.match import check_version
from anastylos_fingerprints.store import load_gallery, save_record, validate_id
from anastylos_fingerprints.synthetic import render


def record(kind="ic", record_id="ic-01", **kwargs):
    return fingerprint(render(kind, seed=1), record_id=record_id, **kwargs)


class StoreTest(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_record(tmp, record())
            gallery = load_gallery(tmp)
            self.assertEqual(gallery["excluded"], [])
            (loaded,) = gallery["records"]
            self.assertEqual(loaded["id"], "ic-01")
            self.assertEqual(loaded["descriptor_version"], DESCRIPTOR_VERSION)

    def test_records_sorted_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_record(tmp, record(record_id="b-part"))
            save_record(tmp, record(record_id="a-part"))
            gallery = load_gallery(tmp)
            self.assertEqual([item["id"] for item in gallery["records"]],
                             ["a-part", "b-part"])

    def test_refuses_poor_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = fingerprint(np.full((200, 200, 3), 128, np.uint8), record_id="bad")
            with self.assertRaises(ValueError):
                save_record(tmp, bad)
            self.assertEqual(list(Path(tmp).glob("*.json")), [])

    def test_refuses_clobber_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_record(tmp, record())
            with self.assertRaises(ValueError):
                save_record(tmp, record())
            save_record(tmp, record(), overwrite=True)
            self.assertEqual(len(load_gallery(tmp)["records"]), 1)

    def test_rejects_escaping_ids(self):
        for bad in ("../evil", "a/b", "", "has space", "semi;colon"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_id(bad)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                save_record(tmp, record(record_id="../evil"))

    def test_corrupt_file_excluded_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_record(tmp, record())
            Path(tmp, "broken.json").write_text("{oops", encoding="utf-8")
            gallery = load_gallery(tmp)
            self.assertEqual(len(gallery["records"]), 1)
            (excluded,) = gallery["excluded"]
            self.assertEqual(excluded["id"], "broken")
            self.assertIn("unreadable", excluded["reason"])

    def test_id_mismatch_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = record()
            Path(tmp, "other.json").write_text(json.dumps(saved), encoding="utf-8")
            gallery = load_gallery(tmp)
            self.assertEqual(gallery["records"], [])
            self.assertIn("does not match", gallery["excluded"][0]["reason"])

    def test_version_mismatch_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = record()
            stale["descriptor_version"] = DESCRIPTOR_VERSION + 99
            Path(tmp, "ic-01.json").write_text(json.dumps(stale), encoding="utf-8")
            gallery = load_gallery(tmp)
            self.assertEqual(gallery["records"], [])
            self.assertIn("descriptor version", gallery["excluded"][0]["reason"])

    def test_check_version_current_ok(self):
        self.assertIsNone(check_version(record()))
        self.assertIn("version", check_version({"descriptor_version": 0}))

    def test_missing_gallery_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                load_gallery(str(Path(tmp) / "nope"))

    def test_no_temp_files_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_record(tmp, record())
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
