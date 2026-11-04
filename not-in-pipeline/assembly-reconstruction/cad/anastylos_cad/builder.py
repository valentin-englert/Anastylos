"""Allowlisted CadQuery kernel calls for validated v1 features.

Worker venv only: importing this module requires cadquery. It exposes
exactly three operations (box, cylinder, through-hole cut) plus STEP
import and measurement helpers. There is no generic eval, no import of
caller-supplied module names, and no path handling here -- the worker
owns files.
"""

import cadquery as cq
from OCP.gp import gp_Trsf
from OCP.TopLoc import TopLoc_Location

# Hole cutting tool overshoot (mm) past each face for a clean boolean.
TOOL_OVERSHOOT_MM = 1.0


def _location(matrix):
    """Row-major 4x4 feature->CAD matrix to a CadQuery location."""
    trsf = gp_Trsf()
    trsf.SetValues(matrix[0], matrix[1], matrix[2], matrix[3],
                   matrix[4], matrix[5], matrix[6], matrix[7],
                   matrix[8], matrix[9], matrix[10], matrix[11])
    return cq.Location(TopLoc_Location(trsf))


def _located(shape, matrix):
    return shape.located(_location(matrix))


def build_solids(features):
    """Build each solid feature in the CAD frame; cut holes into parents.

    `features` are validated v1 feature dicts. Returns {feature_id: Solid}
    for box/cylinder features only; through_hole entries are consumed by
    their parent cut. Raises ValueError if a boolean yields no solid.
    """
    solids = {}
    for feat in features:
        kind = feat["kind"]
        params = feat["parameters"]
        frame = feat["T_CAD_feature"]["matrix"]
        if kind == "box":
            sx, sy, sz = params["size_mm"]
            solids[feat["feature_id"]] = _located(
                cq.Solid.makeBox(sx, sy, sz), frame)
        elif kind == "cylinder":
            solids[feat["feature_id"]] = _located(
                cq.Solid.makeCylinder(params["radius_mm"],
                                      params["height_mm"]), frame)
    for feat in features:
        if feat["kind"] != "through_hole":
            continue
        params = feat["parameters"]
        parent_id = params["parent_feature_id"]
        radius = params["radius_mm"]
        depth = params["depth_mm"]
        tool = cq.Solid.makeCylinder(
            radius, depth + 2.0 * TOOL_OVERSHOOT_MM,
            cq.Vector(0, 0, -TOOL_OVERSHOOT_MM), cq.Vector(0, 0, 1))
        tool = _located(tool, feat["T_CAD_feature"]["matrix"])
        cut = solids[parent_id].cut(tool)
        inners = cut.Solids()
        if len(inners) != 1:
            raise ValueError(
                "hole %r cut did not leave exactly one solid (got %d)"
                % (feat["feature_id"], len(inners)))
        solids[parent_id] = inners[0]
    return solids


def solid_report(solid):
    """Plain-data validity/volume/bounds report for one solid."""
    bbox = solid.BoundingBox()
    return {
        "is_valid": bool(solid.isValid()),
        "volume": float(solid.Volume()),
        "bbox_mm": [float(bbox.xmin), float(bbox.ymin), float(bbox.zmin),
                    float(bbox.xmax), float(bbox.ymax), float(bbox.zmax)],
        "bbox_size": [float(bbox.xmax - bbox.xmin),
                      float(bbox.ymax - bbox.ymin),
                      float(bbox.zmax - bbox.zmin)],
    }


def tessellate(solid, tolerance=0.5):
    """Tessellation as plain lists for the stdlib-only preview writer."""
    verts, tris = solid.tessellate(tolerance)
    return ([(float(v.x), float(v.y), float(v.z)) for v in verts],
            [(int(t[0]), int(t[1]), int(t[2])) for t in tris])


def export_step(solid, path):
    cq.exporters.export(cq.Workplane(obj=solid), str(path))


def import_step_solids(path):
    """Fresh-process STEP import; returns a list of Solids."""
    shape = cq.importers.importStep(str(path))
    if isinstance(shape, cq.Solid):
        return [shape]
    vals = shape.solids().vals()
    return list(vals)


def versions():
    from importlib.metadata import version as dist_version
    import sys
    try:
        ocp = dist_version("cadquery-ocp")
    except Exception:
        ocp = "unknown"
    return {"python": sys.version.split()[0],
            "cadquery": cq.__version__,
            "cadquery_ocp": ocp}
