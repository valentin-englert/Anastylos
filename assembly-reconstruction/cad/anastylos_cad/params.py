"""Stable editable parameters over a validated cad-feature-model-v1 document.

Standard library only.

The validated feature specification stays the authoritative parametric
source of truth. This module derives a stable parameter table from it
(`extract_params`), applies operator edits deterministically
(`apply_edits`), and merges later machine re-estimates without ever
silently overwriting operator-specified values (`merge_estimates`).

Parameter IDs are `{feature_id}.{role}` and are stable across revisions:
`base_plate.size_x_mm`, `hole_1.radius_mm`, `hole_1.center_x_mm`, ...
Units are always mm. Provenance (estimated vs operator-specified plus
revision history) lives in a sidecar document, never in the frozen
feature schema -- see PARAMETRIC-PROPOSAL.md for the contract-owner
proposal to standardize it.
"""

import copy
import math

EDITS_SCHEMA = "anastylos-cad-param-edits@1"
PROVENANCE_SCHEMA = "anastylos-cad-param-provenance@1"

DIM_MIN, DIM_MAX = 0.0, 1000.0       # exclusive min, inclusive max
CENTER_MIN, CENTER_MAX = -1000.0, 1000.0


class ParamError(ValueError):
    """Reject: unknown/stable-id, bounds, stale base, or derived param."""


def _finite_number(value):
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value))


def model_sha256(model):
    """Stable hash of the canonical model bytes (revision identity).

    Uses the same canonical form the worker writes, so the sha256 of a
    tool-written feature-model.json file always equals the base hash its
    edits must pin. Hand-written files hash by parsed content instead.
    """
    from anastylos_cad import evidence as ev
    return ev.sha256_bytes(ev.canonical_json(model).encode("utf-8"))


def _roles_for(feat):
    """(param_id suffix, getter, setter, min, max, editable) per kind."""
    fid = feat["feature_id"]
    kind = feat["kind"]
    params = feat["parameters"]
    if kind == "box":
        for i, axis in enumerate("xyz"):
            yield ("size_%s_mm" % axis,
                   lambda p=params, j=i: p["size_mm"][j],
                   lambda v, p=params, j=i: p["size_mm"].__setitem__(j, v),
                   DIM_MIN, DIM_MAX, True)
    elif kind == "cylinder":
        yield ("radius_mm", lambda p=params: p["radius_mm"],
               lambda v, p=params: p.__setitem__("radius_mm", v),
               DIM_MIN, DIM_MAX, True)
        yield ("height_mm", lambda p=params: p["height_mm"],
               lambda v, p=params: p.__setitem__("height_mm", v),
               DIM_MIN, DIM_MAX, True)
    elif kind == "through_hole":
        yield ("radius_mm", lambda p=params: p["radius_mm"],
               lambda v, p=params: p.__setitem__("radius_mm", v),
               DIM_MIN, DIM_MAX, True)
        yield ("depth_mm", lambda p=params: p["depth_mm"],
               None, DIM_MIN, DIM_MAX, False)  # derived: == parent thickness
        matrix = feat["T_CAD_feature"]["matrix"]
        for i, axis in enumerate("xyz"):
            yield ("center_%s_mm" % axis,
                   lambda m=matrix, j=i: m[3 + 4 * j],
                   lambda v, m=matrix, j=i: m.__setitem__(3 + 4 * j, v),
                   CENTER_MIN, CENTER_MAX, True)


def extract_params(model):
    """Stable parameter table for a validated model (read-only view)."""
    table = {}
    for feat in model["features"]:
        fid = feat["feature_id"]
        for suffix, getter, _setter, lo, hi, editable in _roles_for(feat):
            table["%s.%s" % (fid, suffix)] = {
                "value": getter(),
                "units": "mm",
                "feature_id": fid,
                "role": suffix,
                "minimum": lo,
                "maximum": hi,
                "editable": editable,
            }
    return table


def _setters_for(model):
    setters = {}
    for feat in model["features"]:
        fid = feat["feature_id"]
        for suffix, _get, setter, lo, hi, editable in _roles_for(feat):
            setters["%s.%s" % (fid, suffix)] = (setter, lo, hi, editable)
    return setters


def _check_edits_doc(edits):
    if not isinstance(edits, dict):
        raise ParamError("edits document must be an object")
    if edits.get("schema") != EDITS_SCHEMA:
        raise ParamError("edits schema must be %r" % EDITS_SCHEMA)
    items = edits.get("edits")
    if not isinstance(items, list) or not items:
        raise ParamError("edits.edits must be a non-empty array")
    return items


def apply_edits(model, edits, prior_provenance=None):
    """Apply operator edits; return (new_model, provenance).

    The input model is never mutated. The new model carries revision+1
    and parent_model_sha256 of the base. Provenance preserves the
    original estimate as history[0] and marks revised params
    operator-specified. Raises ParamError on any reject (atomic: either
    all edits apply or none do).
    """
    items = _check_edits_doc(edits)
    base_sha = model_sha256(model)
    want = edits.get("base_model_sha256")
    if want is not None and want != base_sha:
        raise ParamError("stale base: edits target %s, model is %s"
                         % (want, base_sha))
    setters = _setters_for(model)
    staged = []
    for item in items:
        if not isinstance(item, dict):
            raise ParamError("each edit must be an object")
        pid = item.get("param_id")
        if pid not in setters:
            raise ParamError("unknown parameter %r" % (pid,))
        setter, lo, hi, editable = setters[pid]
        if not editable:
            raise ParamError("parameter %r is derived, not editable" % (pid,))
        value = item.get("value")
        if not _finite_number(value):
            raise ParamError("parameter %r value must be finite" % (pid,))
        if not (value > lo and value <= hi):
            raise ParamError("parameter %r value %r out of (%r, %r]"
                             % (pid, value, lo, hi))
        staged.append((pid, setter, float(value), item.get("reason", "")))
    new_model = copy.deepcopy(model)
    # rebind setters to the copy by re-deriving on it
    fresh = _setters_for(new_model)
    for pid, _setter, value, _reason in staged:
        fresh[pid][0](value)
    _sync_derived_depths(new_model)
    new_model["revision"] = model["revision"] + 1
    new_model["parent_model_sha256"] = base_sha
    author = edits.get("author", "operator")
    provenance = _build_provenance(model, new_model, prior_provenance,
                                   staged, base_sha, author)
    return new_model, provenance


