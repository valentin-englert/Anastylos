"""Schema-shape + semantic validation for cad-feature-model-v1.

Standard library only; safe to run outside the CAD worker venv.

Two rejection classes (frozen contract vectors distinguish them):
  SCHEMA   -- shape/type/enum/range violation the JSON Schema would reject.
  SEMANTIC -- determinant/parent-existence/acyclicity/depth violation that
              JSON Schema cannot express; rejected before the kernel.

Never raises on malformed input: every failure returns a Reject with a
stable machine-readable code and a human message.
"""

import math
import re

SCHEMA = "schema-reject"
SEMANTIC = "semantic-reject"

SCHEMA_ID = "cad-feature-model-v1"
UNITS = "mm"
MAX_FEATURES = 100
MAX_DIM_MM = 1000.0
MAX_EVIDENCE_IDS = 100
MAX_ASSUMPTIONS = 20
MAX_ASSUMPTION_LEN = 500

KINDS = ("box", "cylinder", "through_hole")
BASES = ("geometric_measurement", "operator_testimony", "model_assumption")

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
FEATURE_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")

MODEL_KEYS = frozenset([
    "schema", "model_id", "revision", "parent_model_sha256",
    "dataset_sha256", "units", "T_CAD_B", "features",
])
FEATURE_KEYS = frozenset([
    "feature_id", "kind", "parameters", "basis", "evidence_ids",
    "uncertainty_mm", "assumptions", "T_CAD_feature",
])
TRANSFORM_KEYS = frozenset(["from", "to", "units", "matrix"])
BOX_PARAMS = frozenset(["size_mm"])
CYLINDER_PARAMS = frozenset(["radius_mm", "height_mm"])
HOLE_PARAMS = frozenset(["radius_mm", "depth_mm", "parent_feature_id"])

# Rotation-part tolerances for rigid-transform checks.
DET_TOL = 1e-6
ORTHO_TOL = 1e-6


class Reject(Exception):
    """A validation rejection. `kind` is SCHEMA or SEMANTIC."""

    def __init__(self, kind, code, message):
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.message = message

    def as_dict(self):
        return {"ok": False, "reject": self.kind, "code": self.code,
                "error": self.message}


def _fail_schema(code, message):
    raise Reject(SCHEMA, code, message)


def _fail_semantic(code, message):
    raise Reject(SEMANTIC, code, message)


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_finite(value, what):
    if not _is_num(value) or not math.isfinite(value):
        _fail_semantic("nonfinite-number",
                       "%s must be a finite number" % what)


def _check_bounded_dim(value, what):
    _check_finite(value, what)
    if value <= 0:
        _fail_schema("non-positive-dimension",
                     "%s must be > 0 (got %r)" % (what, value))
    if value > MAX_DIM_MM:
        _fail_schema("dimension-exceeds-bound",
                     "%s exceeds %g mm (got %r)" % (what, MAX_DIM_MM, value))


def _check_transform(node, what):
    if not isinstance(node, dict):
        _fail_schema("transform-not-object", "%s must be an object" % what)
    if set(node) != TRANSFORM_KEYS:
        _fail_schema("transform-keys",
                     "%s must have exactly %s" % (what, sorted(TRANSFORM_KEYS)))
    for end in ("from", "to"):
        label = node[end]
        if not isinstance(label, str) or not (1 <= len(label) <= 120):
            _fail_schema("transform-label",
                         "%s.%s must be a 1..120 char label" % (what, end))
    if node["units"] != UNITS:
        _fail_schema("transform-units",
                     "%s.units must be %r" % (what, UNITS))
    matrix = node["matrix"]
    if not isinstance(matrix, list) or len(matrix) != 16:
        _fail_schema("transform-matrix",
                     "%s.matrix must be a 16-number list" % what)
    for value in matrix:
        _check_finite(value, "%s.matrix entry" % what)
    return matrix


def _rotation_det(matrix):
    r = matrix
    return (r[0] * (r[5] * r[10] - r[6] * r[9])
            - r[1] * (r[4] * r[10] - r[6] * r[8])
            + r[2] * (r[4] * r[9] - r[5] * r[8]))


