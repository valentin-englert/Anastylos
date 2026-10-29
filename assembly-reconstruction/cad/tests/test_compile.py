"""Compiler tests: determinism, regen diffs, no-foreign-code proof.

Run from the assembly-reconstruction/cad directory:

    .venv/Scripts/python -m unittest discover -s tests -v

This file is standard library only.
"""

import ast
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anastylos_cad.compile import (  # noqa: E402
    BUILDER_ENTRY, canonical_params, generate_model_py,
)

FIX = ROOT / "fixtures" / "synthetic"

# The only calls the fixed template may contain: parse the embedded JSON
# literal, call the allowlisted builder entry, run main.
ALLOWED_CALLS = frozenset(["json.loads", BUILDER_ENTRY, "main"])
ALLOWED_IMPORTS = frozenset(["json", "sys"])
ALLOWED_FROM_IMPORTS = frozenset([("anastylos_cad", "builder")])


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def load(name):
    with open(FIX / name, encoding="utf-8") as handle:
        return json.load(handle)


class Determinism(unittest.TestCase):
    def test_same_input_byte_identical(self):
        model = load("plate_hole.json")
        self.assertEqual(generate_model_py(model), generate_model_py(model))

    def test_feature_order_irrelevant(self):
        model = load("plate_hole.json")
        flipped = copy.deepcopy(model)
        flipped["features"] = list(reversed(flipped["features"]))
        self.assertEqual(generate_model_py(model), generate_model_py(flipped))

    def test_model_identity_stamped(self):
        model = load("plate_hole.json")
        source = generate_model_py(model)
        self.assertIn(model["model_id"], source)
        self.assertIn("revision 1", source)


class RegenDiff(unittest.TestCase):
    def test_plus_1mm_changes_only_that_dimension(self):
        model = load("plate_hole.json")
        grown = copy.deepcopy(model)
        grown["features"][0]["parameters"]["size_mm"][0] += 1
        before = generate_model_py(model)
        after = generate_model_py(grown)
        self.assertNotEqual(before, after)
        self.assertIn("[81,50,6]", after.replace(" ", ""))
        self.assertIn("[80,50,6]", before.replace(" ", ""))
        # Every other literal survives: depth, radius, adapter, hole offset.
        for literal in ("6", "3", "40", "25"):
            self.assertIn(literal, after)


class NoForeignCode(unittest.TestCase):
    def test_generated_calls_are_allowlisted_only(self):
        tree = ast.parse(generate_model_py(load("plate_hole.json")))
        calls = {_dotted(node.func) for node in ast.walk(tree)
                 if isinstance(node, ast.Call)}
        self.assertEqual(calls, ALLOWED_CALLS)

    def test_generated_imports_are_allowlisted_only(self):
        tree = ast.parse(generate_model_py(load("plate_hole.json")))
        plain = {a.name for node in ast.walk(tree)
                 if isinstance(node, ast.Import) for a in node.names}
        fromed = {(node.module, a.name) for node in ast.walk(tree)
                  if isinstance(node, ast.ImportFrom) for a in node.names}
        self.assertEqual(plain, ALLOWED_IMPORTS)
        self.assertEqual(fromed, ALLOWED_FROM_IMPORTS)

    def test_generated_source_compiles(self):
        compile(generate_model_py(load("plate_hole.json")), "model.py", "exec")


if __name__ == "__main__":
    unittest.main()
