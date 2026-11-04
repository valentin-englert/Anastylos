"""Deterministic construction-script generation for validated v1 models.

Standard library only.

The compiler emits `model.py` from fixed templates: the validated feature
parameters travel as one JSON literal, and the generated code calls exactly
one allowlisted entry point (`anastylos_cad.builder.build_solids`). No part
of the input can become code -- numbers pass through `json.dumps`, which
cannot emit syntax beyond literals. This is what makes executing the
generated script safe: it is compiler output, never model-authored Python.

Same validated input always yields byte-identical `model.py`.
"""

import json

GENERATOR = "anastylos_cad.compile v1"
BUILDER_ENTRY = "builder.build_solids"

TEMPLATE = '''"""Generated construction script -- do not hand-edit.

Generator: %(generator)s
Model: %(model_id)s revision %(revision)d
Regenerate: cadwork.py build --spec feature-model.json --out <dir>
"""
import json
import sys

from anastylos_cad import builder

PARAMS_JSON = %(params_literal)s


def main():
    params = json.loads(PARAMS_JSON)
    solids = builder.build_solids(params["features"])
    return solids


if __name__ == "__main__":
    main()
'''


def canonical_params(model):
    """Extract the compiler input: features + adapter, sorted and rounded."""
    return {
        "schema": model["schema"],
        "model_id": model["model_id"],
        "revision": model["revision"],
        "units": model["units"],
        "T_CAD_B": model["T_CAD_B"],
        "features": sorted(model["features"],
                           key=lambda f: f["feature_id"]),
    }


def generate_model_py(model):
    """Render deterministic model.py source for a validated model."""
    params = canonical_params(model)
    literal = json.dumps(params, sort_keys=True, separators=(",", ":"),
                         allow_nan=False)
    return TEMPLATE % {"generator": GENERATOR,
                       "model_id": model["model_id"],
                       "revision": model["revision"],
                       "params_literal": repr(literal)}
