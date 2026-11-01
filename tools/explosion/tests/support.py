"""Synthetic drawing-proof job folders for the explosion tests (no image, no network, no real session)."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

EXPLOSION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPLOSION_ROOT))

SHAPES = {  # name -> outline polygon in the drawing's mm
    "rect": [(5, 5), (65, 5), (65, 45), (5, 45)],
    "tri": [(10, 8), (90, 8), (50, 58)],
    "hex": [(20, 5), (50, 5), (65, 25), (50, 45), (20, 45), (5, 25)],
}


def drawing_svg(points, width: float = 100.0, height: float = 70.0, units: str = "mm") -> str:
    d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in points) + " Z"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}{units}" height="{height}{units}" viewBox="0 0 {width} {height}">'
            f'<rect width="{width}" height="{height}" fill="#ffffff"/><g id="observed_outline"><path data-line-role="outline" d="{d}" '
            f'fill="#e9eaec" stroke="#222"/></g><g id="observed_regions"></g><g id="opening_candidates" display="none"></g></svg>')


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SessionCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.session = Path(tmp.name) / "s1"
        (self.session / "drawings" / "jobs").mkdir(parents=True)

    def job(self, name: str, shape: str = "rect", part: str = "part", view_side: str = "interior", state: str = "done",
            binding: str | None = None, svg: str | None = None, rectified: str = "chosen_rectified.png") -> Path:
        path = self.session / "drawings" / "jobs" / name
        path.mkdir(parents=True)
        result = {"status": "NEEDS_CONFIRMATION", "spec": {"part": part, "scope": "assembly", "view_side": view_side}}
        (path / "progress.json").write_text(json.dumps({"state": state, "result": result}), encoding="utf-8")
        (path / "drawing.svg").write_text(drawing_svg(SHAPES[shape]) if svg is None else svg, encoding="utf-8")
        (path / "overlay.jpg").write_bytes(b"\xff\xd8overlay-" + name.encode())
        (path / rectified).write_bytes(b"\x89PNGrectified-" + name.encode())
        if binding:
            self.bind(name, binding)
        return path

    def bind(self, name: str, decision: str, drawing_sha: str | None = None) -> None:
        path = self.session / "drawings" / "jobs" / name
        record = {"schema": "anastylos-drawing-binding@1", "decision": decision,
                  "drawing_sha256": drawing_sha or sha(path / "drawing.svg"), "memory_written": False}
        (path / "binding.json").write_text(json.dumps(record), encoding="utf-8")

    def spec(self, *layers: dict) -> dict:
        doc = {"schema": "anastylos-explosion-layout@1", "layers": list(layers)}
        (self.session / "drawings" / "explosion.json").write_text(json.dumps(doc), encoding="utf-8")
        return doc

    @staticmethod
    def layer(job: str, label: str | None = None, view_side: str | None = "interior", flip: bool = False, confirmed: bool = True) -> dict:
        return {"job": job, "label": label or job, "view_side": view_side, "flip_horizontal": flip, "confirmed_by_operator": confirmed}

    def three_jobs(self) -> None:
        self.job("front-1", "rect", part="front shell")
        self.job("pcb-1", "hex", part="pcb")
        self.job("back-1", "tri", part="back shell", view_side="exterior")