def _build_provenance(old_model, new_model, prior, staged, base_sha, author):
    old_table = extract_params(old_model)
    new_table = extract_params(new_model)
    staged_ids = {pid: (value, reason) for pid, _s, value, reason in staged}
    carried = {}
    if isinstance(prior, dict):
        carried = prior.get("params", {})
    params = {}
    for pid, spec in new_table.items():
        if pid in carried and isinstance(carried[pid], dict):
            history = list(carried[pid].get("history", []))
            origin = carried[pid].get("origin", "estimated")
        else:
            history = [{"revision": old_model["revision"],
                        "value": old_table[pid]["value"],
                        "origin": "estimated",
                        "author": "reconstruction",
                        "reason": "machine estimate"}]
            origin = "estimated"
        if pid in staged_ids:
            value, reason = staged_ids[pid]
            history.append({"revision": new_model["revision"],
                            "value": value,
                            "origin": "operator-specified",
                            "author": author,
                            "reason": reason})
            origin = "operator-specified"
        params[pid] = {"value": spec["value"], "units": "mm",
                       "origin": origin, "history": history}
    return {"schema": PROVENANCE_SCHEMA,
            "model_id": new_model["model_id"],
            "revision": new_model["revision"],
            "base_model_sha256": base_sha,
            "params": params}


def _sync_derived_depths(model, new_params=None):
    """Through-hole depth always equals parent box/cylinder thickness."""
    thickness = {}
    for feat in model["features"]:
        if feat["kind"] == "box":
            thickness[feat["feature_id"]] = feat["parameters"]["size_mm"][2]
        elif feat["kind"] == "cylinder":
            thickness[feat["feature_id"]] = feat["parameters"]["height_mm"]
    for feat in model["features"]:
        if feat["kind"] != "through_hole":
            continue
        parent = feat["parameters"]["parent_feature_id"]
        if parent in thickness:
            feat["parameters"]["depth_mm"] = thickness[parent]
            if new_params is not None:
                pid = "%s.depth_mm" % feat["feature_id"]
                if pid in new_params:
                    new_params[pid]["value"] = thickness[parent]


def merge_estimates(current_model, current_provenance, estimate_model):
    """Merge a fresh machine estimate without touching operator values.

    Every operator-specified parameter keeps the operator value; the new
    machine value is recorded as superseded (never applied silently).
    Estimated parameters take the new machine value with appended
    history. Returns (merged_model, report).
    """
    if not isinstance(current_provenance, dict):
        raise ParamError("current provenance must be an object")
    old_params = current_provenance.get("params", {})
    cur_table = extract_params(current_model)
    est_table = extract_params(estimate_model)
    if set(cur_table) != set(est_table):
        raise ParamError("estimate has a different parameter set; "
                         "refit explicitly instead of merging")
    merged = copy.deepcopy(current_model)
    fresh = _setters_for(merged)
    base_sha = model_sha256(current_model)
    merged["revision"] = current_model["revision"] + 1
    merged["parent_model_sha256"] = base_sha
    report = []
    new_params = {}
    for pid in sorted(cur_table):
        origin = old_params.get(pid, {}).get("origin", "estimated")
        history = list(old_params.get(pid, {}).get("history", []))
        est_value = est_table[pid]["value"]
        if not cur_table[pid]["editable"]:
            report.append({"param_id": pid, "action": "derived_follows",
                           "value": est_value})
            history.append({"revision": merged["revision"],
                            "value": est_value,
                            "origin": "estimated", "author": "reconstruction",
                            "reason": "derived parameter follows geometry"})
            new_params[pid] = {"value": est_value, "units": "mm",
                               "origin": origin, "history": history}
            continue
        if origin == "operator-specified":
            report.append({"param_id": pid, "action": "kept_operator_value",
                           "kept": cur_table[pid]["value"],
                           "superseded_estimate": est_value})
            history.append({"revision": merged["revision"],
                            "value": cur_table[pid]["value"],
                            "origin": "operator-specified",
                            "author": "merge",
                            "reason": "operator value kept; machine "
                                      "estimate %r superseded" % (est_value,)})
        else:
            fresh[pid][0](est_value)
            report.append({"param_id": pid, "action": "took_estimate",
                           "previous": cur_table[pid]["value"],
                           "value": est_value})
            history.append({"revision": merged["revision"], "value": est_value,
                            "origin": "estimated", "author": "reconstruction",
                            "reason": "fresh multiview estimate"})
        new_params[pid] = {"value": extract_params(merged)[pid]["value"],
                           "units": "mm", "origin": origin,
                           "history": history}
    _sync_derived_depths(merged, new_params)
    provenance = {"schema": PROVENANCE_SCHEMA,
                  "model_id": merged["model_id"],
                  "revision": merged["revision"],
                  "base_model_sha256": base_sha,
                  "params": new_params}
    return merged, report, provenance
