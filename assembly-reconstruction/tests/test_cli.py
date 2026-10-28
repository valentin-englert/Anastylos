"""CLI tests: one reproducible build command, end to end.

Run from the assembly-reconstruction directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "assembly-memory"))
sys.path.insert(0, str(ROOT.parent / "cable-routing"))

from anastylos_reconstruction.cli import main  # noqa: E402
from anastylos_reconstruction.schema import validate_state  # noqa: E402

FIX = ROOT / "fixtures" / "synthetic"


def _run(argv):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(argv)
    return code, stdout.getvalue()


class CliBuild(unittest.TestCase):
    def test_build_writes_state_and_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "out")
            code, summary_text = _run([
                "build", str(FIX / "memory.jsonl"),
                "--results-dir", str(FIX / "results"),
                "--graph", str(FIX / "assembly_graph.json"),
                "--out", out])
            self.assertEqual(code, 0)
            summary = json.loads(summary_text)
            self.assertEqual(summary["observations"], 5)
            self.assertEqual(summary["confirmed"], 1)
            self.assertEqual(summary["candidates"], 2)
            self.assertEqual(summary["embedded_images"], 0)
            state_path = Path(out) / "assembly_state.json"
            svg_path = Path(out) / "exploded.svg"
            self.assertTrue(state_path.is_file())
            self.assertTrue(svg_path.is_file())
            validate_state(json.loads(state_path.read_text(encoding="utf-8")))
            ET.parse(str(svg_path))

    def test_reported_sha256_matches_written_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code, summary_text = _run(["build", str(FIX / "memory.jsonl"), "--out", str(out)])
            self.assertEqual(code, 0)
            summary = json.loads(summary_text)
            for name, key in (("assembly_state.json", "state_sha256"), ("exploded.svg", "svg_sha256")):
                data = (out / name).read_bytes()
                self.assertNotIn(b"\r\n", data)
                self.assertEqual(hashlib.sha256(data).hexdigest(), summary[key])

    def test_build_without_optional_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "out")
            code, _ = _run(["build", str(FIX / "memory.jsonl"), "--out", out])
            self.assertEqual(code, 0)
            state = json.loads((Path(out) / "assembly_state.json").read_text(
                encoding="utf-8"))
            # Basename search fails without --results-dir, but the
            # memory-relative search still resolves fixture citations.
            self.assertTrue(state["components"][0]["physical"]["region_resolved"])
            self.assertEqual(state["relationships"]["status"], "unknown")

    def test_build_is_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "a", Path(tmp) / "b"
            argv = ["build", str(FIX / "memory.jsonl"),
                   "--results-dir", str(FIX / "results"),
                   "--graph", str(FIX / "assembly_graph.json")]
            self.assertEqual(_run([*argv, "--out", str(first)])[0], 0)
            self.assertEqual(_run([*argv, "--out", str(second)])[0], 0)
            self.assertEqual(
                (first / "assembly_state.json").read_bytes(),
                (second / "assembly_state.json").read_bytes())

    def test_embed_crops_produces_self_contained_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "out")
            code, summary_text = _run([
                "build", str(FIX / "memory.jsonl"),
                "--results-dir", str(FIX / "results"),
                "--graph", str(FIX / "assembly_graph.json"),
                "--embed-crops", "--out", out])
            self.assertEqual(code, 0)
            summary = json.loads(summary_text)
            self.assertEqual(summary["embedded_images"], 2)
            text = (Path(out) / "exploded.svg").read_text(encoding="utf-8")
            ET.parse(str(Path(out) / "exploded.svg"))
            self.assertIn("data:image/png;base64,", text)
            self.assertNotIn("back_before.png", text)
            self.assertNotIn("back_after.png", text)

    def test_missing_log_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = _run(["build", str(Path(tmp) / "absent.jsonl"),
                            "--out", str(Path(tmp) / "out")])
            self.assertEqual(code, 2)


class LiveGraphIntegration(unittest.TestCase):
    """The real graph builder feeds the projector (test-only coupling)."""

    def test_real_graph_to_state_to_svg(self):
        from anastylos_cable.graph import build_graph
        from anastylos_reconstruction.projector import build_state
        from anastylos_reconstruction.render import render_svg
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = build_graph(
                str(FIX / "memory.jsonl"),
                results_dir=str(FIX / "results"),
                confirmations_path=str(FIX / "confirmations.json"))
            graph_path = root / "live_graph.json"
            graph_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")
            state = build_state(str(FIX / "memory.jsonl"),
                                results_dir=str(FIX / "results"),
                                graph_path=str(graph_path))
            validate_state(state)
            self.assertEqual(len(state["relationships"]["confirmed"]), 1)
            ET.fromstring(render_svg(state, svg_path=str(root / "x.svg")))


if __name__ == "__main__":
    unittest.main()
