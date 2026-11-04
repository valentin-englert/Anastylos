"""Stdlib-only validator for the JSON Schema subset used in ../../schemas.

Supported keywords: type, properties, required, additionalProperties,
items, enum, const, pattern, minimum, maximum, minLength, minItems,
maxItems, anyOf, oneOf, $ref (local "#/$defs/..."). This keeps the Core
dependency-free; if the third-party ``jsonschema`` package happens to be
installed, the tests additionally cross-check with it.
"""

from __future__ import annotations

import re
from pathlib import Path

from anastylos_microscopy.util import read_json

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
SCHEMA_FILES = {
    "inspection": "microscopy-inspection.v1.schema.json",
    "calibration": "microscopy-calibration.v1.schema.json",
    "capture_manifest": "capture-manifest.v1.schema.json",
    "dataset": "microscopy-dataset.v1.schema.json",
    "review": "microscopy-review.v1.schema.json",
    "batch": "microscopy-batch.v1.schema.json",
}

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def load_schema(name: str) -> dict:
    return read_json(SCHEMA_DIR / SCHEMA_FILES[name])


def _resolve(ref: str, root: dict) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref supported, got {ref}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def validate(instance, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    root = root or schema
    errors: list[str] = []
    if "$ref" in schema:
        return validate(instance, _resolve(schema["$ref"], root), root, path)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](instance) for t in types):
            return [f"{path}: expected {types}, got {type(instance).__name__}"]
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in {schema['enum']}")
    if isinstance(instance, str):
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: {instance!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                errors += validate(value, props[key], root, f"{path}.{key}")
            else:
                extra = schema.get("additionalProperties", True)
                if extra is False:
                    errors.append(f"{path}: unexpected property {key!r}")
                elif isinstance(extra, dict):
                    errors += validate(value, extra, root, f"{path}.{key}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(instance):
                errors += validate(item, schema["items"], root, f"{path}[{index}]")
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            matches = [not validate(instance, sub, root, path) for sub in schema[keyword]]
            count = sum(matches)
            if (keyword == "anyOf" and count == 0) or (keyword == "oneOf" and count != 1):
                errors.append(f"{path}: does not satisfy {keyword} ({count} branches matched)")
    return errors


def validate_named(instance, name: str) -> list[str]:
    return validate(instance, load_schema(name))