def _check_proper_rotation(matrix, what):
    """det(R) == +1 and R orthonormal: a real rotation, never a reflection."""
    det = _rotation_det(matrix)
    if abs(det - 1.0) > DET_TOL:
        _fail_semantic("improper-rotation",
                       "%s rotation determinant is %.6f, want +1 "
                       "(reflection/change-of-handedness rejected)"
                       % (what, det))
    r = matrix
    rows = [(r[0], r[1], r[2]), (r[4], r[5], r[6]), (r[8], r[9], r[10])]
    for i, row in enumerate(rows):
        norm = math.sqrt(sum(v * v for v in row))
        if abs(norm - 1.0) > ORTHO_TOL:
            _fail_semantic("non-rigid-transform",
                           "%s row %d is not unit length" % (what, i))
    dots = [sum(a * b for a, b in zip(rows[0], rows[1])),
            sum(a * b for a, b in zip(rows[0], rows[2])),
            sum(a * b for a, b in zip(rows[1], rows[2]))]
    for i, dot in enumerate(dots):
        if abs(dot) > ORTHO_TOL:
            _fail_semantic("non-rigid-transform",
                           "%s rows are not orthogonal (pair %d)" % (what, i))
    if abs(matrix[12]) > ORTHO_TOL or abs(matrix[13]) > ORTHO_TOL \
            or abs(matrix[14]) > ORTHO_TOL or abs(matrix[15] - 1.0) > ORTHO_TOL:
        _fail_semantic("non-rigid-transform",
                       "%s bottom row must be [0,0,0,1]" % what)


def _check_params(fid, kind, params):
    if not isinstance(params, dict):
        _fail_schema("params-not-object", "%s.parameters must be an object" % fid)
    if kind == "box":
        if set(params) != BOX_PARAMS:
            _fail_schema("params-keys",
                         "%s box parameters must be exactly %s"
                         % (fid, sorted(BOX_PARAMS)))
        size = params["size_mm"]
        if not isinstance(size, list) or len(size) != 3:
            _fail_schema("box-size-shape", "%s size_mm must be [x,y,z]" % fid)
        for i, value in enumerate(size):
            _check_bounded_dim(value, "%s size_mm[%d]" % (fid, i))
    elif kind == "cylinder":
        if set(params) != CYLINDER_PARAMS:
            _fail_schema("params-keys",
                         "%s cylinder parameters must be exactly %s"
                         % (fid, sorted(CYLINDER_PARAMS)))
        _check_bounded_dim(params["radius_mm"], "%s radius_mm" % fid)
        _check_bounded_dim(params["height_mm"], "%s height_mm" % fid)
    else:  # through_hole
        if set(params) != HOLE_PARAMS:
            _fail_schema("params-keys",
                         "%s through_hole parameters must be exactly %s"
                         % (fid, sorted(HOLE_PARAMS)))
        _check_bounded_dim(params["radius_mm"], "%s radius_mm" % fid)
        _check_bounded_dim(params["depth_mm"], "%s depth_mm" % fid)
        parent = params["parent_feature_id"]
        if not isinstance(parent, str) or not parent or len(parent) > 64:
            _fail_schema("hole-parent-shape",
                         "%s parent_feature_id must be a 1..64 char id" % fid)


def _check_feature(node, index):
    where = "features[%d]" % index
    if not isinstance(node, dict):
        _fail_schema("feature-not-object", "%s must be an object" % where)
    if set(node) != FEATURE_KEYS:
        _fail_schema("feature-keys",
                     "%s must have exactly %s" % (where, sorted(FEATURE_KEYS)))
    fid = node["feature_id"]
    if not isinstance(fid, str) or not FEATURE_ID_RE.match(fid):
        _fail_schema("feature-id-shape",
                     "%s.feature_id violates ^[a-zA-Z][a-zA-Z0-9_-]{0,63}$"
                     % where)
    kind = node["kind"]
    if kind not in KINDS:
        _fail_schema("unknown-kind",
                     "%s kind %r is not in v1 %s (profile extrude, "
                     "blind cuts, slots and lofts are v2 extensions)"
                     % (fid, kind, list(KINDS)))
    _check_params(fid, kind, node["parameters"])
    if node["basis"] not in BASES:
        _fail_schema("unknown-basis",
                     "%s basis %r is not one of %s (map a model suggestion "
                     "to model_assumption + assumptions[] text)"
                     % (fid, node["basis"], list(BASES)))
    evidence = node["evidence_ids"]
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE_IDS:
        _fail_schema("evidence-ids-shape",
                     "%s evidence_ids must be a list of at most %d"
                     % (fid, MAX_EVIDENCE_IDS))
    for eid in evidence:
        if not isinstance(eid, str) or not UUID_RE.match(eid):
            _fail_schema("evidence-id-shape",
                         "%s evidence id %r is not a UUID" % (fid, eid))
    unc = node["uncertainty_mm"]
    if unc is not None:
        _check_finite(unc, "%s uncertainty_mm" % fid)
        if unc < 0:
            _fail_schema("negative-uncertainty",
                         "%s uncertainty_mm must be >= 0" % fid)
    assumptions = node["assumptions"]
    if not isinstance(assumptions, list) or len(assumptions) > MAX_ASSUMPTIONS:
        _fail_schema("assumptions-shape",
                     "%s assumptions must be a list of at most %d"
                     % (fid, MAX_ASSUMPTIONS))
    for text in assumptions:
        if not isinstance(text, str) or len(text) > MAX_ASSUMPTION_LEN:
            _fail_schema("assumption-shape",
                         "%s assumption entries must be strings <= %d chars"
                         % (fid, MAX_ASSUMPTION_LEN))
    matrix = _check_transform(node["T_CAD_feature"], "%s.T_CAD_feature" % fid)
    _check_proper_rotation(matrix, "%s.T_CAD_feature" % fid)


