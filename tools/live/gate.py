"""Gates for L1 proposals (CONTRACT §3.1): a model output is used only if deterministic checks pass.

The core never imports ``providers/``; a ``Proposer`` is passed in. Every gate returns
``(accepted_output | None, reason)``; on ``None`` the caller keeps its L0 result. Checks:

- provider status ``ok`` and the output re-validated against the task's output schema,
- ``question_wording``: rubric R1–R7, no number, quoted label or cell that is not in the facts,
  ``facts_used`` only names supplied facts, screen-option mode still points at the screen,
- ``answer_interpretation``: choice only from the allowed choices, place and label text only
  as verbatim extracts of what the operator said (the model may select, never add).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

from templates import MACHINE_ID, rubric

SCHEMAS = Path(__file__).resolve().parent / "schemas"
RESULT_SCHEMA = "anastylos-live-provider-result@1"


class Proposer(Protocol):
    def propose(self, task: str, payload: dict, request_id: str) -> dict: ...


def task_schema(task: str) -> dict:
    return json.loads((SCHEMAS / f"task.{task}.json").read_text(encoding="utf-8"))


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def schema_errors(value, schema: dict, where: str = "$") -> list[str]:
    """Validator for the schema subset CONTRACT §3 allows (type, enum, const, required, properties,
    additionalProperties false, items, maxLength, maxItems)."""
    errors = []
    types = schema.get("type")
    if types is not None:
        names = types if isinstance(types, list) else [types]
        ok = any((name in ("number", "integer") and isinstance(value, (int, float)) and not isinstance(value, bool)
                  and (name == "number" or float(value).is_integer())) or
                 (name in _TYPES and isinstance(value, _TYPES[name]) and not (name != "boolean" and isinstance(value, bool)))
                 for name in names)
        if not ok:
            return [f"{where}: expected {names}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} not in {schema['enum']}")
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        errors.append(f"{where}: longer than {schema['maxLength']}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{where}: more than {schema['maxItems']} items")
        for i, item in enumerate(value):
            errors += schema_errors(item, schema.get("items", {}), f"{where}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        errors += [f"{where}: missing {k!r}" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"{where}: unexpected {k!r}" for k in value if k not in props]
        for k, sub in props.items():
            if k in value:
                errors += schema_errors(value[k], sub, f"{where}.{k}")
    return errors


def result_errors(result: dict, task: str) -> list[str]:
    """Why a provider result cannot be used (empty when its output may go to the task gate)."""
    if not isinstance(result, dict) or result.get("schema") != RESULT_SCHEMA:
        return [f"not a {RESULT_SCHEMA}"]
    if result.get("task") != task:
        return [f"result for task {result.get('task')!r}, expected {task!r}"]
    if result.get("status") != "ok":
        return [f"provider status {result.get('status')!r}"]
    return schema_errors(result.get("output"), task_schema(task)["output"], "output")


_NUMBER = re.compile(r"\d+")
_QUOTED = re.compile(r"„([^“]*)“")
_CELL = re.compile(r"\bZelle\s+(\d+)", re.IGNORECASE)


def _fact_strings(facts) -> list[str]:
    if isinstance(facts, dict):
        return [s for v in facts.values() for s in _fact_strings(v)]
    if isinstance(facts, list):
        return [s for v in facts for s in _fact_strings(v)]
    return [str(facts)] if facts is not None else []


def wording_gate(output: dict, prompt: dict) -> tuple[dict | None, str]:
    text, facts = output["text"], prompt["facts"]
    known = " ".join(_fact_strings(facts))
    has_anchor = bool(facts.get("anchor")) and facts["anchor"] in text
    checks = rubric(text, anchor_possible=prompt["anchor_possible"], has_anchor=has_anchor)
    if not checks["pass"]:
        return None, "rubric failed: " + ", ".join(k for k, v in checks.items() if not v and k != "pass")
    new_numbers = sorted(set(_NUMBER.findall(text)) - set(_NUMBER.findall(known)))
    if new_numbers:
        return None, f"numbers not in the facts: {new_numbers}"
    new_labels = [q for q in _QUOTED.findall(text) if q not in known]
    if new_labels:
        return None, f"quoted names not in the facts: {new_labels}"
    if set(output["facts_used"]) - set(facts):
        return None, f"facts_used names unknown facts: {sorted(set(output['facts_used']) - set(facts))}"
    if prompt["mode"] == "open_with_screen_options" and "Bildschirm" not in text:
        return None, "screen options exist but the wording does not point at the screen"
    if prompt["mode"] == "spoken_options" and not all(o in text for o in facts.get("options", [])):
        return None, "a spoken option was dropped or changed"
    return output, "accepted"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def interpretation_gate(output: dict, allowed_choices: list[str], operator_text: str, kind: str) -> tuple[dict | None, str]:
    intent, choice = output["intent"], output["choice"]
    if choice is not None and choice not in allowed_choices:
        return None, f"choice {choice!r} not among the allowed choices"
    if (intent == "choose") != (choice is not None):
        return None, "a choice must come with intent 'choose' and only then"
    for field in ("place_text", "label_text"):
        value = output[field]
        if value is not None and (not value.strip() or _norm(value) not in _norm(operator_text)):
            return None, f"{field} is not a verbatim part of what the operator said"
        if value is not None and MACHINE_ID.search(value) and not MACHINE_ID.search(operator_text):
            return None, f"{field} carries a machine id"
    if intent == "describe_place" and output["place_text"] is None:
        return None, "describe_place without place_text"
    if kind == "read_back" and intent not in ("confirm", "reject", "unclear"):
        return None, f"intent {intent!r} is not an answer to a read-back"
    if kind != "read_back" and intent in ("confirm", "reject"):
        return None, f"intent {intent!r} is not an answer to an open question"
    if intent == "correction":
        return None, "corrections go back to the operator (L0 asks again)"
    return {**output, "not_a_part": False}, "accepted"
