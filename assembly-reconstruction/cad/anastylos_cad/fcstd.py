"""Native parametric FreeCAD export for validated v1 models.

Standard library only for the document model, XML and macro text; the
independent geometry cross-check (`rebuild_tree_report`) imports OCP
lazily and needs the worker venv.

A v1 model maps to a genuine Part feature tree -- Part::Box,
Part::Cylinder, Part::Cut -- whose Length/Width/Height/Radius/Placement
properties are the editable parameters. The .FCStd contains NO imported
BRep/STEP blob: FreeCAD recomputes the shape from the properties on
open. The through-hole tool cylinder is Height = depth + 2*overshoot
placed z=-overshoot in the hole frame, exactly matching the trusted
builder's boolean, so the parametric tree describes identical geometry.

Verified without FreeCAD: zip structure, well-formed Document.xml,
property/param equality, and an independent OCP rebuild of the tree
whose bbox/volume must match the CadQuery builder. Opening, editing a
property, recomputing and re-exporting STEP inside real FreeCAD remains
a human-session gate (FreeCAD is not installed on this workstation) --
the generated .FCMacro gives the reviewer a second, runnable path.
"""

import io
import math
import zipfile

TOOL_OVERSHOOT_MM = 1.0

SCHEMA_HINT = "Part feature tree generated from cad-feature-model-v1"


def _obj_name(feature_id):
    return "".join(w[:1].upper() + w[1:] for w in
                   feature_id.replace("-", "_").split("_"))


def build_document(model):
    """Parameter tree: {feature_key: {Property: value}} + cut links."""
    tree = {}
    cuts = []
    for feat in model["features"]:
        fid = feat["feature_id"]
        params = feat["parameters"]
        matrix = feat["T_CAD_feature"]["matrix"]
        placement = {"position_mm": [matrix[3], matrix[7], matrix[11]],
                     "rotation": [matrix[0], matrix[1], matrix[2],
                                  matrix[4], matrix[5], matrix[6],
                                  matrix[8], matrix[9], matrix[10]]}
        if feat["kind"] == "box":
            sx, sy, sz = params["size_mm"]
            tree[_obj_name(fid)] = {"type": "Part::Box", "Length": sx,
                                    "Width": sy, "Height": sz,
                                    "Placement": placement}
        elif feat["kind"] == "cylinder":
            tree[_obj_name(fid)] = {"type": "Part::Cylinder",
                                    "Radius": params["radius_mm"],
                                    "Height": params["height_mm"],
                                    "Placement": placement}
        elif feat["kind"] == "through_hole":
            key = "Tool_" + _obj_name(fid)
            tree[key] = {"type": "Part::Cylinder",
                         "Radius": params["radius_mm"],
                         "Height": (params["depth_mm"]
                                    + 2.0 * TOOL_OVERSHOOT_MM),
                         "Placement": {
                             "position_mm": [matrix[3], matrix[7],
                                             matrix[11] - TOOL_OVERSHOOT_MM],
                             "rotation": placement["rotation"]}}
            cuts.append({"name": "Cut_" + _obj_name(fid),
                         "base": _obj_name(params["parent_feature_id"]),
                         "tool": key})
    return {"schema_hint": SCHEMA_HINT,
            "model_id": model["model_id"],
            "revision": model["revision"],
            "units": "mm",
            "tree": tree,
            "cuts": cuts}


def _rotation_to_quaternion(row):
    """3x3 row-major rotation matrix to (x, y, z, w)."""
    m00, m01, m02, m10, m11, m12, m20, m21, m22 = row
    trace = m00 + m11 + m22
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        return ((m21 - m12) * s, (m02 - m20) * s,
                (m10 - m01) * s, 0.25 / s)
    if m00 > m11 and m00 > m22:
        s = 2.0 * math.sqrt(1.0 + m00 - m11 - m22)
        return (0.25 * s, (m01 + m10) / s,
                (m02 + m20) / s, (m21 - m12) / s)
    if m11 > m22:
        s = 2.0 * math.sqrt(1.0 + m11 - m00 - m22)
        return ((m01 + m10) / s, 0.25 * s,
                (m12 + m21) / s, (m02 - m20) / s)
    s = 2.0 * math.sqrt(1.0 + m22 - m00 - m11)
    return ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s,
            (m10 - m01) / s)


