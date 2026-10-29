"""Orthographic SVG wireframe projected from tessellation data.

Standard library only: the caller supplies plain vertex/triangle lists
(taken from the reimported solid) and the STEP SHA-256, which the SVG
cites in a comment. A projection is labelled a projection; it never
claims to be a shaded render.
"""

SVG_WIDTH = 400
SVG_HEIGHT = 400
SVG_MARGIN = 10.0


def project_xy(vertices):
    """Drop z; returns list of (x, y)."""
    return [(x, y) for x, y, _z in vertices]


def _edges(triangles):
    edges = set()
    for a, b, c in triangles:
        for u, v in ((a, b), (b, c), (c, a)):
            edges.add((min(u, v), max(u, v)))
    return sorted(edges)


def render_wireframe_svg(vertices_xy, triangles, model_sha256,
                         label="XY projection of tessellated solid"):
    if not vertices_xy:
        raise ValueError("no vertices to project")
    xs = [p[0] for p in vertices_xy]
    ys = [p[1] for p in vertices_xy]
    lo_x, hi_x = min(xs), max(xs)
    lo_y, hi_y = min(ys), max(ys)
    span = max(hi_x - lo_x, hi_y - lo_y, 1e-9)
    scale = (min(SVG_WIDTH, SVG_HEIGHT) - 2.0 * SVG_MARGIN) / span
    ox = SVG_MARGIN - lo_x * scale
    oy = SVG_MARGIN + hi_y * scale
    lines = []
    for u, v in _edges(triangles):
        x1, y1 = ox + vertices_xy[u][0] * scale, oy - vertices_xy[u][1] * scale
        x2, y2 = ox + vertices_xy[v][0] * scale, oy - vertices_xy[v][1] * scale
        lines.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>'
                     % (x1, y1, x2, y2))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">\n'
            '<!-- %s; model sha256:%s -->\n'
            '<g stroke="black" stroke-width="0.5" fill="none">\n%s\n</g>\n</svg>\n'
            % (SVG_WIDTH, SVG_HEIGHT, label, model_sha256, "\n".join(lines)))