def _check_semantic_links(features):
    by_id = {}
    for feat in features:
        fid = feat["feature_id"]
        if fid in by_id:
            _fail_semantic("duplicate-feature-id",
                           "duplicate feature_id %r" % fid)
        by_id[fid] = feat
    for feat in features:
        if feat["kind"] != "through_hole":
            continue
        fid = feat["feature_id"]
        parent = feat["parameters"]["parent_feature_id"]
        if parent not in by_id:
            _fail_semantic("dangling-parent",
                           "%s parent %r does not exist" % (fid, parent))
        target = by_id[parent]
        if target["kind"] == "through_hole":
            _fail_semantic("hole-on-hole",
                           "%s parent %r is a hole, not a solid" % (fid, parent))
        if target["kind"] == "box":
            thick = target["parameters"]["size_mm"][2]
        else:
            thick = target["parameters"]["height_mm"]
        if abs(feat["parameters"]["depth_mm"] - thick) > 1e-9:
            _fail_semantic("depth-mismatch",
                           "%s depth_mm must equal parent %r thickness %g"
                           % (fid, parent, thick))
    # Generic acyclicity over parent links (v1 parents are always solids,
    # so a cycle is unreachable -- but the check stays for v2 growth).
    for feat in features:
        seen = {feat["feature_id"]}
        node = feat
        while node["kind"] == "through_hole":
            node = by_id[node["parameters"]["parent_feature_id"]]
            if node["feature_id"] in seen:
                _fail_semantic("dependency-cycle",
                               "dependency cycle through %r"
                               % node["feature_id"])
            seen.add(node["feature_id"])


def validate_model(model):
    """Validate a parsed cad-feature-model-v1 document.

    Returns {"ok": True, "features": [...], "solids": [...]} on success,
    else {"ok": False, "reject": ..., "code": ..., "error": ...}.
    """
    try:
        if not isinstance(model, dict):
            _fail_schema("model-not-object", "model must be an object")
        if set(model) != MODEL_KEYS:
            _fail_schema("model-keys",
                         "model must have exactly %s" % sorted(MODEL_KEYS))
        if model["schema"] != SCHEMA_ID:
            _fail_schema("schema-id",
                         "schema must be %r" % SCHEMA_ID)
        if not isinstance(model["model_id"], str) \
                or not UUID_RE.match(model["model_id"]):
            _fail_schema("model-id-shape", "model_id must be a UUID")
        rev = model["revision"]
        if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
            _fail_schema("revision-shape", "revision must be an int >= 1")
        parent = model["parent_model_sha256"]
        if parent is not None and \
                (not isinstance(parent, str) or not SHA_RE.match(parent)):
            _fail_schema("parent-sha-shape",
                         "parent_model_sha256 must be null or a sha256 hex")
        if not isinstance(model["dataset_sha256"], str) \
                or not SHA_RE.match(model["dataset_sha256"]):
            _fail_schema("dataset-sha-shape",
                         "dataset_sha256 must be a sha256 hex string")
        if model["units"] != UNITS:
            _fail_schema("units", "units must be %r" % UNITS)
        adapter = _check_transform(model["T_CAD_B"], "T_CAD_B")
        _check_proper_rotation(adapter, "T_CAD_B")
        features = model["features"]
        if not isinstance(features, list) \
                or not (1 <= len(features) <= MAX_FEATURES):
            _fail_schema("features-shape",
                         "features must be a list of 1..%d" % MAX_FEATURES)
        for index, feat in enumerate(features):
            _check_feature(feat, index)
        _check_semantic_links(features)
    except Reject as rejected:
        return rejected.as_dict()
    solids = [f["feature_id"] for f in features
              if f["kind"] in ("box", "cylinder")]
    return {"ok": True, "features": [f["feature_id"] for f in features],
            "solids": solids}