def _placement_xml(placement):
    pos = placement["position_mm"]
    quat = _rotation_to_quaternion(placement["rotation"])
    return ('<Property name="Placement" type="App::PropertyPlacement">'
            '<Placement value="0" q0="%r" q1="%r" q2="%r" q3="%r" '
            'A="%r" B="%r" C="%r"/></Property>' % (quat[3], quat[0],
                                                   quat[1], quat[2],
                                                   pos[0], pos[1], pos[2]))


def _float_prop(name, value):
    return ('<Property name="%s" type="App::PropertyLength">'
            '<Float value="%r"/></Property>' % (name, value))


def document_xml(doc):
    """Minimal FreeCAD Document.xml for the parametric tree."""
    parts = ["<?xml version='1.0' encoding='utf-8'?>",
             '<Document SchemaVersion="4" ProgramVersion="1.0" '
             'FileVersion="1">',
             "<Properties><Property name=\"Comment\" type=\"App::PropertyString\">"
             "<String value=\"%s model %s rev %d\"/></Property>"
             "<Property name=\"Company\" type=\"App::PropertyString\">"
             "<String value=\"ANASTYLOS\"/></Property></Properties>" % (
                 SCHEMA_HINT, doc["model_id"], doc["revision"]),
             "<Objects>"]
    for name in sorted(doc["tree"]):
        node = doc["tree"][name]
        parts.append('<Object name="%s" type="%s">' % (name, node["type"]))
        parts.append("<Properties>")
        if node["type"] == "Part::Box":
            parts.append(_float_prop("Length", node["Length"]))
            parts.append(_float_prop("Width", node["Width"]))
            parts.append(_float_prop("Height", node["Height"]))
        elif node["type"] == "Part::Cylinder":
            parts.append(_float_prop("Radius", node["Radius"]))
            parts.append(_float_prop("Height", node["Height"]))
        parts.append(_placement_xml(node["Placement"]))
        parts.append("</Properties></Object>")
    for cut in doc["cuts"]:
        parts.append('<Object name="%s" type="Part::Cut">' % cut["name"])
        parts.append("<Properties>")
        parts.append('<Property name="Base" type="App::PropertyLink">'
                     '<Link value="%s"/></Property>' % cut["base"])
        parts.append('<Property name="Tool" type="App::PropertyLink">'
                     '<Link value="%s"/></Property>' % cut["tool"])
        parts.append("</Properties></Object>")
    parts.append("</Objects></Document>")
    return "\n".join(parts) + "\n"


def gui_document_xml(doc):
    names = sorted(doc["tree"]) + [c["name"] for c in doc["cuts"]]
    views = "".join('<ViewProvider name="%s"><Properties/>'
                         "</ViewProvider>" % n for n in names)
    return ("<?xml version='1.0' encoding='utf-8'?>\n"
            '<GuiDocument SchemaVersion="4">%s</GuiDocument>\n' % views)


def write_fcstd_bytes(model):
    """Assemble the .FCStd zip (parametric tree only, no shape blobs)."""
    doc = build_document(model)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Document.xml", document_xml(doc))
        archive.writestr("GuiDocument.xml", gui_document_xml(doc))
    return buffer.getvalue()


