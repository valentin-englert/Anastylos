"""Synthetic scenes and exact silhouette rendering for mesh experiments.

Produces Camera+Mask fixture sets with provenance "synthetic" for the
visual-hull pipeline (``carve`` -> ``surface`` -> ``export``). All
geometry is board mm in the core convention (see
``anastylos_mesh.geometry``): x right, y down, z into the board.

Convention note: per the synthesis task spec, synthetic objects occupy
z > 0 above the board plane z = 0 (i.e. on the into-the-board side of
the plane). No board occlusion is modelled, so carving is unaffected;
cameras stay at z < 0 as required by ``Camera.validate`` and look at
the ROI. Poses are rigid T_board_camera (mm, det +1) and pass the
fail-closed ``validate_det1`` checks.

Rendering is an exact vectorized Moeller-Trumbore ray-caster in numpy
(no rtree, no randomness): one ray per pixel center through the pinhole
model, foreground iff any triangle is hit (no backface culling, so
closed soups silhouette correctly from any outside view).

PNG evidence bytes are encoded/decoded with stdlib zlib only, so this
module stays numpy+stdlib like the core.
"""

from __future__ import annotations

import hashlib
import math
import struct
import zlib
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from anastylos_mesh.cameras import Camera
from anastylos_mesh.geometry import MeshCalibrationError, det3
from anastylos_mesh.masks import PNG_MAGIC, Mask

PROVENANCE = "synthetic"
IMAGE_BG = 32
IMAGE_FG = 220

__all__ = [
    "PROVENANCE",
    "Scene",
    "FixtureSet",
    "SCENE_NAMES",
    "box_triangles",
    "cylinder_triangles",
    "make_scene",
    "camera_ring",
    "raycast_silhouette",
    "render_fixtures",
    "encode_png_gray",
    "decode_png_gray",
]


# ---------------------------------------------------------------------------
# Minimal deterministic grayscale PNG codec (stdlib only).
# ---------------------------------------------------------------------------

def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + ctype
        + payload
        + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
    )


