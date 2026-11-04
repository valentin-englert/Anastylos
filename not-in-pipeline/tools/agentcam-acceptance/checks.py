#!/usr/bin/env python3
"""Frozen-contract replay checks for the AgentCam/CAD Wave-1 evaluator.

Standard library only, no network. The functions here are pure: they take
loaded JSON documents plus the frozen schemas and return violation lists or
result dicts. File I/O and scorecard emission live in accept.py.

Scope (honest): this is NOT a general Draft 2020-12 validator. Full schema
validation was done at G0 with ajv (see CONTRACT.json "validation"). These
checks replay the frozen *instances* against the schema constraints that
govern them, plus the semantic rules from the contracts README and the
blueprint frozen resolutions. Any byte drift in schemas or vectors breaks
the manifest gate first.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# Transport-vocabulary tripwire tokens for control-transitions replay. The
# state/status tokens come from the schema enums at runtime; these literals
# are the transport/error vocabulary of MCP-AND-TRANSPORT-CONTRACTS.md and
# the G0 transition vectors themselves.
TRANSITION_TOKENS = frozenset([
    "409", "revision_conflict", "idempotency_conflict", "snapshot_required",
    "duplicate", "replay", "replayed", "collapsed", "denied", "reservation",
    "max_images", "queued", "retake", "failed", "supersede", "superseded",
    "revoke", "revoked", "expire", "expired", "cancel", "cancelled",
    "pause", "paused", "resume", "cursor", "gap", "snapshot", "ttl", "cas",
    "shutter", "latch", "bytes", "hash", "consent", "grant", "lease",
    "disarm", "disarmed", "foreground", "background", "receipt",
])


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> tuple[int, str]:
    raw = path.read_bytes()
    return len(raw), sha256_bytes(raw)


def recompute_contract_sha(manifest_files: list[dict]) -> str:
    """Recompute contract_sha256: sha256 over sha256sum-style lines.

    Line format (verified against the frozen G0 value on 2026-10-29):
    "<file-sha256>  <manifest-path>\\n" in manifest order.
    """
    lines = "".join(f"{f['sha256']}  {f['path']}\n" for f in manifest_files)
    return sha256_bytes(lines.encode("utf-8"))


def load_json_strict(path: Path):
    """Parse JSON, rejecting NaN/Infinity tokens (control-negative "nan")."""

    def _reject(value: str):
        raise ValueError(f"non-strict JSON constant: {value}")

    return json.loads(
        path.read_text(encoding="utf-8"), parse_constant=_reject
    )


def loads_strict(text: str):
    def _reject(value: str):
        raise ValueError(f"non-strict JSON constant: {value}")

    return json.loads(text, parse_constant=_reject)


def canonical_body_hash(doc) -> str:
    return sha256_bytes(
        json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def resolve_pointer(doc, pointer: str):
    """Resolve a local JSON pointer ("#/a/b/0"). Raises KeyError/IndexError."""
    if not pointer.startswith("#/"):
        raise ValueError(f"external or root pointer not supported: {pointer}")
    node = doc
    for raw in pointer[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def set_pointer(doc, pointer: str, value) -> None:
    """Set a value at a document JSON pointer ("#/a/b" or "/a/b").

    Frozen vectors use plain RFC 6901 form ("/target/..."); schema $refs
    use fragment form ("#/..."). Both address this document.
    """
    body = pointer[1:] if pointer.startswith("#") else pointer
    if not body.startswith("/"):
        raise ValueError(f"external pointer not supported: {pointer}")
    parts = [
        p.replace("~1", "/").replace("~0", "~")
        for p in body[1:].split("/")
    ]
    node = doc
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def _check_number(node, schema: dict, path: str) -> list[str]:
    bad: list[str] = []
    if isinstance(node, bool):
        return [f"{path}: boolean is not a number"]
    if not isinstance(node, (int, float)):
        return [f"{path}: expected number, got {type(node).__name__}"]
    if schema.get("type") == "integer" and not isinstance(node, int):
        bad.append(f"{path}: expected integer, got float")
    for key, ok, msg in (
        ("minimum", lambda v, lim: v >= lim, "below minimum"),
        ("maximum", lambda v, lim: v <= lim, "above maximum"),
    ):
        if key in schema and not ok(node, schema[key]):
            bad.append(f"{path}: {msg} {schema[key]}")
    for key, cmp, msg in (
        ("exclusiveMinimum", lambda v, lim: v > lim, "not above"),
        ("exclusiveMaximum", lambda v, lim: v < lim, "not below"),
    ):
        if key in schema:
            lim = schema[key]
            if isinstance(lim, bool):  # draft-04 style flag
                lim = schema.get(
                    "minimum" if key == "exclusiveMinimum" else "maximum"
                )
                if lim is None:
                    continue
            if not cmp(node, lim):
                bad.append(f"{path}: {msg} exclusive bound {schema[key]}")
    return bad


def match_schema(node, schema: dict, root: dict, path: str = "$") -> list[str]:
    """Check one instance node against a (sub)schema. Returns violations.

    Supported subset: $ref (local), type, const, enum, required,
    properties, additionalProperties, items, min/maxItems, min/maxLength,
    pattern, numeric bounds, oneOf, anyOf, allOf, if/then. "format" is
    not validated (patterns carry the frozen constraints).
    """
    if "$ref" in schema:
        return match_schema(node, resolve_pointer(root, schema["$ref"]),
                            root, path)
    bad: list[str] = []
    want = schema.get("type")
    if want is not None:
        kinds = [want] if isinstance(want, str) else list(want)
        if not any(_type_matches(node, k) for k in kinds):
            return [f"{path}: expected {want}, "
                    f"got {_type_name(node)}"]
    if "const" in schema and node != schema["const"]:
        bad.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and node not in schema["enum"]:
        bad.append(f"{path}: {node!r} not in enum")
    if isinstance(node, dict):
        for key in schema.get("required", []):
            if key not in node:
                bad.append(f"{path}: missing required {key!r}")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in node:
                bad.extend(match_schema(node[key], sub, root,
                                        f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            for key in node:
                if key not in props:
                    bad.append(f"{path}: unexpected property {key!r}")
    if isinstance(node, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, sub in enumerate(node):
                bad.extend(match_schema(sub, items, root, f"{path}[{i}]"))
        if "minItems" in schema and len(node) < schema["minItems"]:
            bad.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(node) > schema["maxItems"]:
            bad.append(f"{path}: more than {schema['maxItems']} items")
    if isinstance(node, str):
        if "minLength" in schema and len(node) < schema["minLength"]:
            bad.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(node) > schema["maxLength"]:
            bad.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], node):
            bad.append(f"{path}: does not match {schema['pattern']}")
    if (isinstance(node, (int, float)) and not isinstance(node, bool)
            and any(k in schema for k in ("minimum", "maximum",
                                          "exclusiveMinimum",
                                          "exclusiveMaximum"))):
        bad.extend(_check_number(node, schema, path))
    if "oneOf" in schema:
        passed = sum(1 for b in schema["oneOf"]
                     if not match_schema(node, b, root, path))
        if passed != 1:
            bad.append(f"{path}: oneOf matched {passed} branches")
    if "anyOf" in schema:
        if all(match_schema(node, b, root, path) for b in schema["anyOf"]):
            bad.append(f"{path}: anyOf matched no branch")
    if "allOf" in schema:
        for b in schema["allOf"]:
            bad.extend(match_schema(node, b, root, path))
    if "if" in schema:
        if not match_schema(node, schema["if"], root, path):
            bad.extend(match_schema(node, schema.get("then", {}), root,
                                    path))
    return bad


def _type_name(node) -> str:
    if node is None:
        return "null"
    if isinstance(node, bool):
        return "boolean"
    if isinstance(node, int):
        return "integer"
    if isinstance(node, float):
        return "number"
    if isinstance(node, str):
        return "string"
    if isinstance(node, list):
        return "array"
    return "object"


def _type_matches(node, kind: str) -> bool:
    if kind == "number":
        return isinstance(node, (int, float)) and not isinstance(node, bool)
    if kind == "integer":
        return isinstance(node, int) and not isinstance(node, bool)
    return _type_name(node) == kind


def det3(matrix16: list[float]) -> float:
    """Determinant of the 3x3 rotation block of a row-major 4x4 matrix."""
    a, b, c = matrix16[0:3]
    d, e, f = matrix16[4:7]
    g, h, i = matrix16[8:11]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def check_view_request(instance: dict, control_schema: dict) -> list[str]:
    return match_schema(instance, {"$ref": "#/$defs/ViewRequest"},
                        control_schema)


def check_spatial(instance: dict, spatial_schema: dict) -> list[str]:
    return match_schema(instance, spatial_schema, spatial_schema)


def check_cad(instance: dict, cad_schema: dict) -> list[str]:
    return match_schema(instance, cad_schema, cad_schema)


def pose_target_semantics(target: dict) -> list[str]:
    """Fire semantic rules for a pose Target. Empty list = clean.

    Rules (frozen frame x-right/y-down/z-into-plane): camera translation z
    must be negative (in front of the board); rotation block must be proper
    (det +1, no reflection).
    """
    fired: list[str] = []
    if target.get("kind") != "pose":
        return fired
    matrix = target["T_B_C"]["matrix"]
    if matrix[11] >= 0:
        fired.append("board_side: t_z >= 0 violates z-into-plane")
    if det3(matrix) < 0:
        fired.append("reflection: det(R) < 0")
    return fired


def request_time_semantics(created_at: str, expires_at: str) -> list[str]:
    fired: list[str] = []
    if expires_at <= created_at:
        fired.append("expired: expires_at <= created_at")
    return fired


def _apply_patch(base: dict, case: dict) -> dict:
    mutated = copy.deepcopy(base)
    if "patch" in case:
        mutated.update(copy.deepcopy(case["patch"]))
    if "json_pointer" in case:
        set_pointer(mutated, case["json_pointer"], case["value"])
    return mutated


def check_control_negative(base: dict, cases_doc: dict,
                           control_schema: dict) -> list[dict]:
    """Replay control-negative.json. Each case returns its outcome + ok."""
    results: list[dict] = []
    for case in cases_doc["cases"]:
        cid = case["id"]
        expected: str = case["expected"]
        if cid == "nan":
            try:
                loads_strict('{"probe": NaN}')
                results.append({"id": cid, "expected": expected,
                                "outcome": "parsed",
                                "ok": False})
            except ValueError:
                results.append({"id": cid, "expected": expected,
                                "outcome": "parse-reject",
                                "ok": expected.startswith("parse-reject")})
            continue
        mutated = _apply_patch(base, case)
        violations = check_view_request(mutated, control_schema)
        clean = not violations
        if expected.startswith("schema-reject"):
            results.append({"id": cid, "expected": expected,
                            "outcome": "schema-reject" if violations
                            else "schema-accept",
                            "ok": bool(violations),
                            "violations": violations[:3]})
        elif cid == "wrong-source":
            differs = mutated["source_id"] != base["source_id"]
            results.append({"id": cid, "expected": expected,
                            "outcome": "structurally-valid, source differs; "
                            "live authorization NOT_TESTED (no Core store)",
                            "ok": clean and differs})
        elif cid == "same-operation-new-body":
            same_op = (mutated["operation_id"] == base["operation_id"]
                       and mutated["session_id"] == base["session_id"])
            body_differs = (canonical_body_hash(mutated)
                            != canonical_body_hash(base))
            results.append({"id": cid, "expected": expected,
                            "outcome": "409 idempotency_conflict"
                            if (clean and same_op and body_differs)
                            else "no-conflict",
                            "ok": clean and same_op and body_differs})
        else:  # semantic-reject cases: schema must pass, rule must fire
            fired = pose_target_semantics(mutated["target"])
            fired += request_time_semantics(mutated["created_at"],
                                            mutated["expires_at"])
            results.append({"id": cid, "expected": expected,
                            "outcome": ("semantic-reject: " + "; ".join(fired))
                            if (clean and fired) else
                            ("schema-accept, no rule fired" if clean
                             else "schema-reject"),
                            "ok": clean and bool(fired)
                            and expected.startswith("semantic-reject")})
    return results


def check_control_transitions(cases_doc: dict,
                              control_schema: dict) -> dict:
    """Replay control-transitions.json structurally + vocabulary tripwire.

    Honest limit: this verifies the 17 cases are well-formed and reference
    frozen state/status/transport vocabulary. Execution against a real Core
    reducer is NOT_TESTED (no reducer exists in the Wave-1 candidate).
    """
    defs = control_schema["$defs"]
    vocab = set(TRANSITION_TOKENS)
    for ref in ("ViewRequest", "Session", "Receipt", "AttemptDelivery"):
        props = defs[ref].get("properties", {})
        for key in ("status", "state", "receipt_status"):
            enum = props.get(key, {}).get("enum", [])
            vocab.update(str(v).lower() for v in enum if v)
    cases = cases_doc["cases"]
    ids = [c["id"] for c in cases]
    per_case = []
    for case in cases:
        words = set(re.findall(r"[A-Za-z0-9_]+",
                               case.get("expect", "").lower()))
        per_case.append({
            "id": case["id"],
            "fields_ok": all(case.get(k) for k in ("id", "given", "expect")),
            "vocab_hits": sorted(words & vocab),
        })
        per_case[-1]["ok"] = (per_case[-1]["fields_ok"]
                              and bool(per_case[-1]["vocab_hits"]))
    return {"n_cases": len(cases), "ids_unique": len(set(ids)) == len(ids),
            "per_case": per_case,
            "execution": "NOT_TESTED (no Core reducer in candidate)"}


def _cue_word(value: float, positive: str, negative: str) -> str | None:
    if abs(value) < 1e-9:
        return None
    mag = round(value, 6)
    text = str(int(mag)) if mag == int(mag) else str(abs(mag))
    word = positive if value > 0 else negative
    return f"{word} {text.lstrip('-')}"


def guidance_recompute(vector: dict) -> dict:
    """Recompute guidance-signs.json analytically (translation + cue words).

    Frozen mapping (board x-right/y-down/z-into-plane): +x right, -x left;
    +y down, -y up; +z closer, -z back.
    """
    notes = []
    if vector.get("frame") != "board x right y down z into plane":
        notes.append(f"unexpected frame: {vector.get('frame')}")
    if vector.get("units") != "mm":
        notes.append(f"unexpected units: {vector.get('units')}")
    raw_t = vector["current_T_B_C"]
    cur = (raw_t[3], raw_t[7], raw_t[11])
    per_case = []
    for i, case in enumerate(vector["cases"]):
        want = case["desired_center_B_mm"]
        delta = [w - c for w, c in zip(want, cur)]
        delta_ok = all(abs(d - e) < 1e-9
                       for d, e in zip(delta,
                                       case["expected_camera_delta_mm"]))
        cues = [c for c in (
            _cue_word(delta[0], "right", "left"),
            _cue_word(delta[1], "down", "up"),
            _cue_word(delta[2], "closer", "back")) if c]
        per_case.append({"index": i, "delta_mm": delta, "cues": cues,
                         "delta_ok": delta_ok,
                         "cues_ok": cues == case["expected_cues"],
                         "ok": delta_ok
                         and cues == case["expected_cues"]})
    return {"per_case": per_case, "notes": notes,
            "required_extensions": vector.get("required_extensions", []),
            "extensions_status":
            "NOT_TESTED (analytic extensions unimplemented in G0 vectors)"}


def cad_parent_map(instance: dict) -> dict[str, str]:
    parents = {}
    for feat in instance.get("features", []):
        parent = feat.get("parameters", {}).get("parent_feature_id")
        if parent:
            parents[feat["feature_id"]] = parent
    return parents


def cad_semantics(instance: dict) -> list[str]:
    """Fire CAD semantic rules (det, parents, cycles). Empty = clean."""
    fired: list[str] = []
    if det3(instance["T_CAD_B"]["matrix"]) < 0:
        fired.append("reflection: det(T_CAD_B) < 0")
    ids = {f["feature_id"] for f in instance.get("features", [])}
    parents = cad_parent_map(instance)
    for child, parent in parents.items():
        if parent not in ids:
            fired.append(f"dangling-parent: {child} -> {parent}")
    seen: set[str] = set()
    for start in parents:
        slow, fast = start, parents.get(start, "")
        trail = {slow}
        while fast in parents and fast not in seen:
            if fast in trail:
                fired.append(f"parent-cycle at {fast}")
                break
            trail.add(fast)
            slow = parents.get(slow, "")
            fast = parents.get(parents.get(fast, ""), "")
        seen.update(trail)
    return fired


def _cad_mutations(base: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    m = copy.deepcopy(base)
    m["features"][0]["kind"] = "planar_profile_extrude"
    out["unknown-op-planar-profile"] = m
    m = copy.deepcopy(base)
    m["features"][0]["parameters"]["size_mm"][0] = -1
    out["negative-size"] = m
    m = copy.deepcopy(base)
    m["features"][0]["basis"] = "model_suggested"
    out["bad-basis-suggested"] = m
    m = copy.deepcopy(base)
    m["T_CAD_B"]["matrix"] = [1, 0, 0, 0, 0, 1, 0, 0,
                              0, 0, -1, 0, 0, 0, 0, 1]
    out["reflection-transform"] = m
    m = copy.deepcopy(base)
    m["features"][1]["parameters"]["parent_feature_id"] = "nope"
    out["dangling-parent"] = m
    hole = copy.deepcopy(base["features"][1])
    first = copy.deepcopy(hole)
    first["feature_id"] = "hole_a"
    first["parameters"]["parent_feature_id"] = "hole_b"
    second = copy.deepcopy(hole)
    second["feature_id"] = "hole_b"
    second["parameters"]["parent_feature_id"] = "hole_a"
    m = copy.deepcopy(base)
    m["features"] = [base["features"][0], first, second]
    out["parent-cycle"] = m
    return out


def check_cad_invalid(base: dict, cases_doc: dict,
                      cad_schema: dict) -> list[dict]:
    """Replay cad-feature-invalid.json: each mutation must reject as stated."""
    mutated = _cad_mutations(base)
    results = []
    for case in cases_doc["cases"]:
        cid = case["id"]
        expected: str = case["expect"]
        instance = mutated[cid]
        violations = check_cad(instance, cad_schema)
        if expected.startswith("schema-reject"):
            results.append({"id": cid, "expected": expected,
                            "outcome": "schema-reject" if violations
                            else "schema-accept",
                            "ok": bool(violations),
                            "violations": violations[:3]})
        else:
            fired = cad_semantics(instance) if not violations else []
            results.append({"id": cid, "expected": expected,
                            "outcome": ("semantic-reject: "
                                        + "; ".join(fired))
                            if (not violations and fired) else
                            ("schema-accept, no rule fired"
                             if not violations else "schema-reject"),
                            "ok": not violations and bool(fired)
                            and expected.startswith("semantic-reject")})
    return results


def _collect_refs(node, into: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                into.append(value)
            else:
                _collect_refs(value, into)
    elif isinstance(node, list):
        for sub in node:
            _collect_refs(sub, into)


def check_mcp_bundle(expanded: dict, template: dict,
                     control_sha256: str) -> dict:
    """Verify the G0 MCP bundle: 18 schemas, 0 dangling refs, names match.

    Each tool schema is self-contained (own 38 $defs), so refs resolve
    against their own tool schema, not the bundle document root.
    """
    tools_exp = expanded.get("tools", [])
    tools_tpl = template.get("tools", [])
    names_exp = [t.get("name") for t in tools_exp]
    names_tpl = [t.get("name") for t in tools_tpl]
    schemas = [t.get(k) for t in tools_exp
               for k in ("inputSchema", "outputSchema")]
    schemas_present = sum(1 for s in schemas if isinstance(s, dict))
    n_refs, dangling, external = 0, [], []
    for tool, key in ((t, k) for t in tools_exp
                      for k in ("inputSchema", "outputSchema")):
        schema = tool.get(key)
        if not isinstance(schema, dict):
            continue
        refs: list[str] = []
        _collect_refs(schema, refs)
        n_refs += len(refs)
        for ref in refs:
            if not ref.startswith("#"):
                external.append(f"{tool.get('name')}.{key}:{ref}")
                continue
            try:
                resolve_pointer(schema, ref)
            except (KeyError, IndexError, ValueError):
                dangling.append(f"{tool.get('name')}.{key}:{ref}")
    return {"n_tools_expanded": len(tools_exp),
            "n_tools_template": len(tools_tpl),
            "names_match": names_exp == names_tpl,
            "tool_names": names_exp,
            "schemas_present": schemas_present,
            "schemas_expected": 18,
            "n_refs": n_refs,
            "dangling": dangling,
            "external": external,
            "source_control_match":
            expanded.get("source_control_schema_sha256") == control_sha256,
            "source_spatial_noted":
            expanded.get("source_spatial_schema_sha256"),
            "ok": names_exp == names_tpl and schemas_present == 18
            and not dangling and not external
            and expanded.get("source_control_schema_sha256")
            == control_sha256}

