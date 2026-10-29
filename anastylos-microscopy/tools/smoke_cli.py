"""Cross-platform CLI smoke test for Microscopy Core v0.1.1 (Windows, Linux, macOS).

Usage:  python tools/smoke_cli.py [--anastylos-root PATH] [--out DIR] [--json REPORT]

Runs the real CLI (`python -m anastylos_microscopy ...`) in child processes
on the bundled SYNTHETIC examples and compares every *process exit status*
with the documented expectation. Also builds a Windows junction / POSIX
directory-symlink escape on the fly and checks that both `validate` and
`inspect --manifest` reject it. Prints one line per step; exits 1 on any
mismatch. Nothing is written outside --out (default: a temp directory).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
EX = PKG / "examples" / "synthetic_mic"
CLOCK = "2026-10-29T12:00:00Z"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--anastylos-root", default=os.environ.get("ANASTYLOS_ROOT"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)
    if not args.anastylos_root:
        parser.error("set ANASTYLOS_ROOT or pass --anastylos-root")
    out = Path(args.out or tempfile.mkdtemp(prefix="mic-smoke-"))
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(PKG / "src"), "ANASTYLOS_ROOT": args.anastylos_root,
           "PYTHONDONTWRITEBYTECODE": "1"}
    no_root = {k: v for k, v in env.items() if k != "ANASTYLOS_ROOT"}
    rows, failed = [], False

    def step(name, cli_args, expect, environ=None):
        nonlocal failed
        proc = subprocess.run([sys.executable, "-m", "anastylos_microscopy", *map(str, cli_args)],
                              cwd=PKG, env=environ or env, capture_output=True, text=True)
        ok = proc.returncode == expect
        failed |= not ok
        rows.append({"step": name, "args": list(map(str, cli_args)), "expected_exit": expect,
                     "exit": proc.returncode, "pass": ok})
        print(f"{'PASS' if ok else 'FAIL'}  exit={proc.returncode} expected={expect}  {name}")
        if not ok:
            print(proc.stdout[-1500:], proc.stderr[-1500:], sep="\n")
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return None

    step("readers", ["readers"], 0)
    r = step("inspect single image (resistor, clean render via capture frame)",
             ["inspect", "--image", EX / "capture_node_example/frames/frame_000001_20261029T120001000Z.jpg",
              "--mode", "resistor", "--clock", CLOCK, "--out", out / "single"], 0)
    insp = r and r.get("inspection_json")
    g = step("inspect glare fixture -> ambiguous (QA-03), exit 0",
             ["inspect", "--image", EX / "images/unknown_glare.png", "--mode", "resistor",
              "--clock", CLOCK, "--out", out / "glare"], 0)
    if g and g.get("overall_status") != "ambiguous":
        failed = True
        print("FAIL  glare overall_status", g.get("overall_status"))
    step("inspect --manifest example", ["inspect", "--manifest",
                                        EX / "capture_node_example/capture-manifest.json", "--mode",
                                        "resistor", "--clock", CLOCK, "--out", out / "manifest"], 0)
    step("validate capture_manifest example", ["validate", "--kind", "capture_manifest",
                                                EX / "capture_node_example/capture-manifest.json"], 0)
    if insp:
        step("validate inspection", ["validate", "--kind", "inspection", insp], 0)
        step("replay inspection (identical digest)", ["replay", "--inspection", insp, "--out",
                                                     out / "replay"], 0)
        step("review record", ["review", "--inspection", insp, "--operator", "smoke-test",
                               "--decision", "inconclusive", "--clock", CLOCK], 0)
    step("validate batch.json", ["validate", "--kind", "batch", out / "manifest" / "batch.json"], 0)
    # QA-01 batch exit codes
    good = out / "in_good"
    mixed = out / "in_mixed"
    bad = out / "in_bad"
    empty = out / "in_empty"
    for d in (good, mixed, bad, empty):
        d.mkdir(exist_ok=True)
    frame = EX / "capture_node_example/frames/frame_000001_20261029T120001000Z.jpg"
    shutil.copy(frame, good / "a.jpg")
    shutil.copy(EX / "images/unknown_glare.png", good / "b.png")
    shutil.copy(frame, mixed / "a.jpg")
    (mixed / "b.png").write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
    (bad / "a.png").write_bytes(b"not an image")
    (bad / "b.jpg").write_bytes(b"\xff\xd8\xffbroken")
    step("batch all completed -> 0", ["inspect", "--batch-dir", good, "--mode", "resistor",
                                      "--clock", CLOCK, "--out", out / "b_good"], 0)
    step("batch valid + corrupt -> 4", ["inspect", "--batch-dir", mixed, "--mode", "resistor",
                                        "--clock", CLOCK, "--out", out / "b_mixed"], 4)
    step("batch all invalid -> 1", ["inspect", "--batch-dir", bad, "--mode", "resistor",
                                    "--clock", CLOCK, "--out", out / "b_bad"], 1)
    step("batch empty -> 1", ["inspect", "--batch-dir", empty, "--mode", "resistor",
                              "--out", out / "b_empty"], 1)
    step("batch without ANASTYLOS_ROOT -> 3", ["inspect", "--batch-dir", good, "--mode", "resistor",
                                               "--out", out / "b_noroot"], 3, environ=no_root)
    step("batch OCR unavailable -> 3", ["inspect", "--batch-dir", good, "--mode", "smd",
                                        "--tesseract-cmd", "tesseract-not-installed-smoke",
                                        "--out", out / "b_noocr"], 3)
    # QA-02 escape through a directory junction (Windows) / directory symlink (POSIX)
    cap, outside = out / "cap", out / "outside"
    cap.mkdir(exist_ok=True)
    outside.mkdir(exist_ok=True)
    shutil.copy(frame, outside / "escape.jpg")
    import hashlib
    sha = hashlib.sha256((outside / "escape.jpg").read_bytes()).hexdigest()
    link = cap / "frames"
    kind = "junction" if os.name == "nt" else "symlink"
    try:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True,
                           capture_output=True)
        else:
            os.symlink(outside, link, target_is_directory=True)
        made = True
    except (OSError, subprocess.CalledProcessError) as exc:
        made = False
        print(f"SKIP  could not create directory {kind}: {exc}")
        rows.append({"step": f"escape via {kind}", "skipped": str(exc)})
    if made:
        (cap / "capture-manifest.json").write_text(json.dumps({
            "schema": "anastylos-microscopy-capture-manifest/1", "validation_class": "synthetic",
            "source": {"camera_id": "smoke"},
            "frames": [{"file": "frames/escape.jpg", "sha256": sha}]}), encoding="utf-8")
        step(f"validate manifest with frames/ {kind} outside root -> 1",
             ["validate", "--kind", "capture_manifest", cap / "capture-manifest.json"], 1)
        step(f"inspect --manifest with frames/ {kind} outside root -> 1",
             ["inspect", "--manifest", cap / "capture-manifest.json", "--mode", "resistor",
              "--out", out / "escape"], 1)
        leaked = [p for p in (out / "escape").rglob("*") if p.is_file() and sha in p.name]
        if leaked:
            failed = True
            print("FAIL  unauthorized bytes were preserved:", leaked)
    step("eval --print-fingerprint", ["eval", "--print-fingerprint"], 0)
    step("eval dev split (no Tesseract unless installed)", ["eval", "--dataset", EX / "dataset.json",
                                                            "--split", "dev", "--clock", CLOCK,
                                                            "--out", out / "eval"], 0)
    step("eval heldout without --frozen-config -> 1", ["eval", "--dataset", EX / "dataset.json",
                                                      "--split", "heldout", "--out", out / "eval2"], 1)
    rep = json.loads((out / "eval" / "eval-dev.json").read_text(encoding="utf-8"))
    glare = [i for i in rep["items"] if i["id"] == "mic05-glare"][0]["outcome"]
    print(f"{'PASS' if glare == 'correct_abstention' else 'FAIL'}  mic05-glare outcome = {glare}")
    failed |= glare != "correct_abstention"
    summary = {"python": sys.version.split()[0], "platform": sys.platform, "out": str(out),
               "steps": rows, "mic05_glare": glare, "ok": not failed}
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("SMOKE", "OK" if not failed else "FAILURES", f"({sum(1 for r in rows if r.get('pass'))} "
          f"passed of {sum(1 for r in rows if 'pass' in r)} exit-code steps)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