def encode_png_gray(gray: np.ndarray) -> bytes:
    """Encode a (H, W) uint8 array as a deterministic 8-bit gray PNG."""
    arr = np.ascontiguousarray(gray, dtype=np.uint8)
    if arr.ndim != 2 or arr.size == 0:
        raise MeshCalibrationError("PNG_ENCODE_BAD_SHAPE")
    h, w = arr.shape
    raw = b"".join(b"\x00" + arr[r].tobytes() for r in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    return (
        PNG_MAGIC
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def decode_png_gray(png: bytes) -> np.ndarray:
    """Decode a non-interlaced 8-bit gray PNG written by encode_png_gray.

    Accepts only color type 0, bit depth 8 (what the encoder emits);
    raises MeshCalibrationError otherwise.
    """
    data = bytes(png)
    if not data.startswith(PNG_MAGIC):
        raise MeshCalibrationError("PNG_DECODE_BAD_MAGIC")
    pos = len(PNG_MAGIC)
    width = height = 0
    idat = bytearray()
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 8 + length + 4
        if ctype == b"IHDR":
            width, height, depth, ctype_id, comp, filt, inter = struct.unpack(
                ">IIBBBBB", payload
            )
            if (depth, ctype_id, comp, filt, inter) != (8, 0, 0, 0, 0):
                raise MeshCalibrationError("PNG_DECODE_UNSUPPORTED_FORMAT")
        elif ctype == b"IDAT":
            idat += payload
        elif ctype == b"IEND":
            break
    if width <= 0 or height <= 0 or not idat:
        raise MeshCalibrationError("PNG_DECODE_TRUNCATED")
    raw = zlib.decompress(bytes(idat))
    stride = width + 1
    if len(raw) != stride * height:
        raise MeshCalibrationError("PNG_DECODE_SIZE_MISMATCH")
    out = np.empty((height, width), dtype=np.uint8)
    for r in range(height):
        f = raw[r * stride]
        if f != 0:
            # The encoder only emits filter 0; anything else is foreign.
            raise MeshCalibrationError("PNG_DECODE_UNSUPPORTED_FILTER")
        out[r] = np.frombuffer(
            raw[r * stride + 1:(r + 1) * stride], dtype=np.uint8
        )
    return out


# ---------------------------------------------------------------------------
# Triangle-soup scene builders (all mm, object z > 0, base on z = 0).
# ---------------------------------------------------------------------------

def box_triangles(
    lo: tuple[float, float, float], hi: tuple[float, float, float]
) -> tuple[np.ndarray, np.ndarray]:
    """8-vertex, 12-face axis-aligned box soup (outward winding)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = np.array(
        [
            [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
            [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
        ],
        dtype=np.float64,
    )
    f = np.array(
        [
            [0, 2, 1], [0, 3, 2],  # bottom (z0)
            [4, 5, 6], [4, 6, 7],  # top (z1)
            [0, 1, 5], [0, 5, 4],  # front (y0)
            [2, 3, 7], [2, 7, 6],  # back (y1)
            [1, 2, 6], [1, 6, 5],  # right (x1)
            [3, 0, 4], [3, 4, 7],  # left (x0)
        ],
        dtype=np.int64,
    )
    return v, f


def cylinder_triangles(
    radius: float, height: float, segments: int = 64
) -> tuple[np.ndarray, np.ndarray]:
    """Z-axis prism soup approximating a cylinder (base z=0, centered x/y)."""
    if not (radius > 0 and height > 0 and segments >= 8):
        raise MeshCalibrationError("BAD_CYLINDER_PARAMS")
    th = np.linspace(0.0, 2.0 * math.pi, segments, endpoint=False)
    ring_x = radius * np.cos(th)
    ring_y = radius * np.sin(th)
    bottom = np.stack([ring_x, ring_y, np.zeros(segments)], axis=1)
    top = np.stack([ring_x, ring_y, np.full(segments, height)], axis=1)
    verts = np.vstack([bottom, top, [[0.0, 0.0, 0.0], [0.0, 0.0, height]]])
    cb, ct = 2 * segments, 2 * segments + 1
    faces = []
    for i in range(segments):
        j = (i + 1) % segments
        faces.append([i, segments + i, segments + j])
        faces.append([i, segments + j, j])
        faces.append([cb, j, i])
        faces.append([ct, segments + i, segments + j])
    return verts.astype(np.float64), np.array(faces, dtype=np.int64)


def _tray_triangles(
    w: float, l: float, t: float, wall: float, floor: float
) -> tuple[np.ndarray, np.ndarray]:
    """Open-top box-with-cavity soup: outer shell + inner walls + rim."""
    hw, hl = w / 2.0, l / 2.0
    iw, il = hw - wall, hl - wall
    pts: list[list[float]] = []

    def quad(a: list[float], b: list[float], c: list[float], d: list[float]) -> list[int]:
        base = len(pts)
        pts.extend([a, b, c, d])
        return [base, base + 1, base + 2, base, base + 2, base + 3]

    faces: list[list[int]] = []
    # Outer bottom (normal -z) and outer walls.
    faces += [quad([-hw, -hl, 0], [-hw, hl, 0], [hw, hl, 0], [hw, -hl, 0])]
    faces += [quad([-hw, -hl, 0], [hw, -hl, 0], [hw, -hl, t], [-hw, -hl, t])]
    faces += [quad([hw, hl, 0], [-hw, hl, 0], [-hw, hl, t], [hw, hl, t])]
    faces += [quad([hw, -hl, 0], [hw, hl, 0], [hw, hl, t], [hw, -hl, t])]
    faces += [quad([-hw, hl, 0], [-hw, -hl, 0], [-hw, -hl, t], [-hw, hl, t])]
    # Inner cavity floor (normal -z, visible from above through opening).
    faces += [quad([-iw, -il, floor], [iw, -il, floor], [iw, il, floor], [-iw, il, floor])]
    # Inner walls (normals face the cavity void).
    faces += [quad([-iw, -il, t], [iw, -il, t], [iw, -il, floor], [-iw, -il, floor])]
    faces += [quad([iw, il, t], [-iw, il, t], [-iw, il, floor], [iw, il, floor])]
    faces += [quad([iw, -il, t], [iw, il, t], [iw, il, floor], [iw, -il, floor])]
    faces += [quad([-iw, il, t], [-iw, -il, t], [-iw, -il, floor], [-iw, il, floor])]
    # Top rim ring (normal +z): 4 quads joining outer to inner walls.
    faces += [quad([-hw, -hl, t], [-hw, hl, t], [-iw, il, t], [-iw, -il, t])]
    faces += [quad([hw, hl, t], [hw, -hl, t], [iw, -il, t], [iw, il, t])]
    faces += [quad([-iw, -hl, t], [iw, -hl, t], [iw, -il, t], [-iw, -il, t])]
    faces += [quad([iw, hl, t], [-iw, hl, t], [-iw, il, t], [iw, il, t])]
    tris = []
    for q in faces:
        tris.append(q[0:3])
        tris.append(q[3:6])
    return np.array(pts, dtype=np.float64), np.array(tris, dtype=np.int64)


def _box_overlap_volume(
    lo1: tuple[float, ...], hi1: tuple[float, ...],
    lo2: tuple[float, ...], hi2: tuple[float, ...],
) -> float:
    vol = 1.0
    for a0, a1, b0, b1 in zip(lo1, hi1, lo2, hi2):
        vol *= max(0.0, min(a1, b1) - max(a0, b0))
    return vol


def _sample_soup(
    vertices: np.ndarray, faces: np.ndarray, n: int, seed: int
) -> np.ndarray:
    """Area-weighted uniform sampling of triangle-soup surfaces."""
    rng = np.random.default_rng(seed)
    tris = vertices[faces]
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    areas = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)
    total = areas.sum()
    if not np.isfinite(total) or total <= 0:
        raise MeshCalibrationError("EMPTY_SCENE_SURFACE")
    pick = rng.choice(len(tris), size=n, p=areas / total)
    r1 = rng.random(n)
    r2 = rng.random(n)
    s1 = np.sqrt(r1)
    w0 = 1.0 - s1
    w1 = s1 * (1.0 - r2)
    w2 = s1 * r2
    sel = tris[pick]
    return w0[:, None] * sel[:, 0] + w1[:, None] * sel[:, 1] + w2[:, None] * sel[:, 2]


@dataclass(frozen=True)
class Scene:
    """Closed-form synthetic scene: triangle soup + analytic ground truth.

    units is the DECLARED world unit ("mm" or "m"). The pipeline math
    is unit-blind millimetres: a scene declaring "m" keeps identical
    numbers, and the export declared-units gate flags its metric
    claims invalid (this models silent mm/m confusion exactly).
    """

    name: str
    params: dict
    vertices: np.ndarray = field(repr=False)
    faces: np.ndarray = field(repr=False)
    gt_bbox_dims: tuple[float, float, float]
    gt_volume: float
    bounds: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    _sampler: Callable[[int, int], np.ndarray] = field(repr=False)
    units: str = "mm"

    def sample_points(self, n: int, seed: int = 0) -> np.ndarray:
        """n ground-truth surface points (N, 3), deterministic in seed."""
        if n < 1:
            raise MeshCalibrationError("BAD_SAMPLE_COUNT")
        return np.asarray(self._sampler(int(n), int(seed)), dtype=np.float64)


def _bounds_of(vertices: np.ndarray) -> tuple[tuple[float, float], ...]:
    lo = vertices.min(axis=0)
    hi = vertices.max(axis=0)
    return ((float(lo[0]), float(hi[0])), (float(lo[1]), float(hi[1])),
            (float(lo[2]), float(hi[2])))


def _dims_of(bounds: tuple[tuple[float, float], ...]) -> tuple[float, float, float]:
    return (bounds[0][1] - bounds[0][0], bounds[1][1] - bounds[1][0],
            bounds[2][1] - bounds[2][0])


SCENE_NAMES = ("box", "cylinder", "stepped", "cavity")


def make_scene(name: str, units: str = "mm", **params: float) -> Scene:
    """Build one of box|cylinder|stepped|cavity (base on z=0).

    units is a declaration tag only ("mm"|"m"); numbers are unchanged
    and the export gate judges metric validity from the tag.
    """
    if units not in ("mm", "m"):
        raise MeshCalibrationError(f"BAD_SCENE_UNITS:{units}")
    if name == "box":
        w = float(params.get("w", 60.0))
        l = float(params.get("l", 40.0))
        t = float(params.get("t", 30.0))
        if not (w > 0 and l > 0 and t > 0):
            raise MeshCalibrationError("BAD_BOX_PARAMS")
        v, f = box_triangles((-w / 2, -l / 2, 0.0), (w / 2, l / 2, t))
        bounds = _bounds_of(v)
        return Scene(name, {"w": w, "l": l, "t": t}, v, f, (w, l, t), w * l * t,
                     bounds, lambda n, s: _sample_soup(v, f, n, s), units)
    if name == "cylinder":
        r = float(params.get("r", 20.0))
        h = float(params.get("h", 40.0))
        seg = int(params.get("segments", 64))
        v, f = cylinder_triangles(r, h, seg)

        def _cyl_sampler(n: int, seed: int) -> np.ndarray:
            rng = np.random.default_rng(seed)
            side = 2 * math.pi * r * h
            cap = math.pi * r * r
            probs = np.array([side, cap, cap]) / (side + 2 * cap)
            kinds = rng.choice(3, size=n, p=probs)
            out = np.empty((n, 3))
            m = kinds == 0
            th = rng.random(m.sum()) * 2 * math.pi
            out[m, 0] = r * np.cos(th)
            out[m, 1] = r * np.sin(th)
            out[m, 2] = rng.random(m.sum()) * h
            for kind, z in ((1, 0.0), (2, h)):
                m = kinds == kind
                rr = r * np.sqrt(rng.random(m.sum()))
                th = rng.random(m.sum()) * 2 * math.pi
                out[m, 0] = rr * np.cos(th)
                out[m, 1] = rr * np.sin(th)
                out[m, 2] = z
            return out

        return Scene(name, {"r": r, "h": h, "segments": seg}, v, f, (2 * r, 2 * r, h),
                     math.pi * r * r * h, _bounds_of(v), _cyl_sampler, units)
    if name == "stepped":
        w1 = float(params.get("w1", 60.0))
        l1 = float(params.get("l1", 40.0))
        t1 = float(params.get("t1", 20.0))
        w2 = float(params.get("w2", 30.0))
        l2 = float(params.get("l2", 25.0))
        t2 = float(params.get("t2", 20.0))
        ox = float(params.get("ox", 10.0))
        oy = float(params.get("oy", -5.0))
        if not all(v > 0 for v in (w1, l1, t1, w2, l2, t2)):
            raise MeshCalibrationError("BAD_STEPPED_PARAMS")
        lo1 = (-w1 / 2, -l1 / 2, 0.0)
        hi1 = (w1 / 2, l1 / 2, t1)
        lo2 = (ox - w2 / 2, oy - l2 / 2, t1)
        hi2 = (ox + w2 / 2, oy + l2 / 2, t1 + t2)
        v1, f1 = box_triangles(lo1, hi1)
        v2, f2 = box_triangles(lo2, hi2)
        v = np.vstack([v1, v2])
        f = np.vstack([f1, f2 + len(v1)])
        vol = (w1 * l1 * t1 + w2 * l2 * t2
               - _box_overlap_volume(lo1, hi1, lo2, hi2))
        bounds = _bounds_of(v)

        def _stepped_sampler(n: int, seed: int) -> np.ndarray:
            rng = np.random.default_rng(seed)
            got: list[np.ndarray] = []
            need = n
            trial = 0
            while need > 0:
                cand = _sample_soup(v, f, need * 2 + 8, seed * 100003 + trial)
                trial += 1
                inside1 = (
                    (cand[:, 0] > lo1[0]) & (cand[:, 0] < hi1[0])
                    & (cand[:, 1] > lo1[1]) & (cand[:, 1] < hi1[1])
                    & (cand[:, 2] > lo1[2]) & (cand[:, 2] < hi1[2])
                )
                inside2 = (
                    (cand[:, 0] > lo2[0]) & (cand[:, 0] < hi2[0])
                    & (cand[:, 1] > lo2[1]) & (cand[:, 1] < hi2[1])
                    & (cand[:, 2] > lo2[2]) & (cand[:, 2] < hi2[2])
                )
                keep = cand[~(inside1 | inside2)]
                got.append(keep[:need])
                need -= min(len(keep), need)
                if trial > 100:
                    raise MeshCalibrationError("STEPPED_SAMPLER_STALL")
            return np.vstack(got)[:n]

        return Scene(name,
                     {"w1": w1, "l1": l1, "t1": t1, "w2": w2, "l2": l2,
                      "t2": t2, "ox": ox, "oy": oy},
                     v, f, _dims_of(bounds), vol, bounds, _stepped_sampler,
                     units)
    if name == "cavity":
        w = float(params.get("w", 60.0))
        l = float(params.get("l", 40.0))
        t = float(params.get("t", 30.0))
        wall = float(params.get("wall", 5.0))
        floor = float(params.get("floor", 5.0))
        if not (w > 2 * wall and l > 2 * wall and t > floor
                and wall > 0 and floor > 0):
            raise MeshCalibrationError("BAD_CAVITY_PARAMS")
        v, f = _tray_triangles(w, l, t, wall, floor)
        vol = w * l * t - (w - 2 * wall) * (l - 2 * wall) * (t - floor)
        return Scene(name, {"w": w, "l": l, "t": t, "wall": wall, "floor": floor},
                     v, f, (w, l, t), vol, _bounds_of(v),
                     lambda n, s: _sample_soup(v, f, n, s), units)
    raise MeshCalibrationError(f"UNKNOWN_SCENE:{name}")


# ---------------------------------------------------------------------------
# Exact vectorized Moeller-Trumbore ray-caster.
# ---------------------------------------------------------------------------

def raycast_silhouette(
    origins: np.ndarray,
    dirs: np.ndarray,
    vertices: np.ndarray,
    faces: np.ndarray,
    chunk: int = 4096,
) -> np.ndarray:
    """Foreground hit-mask for rays vs a triangle soup (no culling).

    origins/dirs: (R, 3); returns (R,) bool. Chunked over rays so peak
    memory stays ~chunk * ntris * small constant.
    """
    o = np.asarray(origins, dtype=np.float64).reshape(-1, 3)
    d = np.asarray(dirs, dtype=np.float64).reshape(-1, 3)
    tris = np.asarray(vertices, dtype=np.float64)[np.asarray(faces, dtype=np.int64)]
    v0 = tris[:, 0][None, :, :]
    e1 = (tris[:, 1] - tris[:, 0])[None, :, :]
    e2 = (tris[:, 2] - tris[:, 0])[None, :, :]
    eps = 1e-12
    hits = np.zeros(len(o), dtype=np.bool_)
    for s in range(0, len(o), chunk):
        oc = o[s:s + chunk][:, None, :]
        dc = d[s:s + chunk][:, None, :]
        pvec = np.cross(dc, e2)
        det = np.einsum("rtj,rtj->rt", e1, pvec)
        valid = np.abs(det) > eps
        inv = np.zeros_like(det)
        inv[valid] = 1.0 / det[valid]
        tvec = oc - v0
        u = np.einsum("rtj,rtj->rt", tvec, pvec) * inv
        qvec = np.cross(tvec, e1)
        vv = np.einsum("rtj,rtj->rt", dc, qvec) * inv
        tt = np.einsum("rtj,rtj->rt", e2, qvec) * inv
        hit = (valid & (u >= 0.0) & (vv >= 0.0) & (u + vv <= 1.0) & (tt > eps))
        hits[s:s + chunk] = hit.any(axis=1)
    return hits


# ---------------------------------------------------------------------------
# Camera ring + fixture rendering.
# ---------------------------------------------------------------------------

def _look_at_t(center: np.ndarray, target: np.ndarray) -> list[float]:
    """Rigid T_board_camera (row-major mm) with camera +z toward target."""
    zc = target - center
    zn = float(np.linalg.norm(zc))
    if zn == 0:
        raise MeshCalibrationError("LOOK_AT_COINCIDENT")
    zc = zc / zn
    up = np.array([0.0, 0.0, -1.0])  # board -z: out of the board surface
    if abs(float(zc @ up)) > 0.999:
        up = np.array([1.0, 0.0, 0.0])
    xc = np.cross(up, zc)
    xc = xc / float(np.linalg.norm(xc))
    yc = np.cross(zc, xc)
    t = [float(v) for v in center]
    return [
        float(xc[0]), float(yc[0]), float(zc[0]), t[0],
        float(xc[1]), float(yc[1]), float(zc[1]), t[1],
        float(xc[2]), float(yc[2]), float(zc[2]), t[2],
        0.0, 0.0, 0.0, 1.0,
    ]


@dataclass(frozen=True)
class FixtureSet:
    """Rendered synthetic fixtures: validated cameras + masks + PNG bytes."""

    scene: Scene
    cameras: list[Camera] = field(repr=False)
    masks: list[Mask] = field(repr=False)
    image_pngs: list[bytes] = field(repr=False)
    params: dict

    def validate(self) -> "FixtureSet":
        """Fail-closed: every camera and every mask-against-camera check."""
        if len(self.cameras) != len(self.masks) or not self.cameras:
            raise MeshCalibrationError("FIXTURE_COUNT_MISMATCH")
        for cam in self.cameras:
            cam.validate()
        for mask, cam in zip(self.masks, self.cameras):
            mask.validate(cam)
        return self


def camera_ring(
    roi_center: tuple[float, float, float],
    roi_radius: float,
    n_views: int = 8,
    width: int = 320,
    height: int = 240,
    focal_px: float | None = None,
    elevations_deg: tuple[float, float] = (25.0, 45.0),
    azimuth_start_deg: float = 0.0,
    fill: float = 0.8,
    pose_prefix: str = "synthetic",
) -> tuple[list[Camera], list[bytes], dict]:
    """N-view ring: full azimuth spread, alternating 2 elevations.

    Returns (cameras, image_pngs_placeholder, ring_params). Image bytes
    are filled in by render_fixtures (they depend on the silhouette);
    cameras carry provisional image_sha256 of zeros here and are rebuilt
    with real hashes there — use render_fixtures, not this, for fixtures.
    All views share one pose_epoch (pose_prefix): a static capture of a
    static object is a single object-pose epoch.
    """
    if n_views < 2:
        raise MeshCalibrationError("RING_TOO_FEW_VIEWS")
    if roi_radius <= 0:
        raise MeshCalibrationError("RING_BAD_RADIUS")
    f = float(focal_px) if focal_px else 2.5 * max(width, height)
    if not f > 0:
        raise MeshCalibrationError("RING_BAD_FOCAL")
    half_frame = 0.5 * min(width, height)
    distance = roi_radius / max(1e-9, (half_frame / f) * fill)
    el_min = math.radians(min(elevations_deg))
    need = roi_center[2] + 1.0  # keep every center z < 0
    if distance * math.sin(el_min) < need:
        distance = need / max(1e-9, math.sin(el_min))
    k = [f, 0.0, width / 2.0, 0.0, f, height / 2.0, 0.0, 0.0, 1.0]
    if abs(det3([k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7], k[8]])) <= 0:
        raise MeshCalibrationError("RING_BAD_K")
    target = np.array(roi_center, dtype=np.float64)
    cameras = []
    for i in range(n_views):
        az = math.radians(azimuth_start_deg) + 2.0 * math.pi * i / n_views
        el = math.radians(elevations_deg[i % 2])
        off = np.array([
            distance * math.cos(el) * math.cos(az),
            distance * math.cos(el) * math.sin(az),
            -distance * math.sin(el),
        ])
        center = target + off
        t = _look_at_t(center, target)
        cameras.append(Camera(
            K=tuple(k),
            dist=(0.0, 0.0, 0.0, 0.0, 0.0),
            width=width,
            height=height,
            T_board_camera=tuple(t),
            image_sha256="0" * 64,
            pose_epoch=pose_prefix,
            reference_image_size_px=(width, height),
        ))
    params = {
        "n_views": n_views, "width": width, "height": height,
        "focal_px": f, "distance_mm": distance,
        "elevations_deg": list(elevations_deg),
        "azimuth_start_deg": azimuth_start_deg,
        "roi_center": list(roi_center), "roi_radius": roi_radius,
    }
    return cameras, [], params


def render_fixtures(
    scene: Scene,
    n_views: int = 8,
    width: int = 320,
    height: int = 240,
    focal_px: float | None = None,
    elevations_deg: tuple[float, float] = (25.0, 45.0),
    pose_prefix: str = "synthetic",
    azimuth_start_deg: float = 0.0,
) -> FixtureSet:
    """Render silhouette fixtures for a scene (deterministic).

    All views share pose_epoch == pose_prefix (one static epoch); the
    azimuth offset rotates the whole ring for viewpoint diversity.
    """
    lo = np.array([b[0] for b in scene.bounds])
    hi = np.array([b[1] for b in scene.bounds])
    roi_center = tuple((lo + hi) / 2.0)
    roi_radius = float(np.linalg.norm(hi - lo) / 2.0)
    ring, _, params = camera_ring(
        roi_center, roi_radius, n_views, width, height, focal_px,
        elevations_deg, azimuth_start_deg, 0.8, pose_prefix,
    )
    f = params["focal_px"]
    cx, cy = width / 2.0, height / 2.0
    uu, vv = np.meshgrid(np.arange(width) + 0.5, np.arange(height) + 0.5)
    ndc = np.stack([(uu.ravel() - cx) / f, (vv.ravel() - cy) / f,
                    np.ones(width * height)], axis=1)
    cameras: list[Camera] = []
    masks: list[Mask] = []
    image_pngs: list[bytes] = []
    for i, provisional in enumerate(ring):
        t = list(provisional.T_board_camera)
        rot = np.array([[t[0], t[1], t[2]], [t[4], t[5], t[6]],
                        [t[8], t[9], t[10]]])
        center = np.array([t[3], t[7], t[11]])
        dirs = ndc @ rot.T
        dirs = dirs / np.linalg.norm(dirs, axis=1, keepdims=True)
        origins = np.broadcast_to(center, dirs.shape).copy()
        fg = raycast_silhouette(origins, dirs, scene.vertices,
                                scene.faces).reshape(height, width)
        mask_png = encode_png_gray(np.where(fg, 255, 0).astype(np.uint8))
        image_gray = np.where(fg, IMAGE_FG, IMAGE_BG).astype(np.uint8)
        image_png = encode_png_gray(image_gray)
        image_sha = hashlib.sha256(image_png).hexdigest()
        cam = Camera(
            K=provisional.K, dist=provisional.dist, width=width,
            height=height, T_board_camera=provisional.T_board_camera,
            image_sha256=image_sha,
            pose_epoch=provisional.pose_epoch,
            reference_image_size_px=(width, height),
        )
        mask = Mask(
            png_bytes=mask_png,
            sha256=hashlib.sha256(mask_png).hexdigest(),
            image_sha256=image_sha,
            provenance=PROVENANCE,
            width=width, height=height, foreground=fg,
        )
        cam.validate()
        mask.validate(cam)
        cameras.append(cam)
        masks.append(mask)
        image_pngs.append(image_png)
    return FixtureSet(scene, cameras, masks, image_pngs, params).validate()
