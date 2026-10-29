"""Stdlib validator for the JSON Schema subset used by `tools/live/schemas/`.

Supported keywords: `type` (single or list, incl. "null"), `enum`, `const`, `required`, `properties`,
`additionalProperties: false`, `items`, `maxLength`, `maxItems`. Any other keyword raises
`UnsupportedSchema`, so a schema that relies on an unimplemented rule fails closed instead of passing silently.
"""

from __future__ import annotations

_IGNORED = {"$id", "$schema", "title", "description"}
_SUPPORTED = {"type", "enum", "const", "required", "properties", "additionalProperties", "items",
              "maxLength", "maxItems"} | _IGNORED


class UnsupportedSchema(ValueError):
    pass


def _matches_type(value, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    raise UnsupportedSchema(f"unknown type {name!r}")


def validate(instance, schema: dict, path: str = "$") -> list[str]:
    """Return a list of human-readable violations; empty means valid."""
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise UnsupportedSchema(f"{path}: unsupported keywords {sorted(unknown)}")
    if schema.get("additionalProperties", False) is not False:
        raise UnsupportedSchema(f"{path}: only additionalProperties false is supported")

    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_matches_type(instance, name) for name in names):
            return [f"{path}: expected type {'|'.join(names)}, got {type(instance).__name__}"]
    if "const" in schema and instance != schema["const"]:
        return [f"{path}: expected const {schema['const']!r}"]
    if "enum" in schema and instance not in schema["enum"]:
        return [f"{path}: value {instance!r} not in enum"]

    errors: list[str] = []
    if isinstance(instance, str) and "maxLength" in schema and len(instance) > schema["maxLength"]:
        errors.append(f"{path}: length {len(instance)} > maxLength {schema['maxLength']}")
    if isinstance(instance, list):
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: {len(instance)} items > maxItems {schema['maxItems']}")
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], f"{path}[{index}]"))
    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing required property {name!r}")
        if "additionalProperties" in schema:
            for name in sorted(set(instance) - set(properties)):
                errors.append(f"{path}: unexpected property {name!r}")
        for name, subschema in properties.items():
            if name in instance:
                errors.extend(validate(instance[name], subschema, f"{path}.{name}"))
    return errors