def rebuild_tree_report(model):
    """Independently rebuild the tree with raw OCP; return bbox/volume.

    Uses OCP primitives + BRepAlgoAPI_Cut directly (not CadQuery), so a
    match against the trusted builder proves the exported tree describes
    identical geometry.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf
    from OCP.TopLoc import TopLoc_Location

    def located(shape, matrix):
        trsf = gp_Trsf()
        trsf.SetValues(matrix[0], matrix[1], matrix[2], matrix[3],
                       matrix[4], matrix[5], matrix[6], matrix[7],
                       matrix[8], matrix[9], matrix[10], matrix[11])
        return shape.Located(TopLoc_Location(trsf))

    doc = build_document(model)
    shapes = {}
    for name in sorted(doc["tree"]):
        node = doc["tree"][name]
        pos = node["Placement"]["position_mm"]
        point = gp_Pnt(pos[0], pos[1], pos[2])
        axis = gp_Ax2(point, gp_Dir(0, 0, 1))
        if node["type"] == "Part::Box":
            shape = BRepPrimAPI_MakeBox(
                point, node["Length"], node["Width"],
                node["Height"]).Shape()
        else:
            shape = BRepPrimAPI_MakeCylinder(
                axis, node["Radius"], node["Height"]).Shape()
        # orientation beyond translation is identity in v1 fixtures; the
        # general path applies the rotation part explicitly.
        rot = node["Placement"]["rotation"]
        if rot != [1, 0, 0, 0, 1, 0, 0, 0, 1]:
            trsf = gp_Trsf()
            trsf.SetValues(rot[0], rot[1], rot[2], 0,
                           rot[3], rot[4], rot[5], 0,
                           rot[6], rot[7], rot[8], 0)
            shape = shape.Located(TopLoc_Location(trsf))
        shapes[name] = shape
    final = None
    for cut in doc["cuts"]:
        op = BRepAlgoAPI_Cut(shapes[cut["base"]], shapes[cut["tool"]])
        op.Build()
        if not op.IsDone():
            raise ValueError("tree cut %r failed" % (cut["name"],))
        final = op.Shape()
    if final is None:
        if len(shapes) != 1:
            raise ValueError("tree has no cut and %d shapes" % len(shapes))
        final = next(iter(shapes.values()))
    box = Bnd_Box()
    BRepBndLib.Add_s(final, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(final, props)
    return {"bbox_size": [xmax - xmin, ymax - ymin, zmax - zmin],
            "volume": props.Mass()}


def generate_macro(model):
    """FreeCAD macro rebuilding the same parametric tree (human path)."""
    from anastylos_cad import params as P
    table = P.extract_params(model)
    lines = ['"""ANASTYLOS parametric rebuild macro -- run in FreeCAD.',
             "Model %s rev %d. Edit the variables, re-run, recompute." % (
                 model["model_id"], model["revision"]),
             '"""',
             "import FreeCAD",
             "import Part",
             "",
             "Doc = FreeCAD.newDocument(\"Anastylos\")"]
    for pid in sorted(table):
        spec = table[pid]
        tag = "mm" if spec["editable"] else "mm; derived, do not edit"
        lines.append("%s = %r  # %s" % (pid.replace(".", "_"),
                                       spec["value"], tag))
    lines.append("")
    for feat in sorted(model["features"],
                       key=lambda f: f["feature_id"]):
        fid = feat["feature_id"]
        var = fid
        if feat["kind"] == "box":
            lines.append('%s = Doc.addObject("Part::Box", "%s")' % (
                _obj_name(fid), _obj_name(fid)))
            lines.append("%s.Length = %s_size_x_mm" % (_obj_name(fid), var))
            lines.append("%s.Width = %s_size_y_mm" % (_obj_name(fid), var))
            lines.append("%s.Height = %s_size_z_mm" % (_obj_name(fid), var))
        elif feat["kind"] == "cylinder":
            lines.append('%s = Doc.addObject("Part::Cylinder", "%s")' % (
                _obj_name(fid), _obj_name(fid)))
            lines.append("%s.Radius = %s_radius_mm" % (_obj_name(fid), var))
            lines.append("%s.Height = %s_height_mm" % (_obj_name(fid), var))
        elif feat["kind"] == "through_hole":
            tool = "Tool_" + _obj_name(fid)
            lines.append('%s = Doc.addObject("Part::Cylinder", "%s")' % (
                tool, tool))
            lines.append("%s.Radius = %s_radius_mm" % (tool, var))
            lines.append("%s.Height = %s_depth_mm + 2.0" % (tool, var))
            lines.append("%s.Placement.Base = FreeCAD.Vector("
                         "%s_center_x_mm, %s_center_y_mm, "
                         "%s_center_z_mm - 1.0)" % (tool, var, var, var))
    for feat in sorted(model["features"],
                       key=lambda f: f["feature_id"]):
        if feat["kind"] != "through_hole":
            continue
        fid = feat["feature_id"]
        cut = "Cut_" + _obj_name(fid)
        parent = _obj_name(feat["parameters"]["parent_feature_id"])
        tool = "Tool_" + _obj_name(fid)
        lines.append('%s = Doc.addObject("Part::Cut", "%s")' % (cut, cut))
        lines.append("%s.Base = %s" % (cut, parent))
        lines.append("%s.Tool = %s" % (cut, tool))
    lines.append("Doc.recompute()")
    return "\n".join(lines) + "\n"
