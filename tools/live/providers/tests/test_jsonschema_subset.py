import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake import VALID_OUTPUTS  # noqa: E402
from jsonschema_subset import UnsupportedSchema, validate  # noqa: E402
from provider_core import SCHEMAS_DIR, TASKS, task_schema  # noqa: E402
from smoke import SYNTHETIC_PAYLOADS  # noqa: E402


class ValidatorTest(unittest.TestCase):
    def test_types_including_nullable(self):
        self.assertEqual(validate(None, {"type": ["string", "null"]}), [])
        self.assertTrue(validate(3, {"type": ["string", "null"]}))
        self.assertTrue(validate(True, {"type": "integer"}))
        self.assertEqual(validate(2.5, {"type": "number"}), [])

    def test_enum_const_and_lengths(self):
        self.assertTrue(validate("x", {"enum": ["a", None]}))
        self.assertEqual(validate(None, {"type": ["string", "null"], "enum": ["a", None]}), [])
        self.assertTrue(validate("b", {"const": "a"}))
        self.assertTrue(validate("abc", {"type": "string", "maxLength": 2}))
        self.assertTrue(validate([1, 2, 3], {"type": "array", "maxItems": 2}))

    def test_objects_and_items(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["a"],
                  "properties": {"a": {"type": "array", "items": {"type": "string"}}}}
        self.assertEqual(validate({"a": ["x"]}, schema), [])
        self.assertTrue(validate({}, schema))
        self.assertTrue(validate({"a": [], "b": 1}, schema))
        self.assertEqual(validate({"a": ["x", 2]}, schema), ["$.a[1]: expected type string, got int"])

    def test_unsupported_keyword_fails_closed(self):
        with self.assertRaises(UnsupportedSchema):
            validate("x", {"type": "string", "pattern": "^x$"})
        with self.assertRaises(UnsupportedSchema):
            validate({}, {"type": "object", "additionalProperties": True})

    def test_contract_schemas_are_within_the_subset(self):
        for task in TASKS:
            spec = task_schema(task)
            self.assertEqual(validate(SYNTHETIC_PAYLOADS[task], spec["input"]), [], task)
            self.assertEqual(validate(VALID_OUTPUTS[task], spec["output"]), [], task)
        for name in ("provider-result.json", "operator-input.json"):
            validate({}, json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
