"""Build the SYNTHETIC example dataset + capture manifest (no real photos).

Everything produced here is rendered by the existing ANASTYLOS synthetic
renderers or by cv2.putText. It is labelled `synthetic` everywhere and is
NOT an MSI GTX 970 capture. It exists to exercise the MIC-01..05 harness
and the capture-manifest intake.

    set ANASTYLOS_ROOT=C:\\path\\to\\Anastylos      (PowerShell: $env:ANASTYLOS_ROOT=...)
    python examples/build_synthetic_examples.py [--out examples/synthetic_mic]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from anastylos_microscopy import env  # noqa: E402
from anastylos_microscopy.orchestrator import inspect_image  # noqa: E402

CLOCK = "2026-10-29T12:00:00Z"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_panel(text, size=(360, 140), body=(30, 30, 30), ink=(235, 235, 235), scale=1.6):
    import cv2
    import numpy as np

    width, height = size
    image = np.full((height, width, 3), body, np.uint8)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 3)
    cv2.putText(image, text, ((width - tw) // 2, (height + th) // 2), cv2.FONT_HERSHEY_SIMPLEX,
                scale, ink, 3, cv2.LINE_AA)
    return image


def build(out: Path) -> dict:
    import cv2

    root = env.resolve_root()
    env.activate(root)
    from anastylos_fingerprints import synthetic as fp_synth
    from anastylos_resistor import synthetic as res_synth

    images = out / "images"
    images.mkdir(parents=True, exist_ok=True)
    files = {
        "smd_472.png": text_panel("472"),
        "smd_103_heldout.png": text_panel("103"),
        "smd_unmarked.png": text_panel(""),
        "silk_r12_c5.png": text_panel("R12  C5", size=(520, 140), body=(40, 110, 30)),
        "ic_lm358n.png": text_panel("LM358N", size=(480, 140)),
        "fp_ic_enroll.png": fp_synth.render("ic", seed=1),
        "fp_ic_query_rot30.png": fp_synth.render("ic", rotation_deg=30, seed=2),
        "fp_capacitor_negative.png": fp_synth.render("capacitor", seed=3),
        "unknown_uniform.png": text_panel("", body=(128, 128, 128)),
        "unknown_blurred_resistor.png": cv2.GaussianBlur(
            res_synth.render(["brown", "black", "red", "gold"]), (0, 0), 25),
        "unknown_glare.png": res_synth.render(["red", "red", "red", "gold"], glare=True,
                                              brightness=1.9),
    }
    for name, image in files.items():
        cv2.imwrite(str(images / name), image)
    gallery = out / "gallery"
    gallery.mkdir(exist_ok=True)
    # The enrollment inspection record itself is not part of the example set (it would embed
    # absolute box paths); only the resulting gallery record is kept.
    enroll_out = Path(tempfile.mkdtemp(prefix="mic-enroll-"))
    inspect_image(images / "fp_ic_enroll.png", enroll_out, mode="fingerprints",
                  options={"gallery": str(gallery), "fingerprint_action": "enroll",
                           "enroll_id": "synthetic-ic-01", "force": True},
                  capture={"validation_class": "synthetic"}, clock=CLOCK)

    def item(id_, image, split, workflow, mode, expected, **extra):
        return {"id": id_, "image": f"images/{image}", "sha256": _sha(images / image),
                "split": split, "workflow": workflow, "mode": mode, "expected": expected,
                "optical_profile": None, "component_type_confirmed": None,
                "known_uncertainty": "synthetic render", "annotation": {
                    "operator": "generator", "notes": "synthetic; not a microscope photo"}, **extra}

    dataset = {
        "schema": "anastylos-microscopy-dataset/1",
        "dataset_id": "synthetic-mic-v1",
        "provenance": {"kind": "synthetic", "specimen": "rendered fixtures (NOT the MSI GTX 970)",
                       "is_msi_gtx970_capture": False, "operator": None,
                       "notes": "built by examples/build_synthetic_examples.py"},
        "items": [
            item("mic01-472", "smd_472.png", "dev", "MIC-01", "smd", {"visible_marking": "472"},
                 component_type_confirmed=None),
            item("mic01-103", "smd_103_heldout.png", "heldout", "MIC-01", "smd",
                 {"visible_marking": "103"}),
            item("mic01-unmarked", "smd_unmarked.png", "dev", "MIC-01", "smd", {"abstain": True}),
            item("mic02-silk", "silk_r12_c5.png", "dev", "MIC-02", "pcb_ocr",
                 {"designators": ["R12", "C5"]}),
            item("mic03-lm358", "ic_lm358n.png", "dev", "MIC-03", "markings",
                 {"visible_marking": "LM358N"}),
            item("mic04-ic-query", "fp_ic_query_rot30.png", "dev", "MIC-04", "fingerprints",
                 {"gallery_id": "synthetic-ic-01"}, options={"gallery": "gallery"}),
            item("mic04-negative", "fp_capacitor_negative.png", "dev", "MIC-04", "fingerprints",
                 {"gallery_id": None}, options={"gallery": "gallery"}),
            item("mic05-uniform", "unknown_uniform.png", "dev", "MIC-05", "auto", {"abstain": True}),
            item("mic05-blur", "unknown_blurred_resistor.png", "dev", "MIC-05", "resistor",
                 {"abstain": True}),
            item("mic05-glare", "unknown_glare.png", "dev", "MIC-05", "resistor", {"abstain": True}),
        ],
    }
    (out / "dataset.json").write_text(json.dumps(dataset, indent=2) + "\n", encoding="utf-8")

    frames = out / "capture_node_example" / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    entries = []
    for seq, sequence in enumerate((["brown", "black", "red", "gold"],
                                    ["yellow", "violet", "red", "gold"]), start=1):
        name = f"frame_{seq:06d}_20261029T1200{seq:02d}000Z.jpg"
        ok, buffer = cv2.imencode(".jpg", res_synth.render(sequence), [cv2.IMWRITE_JPEG_QUALITY, 92])
        (frames / name).write_bytes(buffer.tobytes())
        entries.append({"file": f"frames/{name}", "sha256": _sha(frames / name), "seq": seq,
                        "captured_utc": f"2026-10-29T12:00:{seq:02d}.000Z",
                        "orientation_cw_deg": 0, "roi_xywh": None,
                        "component_observation_id": f"synthetic-obs-{seq}"})
    manifest = {
        "schema": "anastylos-microscopy-capture-manifest/1",
        "node_id": "SYNTHETIC-EXAMPLE-NODE",
        "session_id": "synthetic-session-001",
        "validation_class": "synthetic",
        "source": {"camera_id": "synthetic-renderer", "optical_profile": None,
                   "capture_device": None, "capture_format": "JPEG (synthetic, re-encoded render)",
                   "capture_resolution": [900, 320],
                   "camera_settings_note": "synthetic; a real node would record MUSTOOL settings "
                                           "read off the microscope by the operator"},
        "orientation_cw_deg": 0,
        "calibration": None,
        "frames": entries,
    }
    (out / "capture_node_example" / "capture-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"dataset": str(out / "dataset.json"),
            "manifest": str(out / "capture_node_example" / "capture-manifest.json")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(HERE / "synthetic_mic"))
    print(json.dumps(build(Path(parser.parse_args().out)), indent=2))
