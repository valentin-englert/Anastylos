"""Gate 2A runner: evidence pack + real Muse vision inference.

Subcommands:
  build   cut crops + context frames from source frames, write the
          evidence manifest (video/frame/crop/prompt SHA-256). Offline.
  run     send Task A + Task B per case to the Muse Model API.
          Sequential, no retries, hard budget abort. Needs MODEL_API_KEY
          env or --api-key-stdin (piped, never logged).
  sheet   render the contact sheet + review template from saved
          results. Offline (re-runnable without new inference).

Costs are estimated from response token usage with --price-in/out
(default: public third-party anchor 1.25/4.25 USD per M tokens,
labeled ESTIMATED_FROM_PUBLIC_RATES, source unverified). The runner
aborts BEFORE any call that could exceed --budget-usd (default 3.0).

Nothing here confirms identities: model outputs are hypotheses.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover
    sys.exit(f"gate2a_run needs OpenCV + NumPy: {exc}")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate2a_client as client

SCHEMA_PACK = "snes01-gate2a/evidence-pack@1"
SCHEMA_RESULTS = "snes01-gate2a/results@1"
VERDICTS = ("same_instance_candidate", "different_instance_candidate",
            "insufficient_evidence")
VIDEO_SHA256 = ("2e103bfc99c7276ac0162a8870a2be1"
                "01d10ec42e8d822c686243f4ab5b51bc9")
PRICE_SRC = ("third-party press anchor (getaiperks/promptfoo ecosystem), "
             "unverified against dev.meta.ai billing")


def _die(message: str):
    print(f"gate2a_run: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases(paths: list) -> list:
    cases = []
    for spec in paths:
        try:
            doc = json.loads(Path(spec).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _die(f"cannot read cases {spec}: {exc}")
        if doc.get("schema") != "snes01-gate2a/cases@1":
            _die(f"{spec}: not gate2a cases")
        cases.extend(doc["cases"])
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        _die("duplicate case ids across case files")
    return cases


def load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"cannot read manifest: {exc}")
    if manifest.get("schema") != "snes01-benchmark/manifest@1":
        _die("not the SNES-01 source manifest")
    return manifest


def cut_crop(image, box: list):
    x, y, w, h = (int(v) for v in box)
    height, width = image.shape[:2]
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    return image[y:y + h, x:x + w]


def cmd_build(args) -> int:
    cases = load_cases(args.cases)
    manifest = load_manifest(Path(args.manifest))
    file_hash = {f["name"]: f["sha256"]
                 for f in manifest["frames"]["files"]}
    frames_dir = Path(args.frames_dir)
    out = Path(args.out)
    (out / "crops").mkdir(parents=True, exist_ok=True)
    (out / "context").mkdir(parents=True, exist_ok=True)
    prompt_a = Path(args.prompt_a).read_text(encoding="utf-8")
    prompt_b = Path(args.prompt_b).read_text(encoding="utf-8")
    built = []
    for case in cases:
        entry = {"id": case["id"], "group": case["group"],
                 "selection_rule": case["selection_rule"],
                 "images": {}}
        for role, spec, sub in (
                ("src", case["source"], "crops"),
                ("dest", case["dest"], "crops")):
            frame_path = frames_dir / spec["frame"]
            if not frame_path.is_file():
                _die(f"{case['id']}: missing {spec['frame']}")
            digest = sha256_file(frame_path)
            if digest != file_hash.get(spec["frame"]):
                _die(f"{case['id']}: {spec['frame']} != manifest hash")
            image = cv2.imread(str(frame_path))
            crop = cut_crop(image, spec["bbox_xywh"])
            name = f"{case['id']}_{role}.png"
            cv2.imwrite(str(out / sub / name), crop)
            entry["images"][role] = {
                "file": f"{sub}/{name}",
                "frame": spec["frame"], "t_s": spec["t_s"],
                "bbox_xywh": spec["bbox_xywh"],
                "frame_sha256": digest,
                "crop_sha256": sha256_file(out / sub / name),
                "crop_size_xy": [int(crop.shape[1]), int(crop.shape[0])],
            }
        for role, frame_key in (("before", "before_frame"),
                                ("after", "after_frame")):
            ctx = case.get("context", {})
            frame = ctx.get(frame_key)
            if frame is None:
                frame = case["source" if role == "before"
                             else "dest"]["frame"]
            frame_path = frames_dir / frame
            digest = sha256_file(frame_path)
            if digest != file_hash.get(frame):
                _die(f"{case['id']}: {frame} != manifest hash")
            image = cv2.imread(str(frame_path))
            small = cv2.resize(image, (540, 960),
                               interpolation=cv2.INTER_AREA)
            name = f"{case['id']}_{role}.jpg"
            cv2.imwrite(str(out / "context" / name), small,
                        [cv2.IMWRITE_JPEG_QUALITY, 90])
            entry["images"][role] = {
                "file": f"context/{name}", "frame": frame,
                "t_s": int(frame.split("_")[1].split(".")[0]) - 1,
                "frame_sha256": digest,
                "file_sha256": sha256_file(out / "context" / name),
                "size_xy": [540, 960],
            }
        entry["case"] = case
        built.append(entry)
    pack = {
        "schema": SCHEMA_PACK,
        "video_sha256": VIDEO_SHA256,
        "manifest": args.manifest,
        "manifest_sha256": sha256_file(Path(args.manifest)),
        "prompt_a_sha256": hashlib.sha256(
            prompt_a.encode()).hexdigest(),
        "prompt_b_sha256": hashlib.sha256(
            prompt_b.encode()).hexdigest(),
        "cases": built,
    }
    (out / "evidence_pack.json").write_text(json.dumps(pack, indent=2),
                                            encoding="utf-8")
    print(json.dumps({"cases": len(built), "out": str(out)}, indent=2))
    return 0


def extract_json(text: str | None) -> tuple[dict | None, str]:
    """Parse model text as JSON; fall back to first {...} span.
    Returns (parsed_or_None, method). Never coerces verdicts."""
    if not text:
        return None, "empty"
    try:
        return json.loads(text), "direct"
    except ValueError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1]), "span"
        except ValueError:
            return None, "span-invalid"
    return None, "no-braces"


def estimate(tokens_in: int, tokens_out: int, price_in: float,
             price_out: float) -> float:
    return tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out


def fill_template(template: str, mapping: dict) -> str:
    """Replace {name} tokens literally (JSON braces untouched)."""
    for name, value in mapping.items():
        template = template.replace("{" + name + "}", str(value))
    return template


def one_call(key: str, model: str, prompt: str, image_paths: list,
             max_tokens: int, max_side: int) -> dict:
    """Single API call, no retries. Returns envelope + raw response."""
    content: list = [{"type": "text", "text": prompt}]
    infos = []
    for spec in image_paths:
        url, info = client.encode_image(Path(spec), max_side)
        infos.append(info)
        content.append({"type": "image_url",
                        "image_url": {"url": url}})
    payload = {"model": model,
               "messages": [{"role": "user", "content": content}],
               "max_tokens": max_tokens,
               "response_format": {"type": "json_object"}}
    try:
        status, data, latency = client._request(
            "POST", client.BASE_URL + "/chat/completions", key, payload,
            timeout_s=300.0)
    except client.ApiError as exc:
        return {"http": exc.status, "error": exc.detail,
                "latency_s": round(exc.latency_s, 2), "images": infos,
                "raw": None}
    usage = data.get("usage", {}) or {}
    details = usage.get("completion_tokens_details", {}) or {}
    choice = {}
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError):
        pass
    content_out = (choice.get("message") or {}).get("content")
    if isinstance(content_out, list):
        content_out = "".join(
            part.get("text", "") for part in content_out
            if isinstance(part, dict))
    return {"http": status, "request_id": data.get("id"),
            "model": data.get("model", model),
            "finish_reason": choice.get("finish_reason"),
            "tokens_in": usage.get("prompt_tokens"),
            "tokens_out": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "latency_s": round(latency, 2), "images": infos,
            "text": content_out, "raw": data}


def cmd_run(args) -> int:
    import os
    key = ""
    if not args.dry_run:
        if args.api_key_stdin:
            key = sys.stdin.read().strip()
            if not key:
                _die("no key on stdin")
        else:
            key = os.environ.get("MODEL_API_KEY", "").strip()
            if not key:
                _die("set MODEL_API_KEY or use --api-key-stdin")
    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    if pack.get("schema") != SCHEMA_PACK:
        _die("not a Gate 2A evidence pack")
    prompt_a = Path(args.prompt_a).read_text(encoding="utf-8")
    prompt_b = Path(args.prompt_b).read_text(encoding="utf-8")
    for label, text, want in (
            ("prompt A", prompt_a, pack["prompt_a_sha256"]),
            ("prompt B", prompt_b, pack["prompt_b_sha256"])):
        if hashlib.sha256(text.encode()).hexdigest() != want:
            _die(f"{label} differs from frozen pack hash; refusing")
    pack_dir = Path(args.pack).parent
    out = Path(args.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    ledger = out / "ledger.jsonl"
    spent, results = 0.0, []
    wanted = pack["cases"]
    if getattr(args, "only", None):
        keep = set(args.only.split(","))
        wanted = [c for c in wanted if c["id"] in keep]
        if not wanted:
            _die("--only matched no case ids")
    tasks_wanted = set(getattr(args, "tasks", "AB"))
    planned = len(wanted) * len(tasks_wanted)
    done = 0
    for case in wanted:
        entry = {"id": case["id"], "group": case["group"],
                 "tasks": {}}
        images = case["images"]
        jobs = [
            ("A", fill_template(prompt_a, {
                "before_frame": images["before"]["frame"],
                "before_t": images["before"].get("t_s", "?"),
                "after_frame": images["after"]["frame"],
                "after_t": images["after"].get("t_s", "?"),
                "src_box": images["src"]["bbox_xywh"],
                "dest_box": images["dest"]["bbox_xywh"]}),
             [images["before"]["file"], images["after"]["file"],
              images["src"]["file"], images["dest"]["file"]]),
            ("B", fill_template(prompt_b, {
                "src_box": images["src"]["bbox_xywh"],
                "src_frame": images["src"]["frame"],
                "src_t": images["src"]["t_s"],
                "dest_box": images["dest"]["bbox_xywh"],
                "dest_frame": images["dest"]["frame"],
                "dest_t": images["dest"]["t_s"]}),
             [images["src"]["file"], images["dest"]["file"]]),
        ]
        for task, prompt_text, rel_paths in jobs:
            if task not in tasks_wanted:
                continue
            if args.dry_run:
                entry["tasks"][task] = {"dry_run": True}
                continue
            worst = estimate(4000, args.max_tokens, args.price_in,
                             args.price_out)
            if spent + worst > args.budget_usd:
                _die(f"budget: stopping before {case['id']}-{task} "
                     f"(spent ~${spent:.4f} of ${args.budget_usd})")
            response = one_call(key, args.model, prompt_text,
                                [str(pack_dir / p) for p in rel_paths],
                                args.max_tokens, args.max_side)
            (out / "raw" / f"{case['id']}_{task}.json").write_text(
                json.dumps({"prompt": prompt_text,
                            "response": response["raw"],
                            "http": response["http"]}, indent=2),
                encoding="utf-8")
            if response["http"] != 200 or response["raw"] is None:
                task_entry = {"http": response["http"],
                              "error": response.get("error"),
                              "latency_s": response["latency_s"]}
            else:
                parsed, method = extract_json(response.get("text"))
                verdict = (parsed or {}).get("verdict")
                if task == "B" and verdict not in VERDICTS:
                    verdict_note = ("INVALID_RESPONSE: verdict not in "
                                    "enum (recorded, not coerced)")
                else:
                    verdict_note = ""
                cost = estimate(response["tokens_in"] or 0,
                                response["tokens_out"] or 0,
                                args.price_in, args.price_out)
                spent += cost
                task_entry = {
                    "http": response["http"],
                    "request_id": response["request_id"],
                    "model": response["model"],
                    "finish_reason": response["finish_reason"],
                    "tokens_in": response["tokens_in"],
                    "tokens_out": response["tokens_out"],
                    "reasoning_tokens": response["reasoning_tokens"],
                    "latency_s": response["latency_s"],
                    "est_cost_usd": round(cost, 6),
                    "parse_method": method,
                    "parsed": parsed,
                    "verdict_note": verdict_note,
                }
                client.append_ledger(
                    ledger, {"kind": f"case-{task}",
                             "case": case["id"],
                             "http": response["http"],
                             "request_id": response["request_id"],
                             "model": response["model"],
                             "tokens_in": response["tokens_in"],
                             "tokens_out": response["tokens_out"],
                             "latency_s": response["latency_s"],
                             "est_cost_usd": round(cost, 6)})
            entry["tasks"][task] = task_entry
            done += 1
            print(f"done {done}/{planned}: {case['id']}-{task} "
                  f"http={task_entry.get('http')}", flush=True)
        results.append(entry)
    report = {
        "schema": SCHEMA_RESULTS,
        "model": args.model,
        "est_spent_usd": round(spent, 6),
        "price_basis": "ESTIMATED_FROM_PUBLIC_RATES",
        "price_source": PRICE_SRC,
        "price_in_per_m": args.price_in,
        "price_out_per_m": args.price_out,
        "budget_usd": args.budget_usd,
        "results": results,
    }
    (out / "results.json").write_text(json.dumps(report, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"cases": len(results),
                      "est_spent_usd": round(spent, 6)}, indent=2))
    return 0


def cmd_sheet(args) -> int:
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    pack_dir = Path(args.pack).parent
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in results["results"]:
        case = next(c for c in pack["cases"] if c["id"] == entry["id"])
        cells = []
        for role in ("src", "dest"):
            image = cv2.imread(
                str(pack_dir / case["images"][role]["file"]))
            height, width = image.shape[:2]
            scale = 480 / max(1, height)
            if scale < 1.0:
                image = cv2.resize(image, (int(width * scale), 480))
            canvas = np.full((480, 480, 3), 20, np.uint8)
            h, w = image.shape[:2]
            canvas[:h, :w] = image
            cv2.putText(canvas, f"{role} {case['images'][role]['frame']}",
                        (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (255, 255, 255), 2)
            cells.append(canvas)
        verdict = ((entry["tasks"].get("B", {}).get("parsed") or {})
                   .get("verdict", "NO-RESPONSE"))
        panel = np.full((480, 480, 3), 20, np.uint8)
        cv2.putText(panel, f"{entry['id']} [{entry['group']}]", (8, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.putText(panel, f"B verdict: {verdict}", (8, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        for num, task in enumerate("AB"):
            task_entry = entry["tasks"].get(task, {})
            line = (f"{task}: http={task_entry.get('http')} "
                    f"tok={task_entry.get('tokens_in')}/"
                    f"{task_entry.get('tokens_out')} "
                    f"parse={task_entry.get('parse_method')}")
            cv2.putText(panel, line[:52], (8, 110 + 30 * num),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cells.append(panel)
        rows.append(np.hstack(cells))
    cv2.imwrite(str(out / "contact_sheet_gate2a.jpg"), np.vstack(rows),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    with open(out / "review_gate2a.csv", "w", newline="",
              encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "group", "task", "auto_verdict",
                         "auto_confidence_uncalibrated",
                         "correct_visual_description",
                         "supported_component_match",
                         "correct_abstention", "false_positive",
                         "hallucinated_evidence",
                         "human_intervention_required",
                         "operator_note"])
        for entry in results["results"]:
            for task in "AB":
                task_entry = entry["tasks"].get(task, {})
                parsed = task_entry.get("parsed") or {}
                writer.writerow([
                    entry["id"], entry["group"], task,
                    parsed.get("verdict", ""),
                    parsed.get("confidence_uncalibrated", ""), "", "", "",
                    "", "", "", ""])
    print(json.dumps({"rows": len(rows), "out": str(out)}, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gate 2A runner.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--cases", action="append", required=True)
    build.add_argument("--frames-dir", required=True)
    build.add_argument("--manifest", required=True)
    build.add_argument("--prompt-a", required=True)
    build.add_argument("--prompt-b", required=True)
    build.add_argument("--out", required=True)
    run = sub.add_parser("run")
    run.add_argument("--pack", required=True)
    run.add_argument("--prompt-a", required=True)
    run.add_argument("--prompt-b", required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--max-tokens", type=int, default=800)
    run.add_argument("--max-side", type=int, default=512)
    run.add_argument("--budget-usd", type=float, default=3.0)
    run.add_argument("--price-in", type=float, default=1.25)
    run.add_argument("--price-out", type=float, default=4.25)
    run.add_argument("--api-key-stdin", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--only", default=None,
                     help="comma-separated case ids (completion runs)")
    run.add_argument("--tasks", default="AB",
                     help="subset of tasks, e.g. A")
    sheet = sub.add_parser("sheet")
    sheet.add_argument("--results", required=True)
    sheet.add_argument("--pack", required=True)
    sheet.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        return cmd_build(args)
    if args.command == "run":
        return cmd_run(args)
    return cmd_sheet(args)


if __name__ == "__main__":
    sys.exit(main())
