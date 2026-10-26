"""Compliance checker self-tests. Standard library only.

Run from the tools/compliance directory:

    py -3.12 -m unittest test_check -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from check import (check_files, check_requirements, collect_reviews, load_inventory,
                   parse_requirements, pypi_purls)


def document(packages):
    return {"spdxVersion": "SPDX-2.3", "packages": packages}


def package(name, version, license="MIT"):
    return {"SPDXID": f"SPDXRef-Package-{name}", "name": name, "versionInfo": version,
            "licenseConcluded": license,
            "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl",
                              "referenceLocator": f"pkg:pypi/{name}@{version}"}]}


class CheckTest(unittest.TestCase):
    def test_clean_fixture_has_no_errors(self):
        pins = {"numpy": "2.5.4", "cffi": "2.1.1"}
        purls = pypi_purls(document([package("numpy", "2.5.4"), package("cffi", "2.1.1")]))
        self.assertEqual(check_requirements(pins, purls), [])

    def test_detects_undocumented_stale_and_drift(self):
        pins = {"numpy": "2.5.4", "newdep": "1.0"}
        purls = pypi_purls(document([package("numpy", "2.0.0"), package("olddep", "1.0")]))
        errors = check_requirements(pins, purls)
        self.assertEqual(len(errors), 3)
        joined = "\n".join(errors)
        self.assertIn("undocumented dependency: newdep", joined)
        self.assertIn("stale inventory entry: olddep", joined)
        self.assertIn("version drift: numpy", joined)

    def test_parses_pins_and_rejects_loose_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.txt"
            path.write_text("# comment\nCairoSVG==2.9.1\n\nnumpy==2.5.4  # trailing\n", encoding="utf-8")
            self.assertEqual(parse_requirements(path), {"cairosvg": "2.9.1", "numpy": "2.5.4"})
            path.write_text("numpy>=2.0\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_requirements(path)

    def test_rejects_broken_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spdx.json"
            path.write_text(json.dumps({"spdxVersion": "SPDX-2.3", "packages": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_inventory(path)
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_inventory(path)

    def test_missing_files_and_review_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            present = folder / "kept.md"
            present.write_text("x", encoding="utf-8")
            self.assertEqual(check_files([present]), [])
            missing = check_files([present, folder / "gone.md"])
            self.assertEqual(len(missing), 1)
            self.assertIn("gone.md", missing[0])
        reviewed = package("pillow", "13.0.0")
        reviewed["annotations"] = [{"comment": "REVIEW: confirm mapping"}]
        reviews = collect_reviews(document([reviewed, package("cffi", "2.1.1")]))
        self.assertEqual(len(reviews), 1)
        self.assertIn("pillow", reviews[0])


if __name__ == "__main__":
    unittest.main()
