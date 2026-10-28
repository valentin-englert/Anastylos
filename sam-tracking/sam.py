#!/usr/bin/env python3
"""ANASTYLOS SAM tracking adapter CLI (experimental, cloud-isolated).

Nothing here runs against the network unless BOTH --live and
--upload-ok are given (GET /v1/models needs --live only). Default is
dry-run: plan, estimate cost, write nothing but the plan file.

Subcommands:
  models           list models (free metadata probe)
  cut-clip         ffmpeg sub-clip with faststart + provenance (offline)
  plan-image       dry-run an image request (offline, writes plan)
  plan-video       dry-run a video request (offline, writes plan)
  segment-image    live image segmentation -> sam-evidence-v1
  segment-video    live video segmentation -> sam-evidence-v1
  replay           re-parse a cached transcript, re-verify (offline)
  render           overlays / contact sheet / review mp4 (offline)
  fuse             temporal fusion over evidence dirs (offline)
  eval-seg         score segmentation vs frozen GT (offline)
  report           usage/cost roll-up over a run dir (offline)

Only this CLI imports anastylos_sam.client (network); replay, render,
fuse, eval, and report never load socket code.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anastylos_sam import EVIDENCE_SCHEMA, MODEL_ID, TOOL_NAME, TOOL_VERSION  # noqa: E402
from anastylos_sam import evidence as EV  # noqa: E402
from anastylos_sam import prompts as PR  # noqa: E402
from anastylos_sam import png as PNG  # noqa: E402
from anastylos_sam import parse as PA  # noqa: E402
from anastylos_sam import fuse as FU  # noqa: E402
from anastylos_sam import eval as SC  # noqa: E402
from anastylos_sam.util import (  # noqa: E402
    atomic_write_json, load_json, sha256_bytes, sha256_file, slug,
    utc_now_iso,
)


def _die(message: str) -> "typing.NoReturn":
    import typing  # noqa: PLC0415
    print(f"{TOOL_NAME}: ERROR: {message}", file=sys.stderr)
    raise SystemExit(2)


def _tool_versions() -> dict:
    import platform  # noqa: PLC0415
    versions = {"python": platform.python_version(),
                TOOL_NAME: TOOL_VERSION}
    try:
        import cv2  # noqa: PLC0415
        versions["opencv"] = cv2.__version__
    except ImportError:
        versions["opencv"] = None
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                             text=True, timeout=15, check=False)
        versions["ffmpeg"] = (out.stdout.splitlines() or ["absent"])[0][:120]
    except (OSError, subprocess.SubprocessError):
        versions["ffmpeg"] = None
    return versions


def _jpeg_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:2] != b"\xff\xd8":
        raise ValueError(f"{path}: not a JPEG")
    pos = 2
    while pos + 4 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height = int.from_bytes(data[pos + 5:pos + 7], "big")
            width = int.from_bytes(data[pos + 7:pos + 9], "big")
            return width, height
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            pos += 2
            continue
        size = int.from_bytes(data[pos + 2:pos + 4], "big")
        pos += 2 + size
    raise ValueError(f"{path}: JPEG dimensions not found")


def _media_size(path: Path) -> tuple[int, int, str]:
    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        width, height = _jpeg_size(path)
        return width, height, "jpeg"
    if suffix == ".png":
        data = path.read_bytes()
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return width, height, "png"
    raise ValueError(f"{path}: image must be .jpg/.jpeg/.png for upload")


def _ffprobe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,width,height,avg_frame_rate,nb_frames,duration,r_frame_rate",
         "-show_entries", "format=duration,size", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        _die(f"ffprobe failed on {path}: {proc.stderr[:300]}")
    return json.loads(proc.stdout)


def _has_faststart(path: Path) -> bool:
    """True when the moov atom precedes mdat (top-level scan)."""
    data = path.read_bytes()[:8 * 1024 * 1024]
    pos = 0
    seen_moov = False
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        tag = data[pos + 4:pos + 8]
        if size < 8:
            break
        if tag == b"moov":
            seen_moov = True
        if tag == b"mdat":
            return seen_moov
        pos += size
    return False


def _request_payload(*, text: str, media_part: dict,
                     confidence: bool, mask_encoding: str,
                     direction: str | None) -> dict:
    metadata: dict[str, str] = {}
    if confidence:
        metadata["include_confidence"] = "true"
    if mask_encoding != "lossless":
        metadata["mask_encoding"] = mask_encoding
    if direction is not None:
        metadata["propagation_direction"] = direction
    payload: dict = {
        "model": MODEL_ID,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": text}, media_part]}],
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def cmd_models(args) -> int:
    from anastylos_sam import client as CL  # noqa: PLC0415
    key = CL.load_key(args.api_key_stdin)
    try:
        status, data = CL.get_models(key)
    except CL.ApiError as exc:
        print(json.dumps({"http": exc.status, "detail": exc.detail,
                          "latency_s": round(exc.latency_s, 2)}, indent=2))
        return 3
    models = data.get("data", data)
    print(json.dumps({"http": status, "models": models}, indent=2)[:4000])
    return 0


def cmd_cut_clip(args) -> int:
    src = Path(args.video)
    if not src.is_file():
        _die(f"video not found: {src}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-ss", str(args.t_start), "-i", str(src),
           "-t", str(args.seconds), "-c", "copy", "-movflags", "+faststart",
           str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        _die(f"ffmpeg cut failed: {proc.stderr[-800:]}")
    probe = _ffprobe(out)
    streams = probe.get("streams", [{}])
    meta = {
        "schema": "sam-evidence-v1/clip@1",
        "src_path": str(src),
        "src_sha256": sha256_file(src),
        "src_bytes": src.stat().st_size,
        "clip_path": str(out),
        "clip_sha256": sha256_file(out),
        "clip_bytes": out.stat().st_size,
        "t_start_s": args.t_start,
        "seconds": args.seconds,
        "faststart": _has_faststart(out),
        "video_stream": streams[0],
        "command": cmd,
        "created_utc": utc_now_iso(),
    }
    atomic_write_json(out.with_suffix(".clip.json"), meta)
    print(json.dumps({key: meta[key] for key in
                      ("clip_sha256", "clip_bytes", "faststart",
                       "t_start_s", "seconds")}, indent=2))
    if not meta["faststart"]:
        _die("remux did not produce faststart; refusing to continue")
    return 0


def _plan_common(args, *, media_kind: str, media: dict,
                 prompt: dict, params: dict, frames: int) -> dict:
    usage = EV.estimate_cost(media_kind=media_kind, frames=frames)
    return {"schema": "sam-evidence-v1/plan@1", "dry_run": True,
            "model": MODEL_ID, "prompt": prompt, "media": media,
            "params": params, "usage_estimate": usage,
            "budget_usd": args.budget_usd,
            "within_budget": usage["usd"] + args.spent_usd <= args.budget_usd + 1e-9,
            "created_utc": utc_now_iso()}


def _prompt_from_args(args) -> tuple[str, dict]:
    if bool(args.concept) == bool(args.objects_json):
        _die("give exactly one of --concept or --objects-json")
    if args.concept:
        text = PR.normalize_concept(args.concept)
        warnings = PR.validate_concept(text)
        return text, {"kind": "concept", "concept": text,
                      "warnings": warnings}
    spec = load_json(args.objects_json)
    text, echo = PR.build_box_prompt(size=(spec["width"], spec["height"]),
                                     objects=spec["objects"])
    return text, {"kind": "objects", "objects": echo,
                  "size": [spec["width"], spec["height"]]}


def cmd_plan_image(args) -> int:
    path = Path(args.image)
    width, height, fmt = _media_size(path)
    text, prompt = _prompt_from_args(args)
    _ = text
    media = {"kind": "image", "path": str(path), "sha256": sha256_file(path),
             "bytes": path.stat().st_size, "width": width, "height": height,
             "format": fmt}
    params = {"stream": True, "include_confidence": not args.no_confidence,
              "mask_encoding": args.mask_encoding,
              "propagation_direction": None}
    plan = _plan_common(args, media_kind="image", media=media, prompt=prompt,
                        params=params, frames=1)
    if args.out:
        atomic_write_json(Path(args.out), plan)
    print(json.dumps(plan, indent=2))
    return 0


def cmd_plan_video(args) -> int:
    clip = Path(args.clip)
    if not clip.is_file():
        _die(f"clip not found: {clip}")
    probe = _ffprobe(clip)
    stream = probe.get("streams", [{}])[0]
    try:
        frames = int(stream.get("nb_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    _text, prompt = _prompt_from_args(args)
    media = {"kind": "video", "path": str(clip), "sha256": sha256_file(clip),
             "bytes": clip.stat().st_size, "width": stream.get("width"),
             "height": stream.get("height"),
             "nb_frames": frames, "faststart": _has_faststart(clip)}
    params = {"stream": True, "include_confidence": not args.no_confidence,
              "mask_encoding": args.mask_encoding,
              "propagation_direction": args.direction}
    plan = _plan_common(args, media_kind="video", media=media, prompt=prompt,
                        params=params, frames=frames)
    if args.out:
        atomic_write_json(Path(args.out), plan)
    print(json.dumps(plan, indent=2))
    return 0


def _require_live(args) -> None:
    if not args.live or not args.upload_ok:
        _die("live upload needs BOTH --live and --upload-ok "
             "(refusing to send real captures to the cloud)")


def _finalize_request(*, out: Path, request_id: str, created_utc: str,
                      prompt: dict, media: dict, params: dict,
                      events: list[dict], meta: dict, cost: dict,
                      notes: list[str]) -> Path:
    parsed = PA.parse_events(events, media["kind"])
    mask_pngs: dict[str, bytes] = {}
    records: list[dict] = []
    for item in parsed["records"]:
        mask = item["mask"]
        name = f"mask_f{item['frame']}_o{item['object_id']}.png"
        png = PNG.encode_gray8(mask["width"], mask["height"], mask["raster"])
        mask_pngs[name] = png
        records.append({
            "frame": item["frame"], "object_id": item["object_id"],
            "scope": "request:" + request_id, "box": item["box"],
            "confidence": item.get("confidence"),
            "mask_confidence": item.get("mask_confidence"),
            "mask": {"height": mask["height"], "width": mask["width"],
                     "encoding": mask["encoding"], "png": "masks/" + name,
                     "sha256": sha256_bytes(png), "rle": mask["rle"]},
        })
    api_id = next((event.get("response", {}).get("id")
                   for event in events
                   if isinstance(event, dict) and
                   event.get("type") == "response.completed"), None)
    response = {"api_request_id": api_id, "outcome": parsed["outcome"],
                "n_events": len(events), "n_records": len(records),
                "n_frames": len({item["frame"] for item in records}),
                "zero_matches": not records, "transport": meta}
    record = EV.build_record(
        request_id=request_id, created_utc=created_utc, prompt=prompt,
        media=media, params=params, response=response, usage=cost,
        records=records, diagnostics=parsed["diagnostics"],
        tool_versions=_tool_versions(), notes=notes)
    return EV.write_request(out, record, transcript_events=events,
                            mask_pngs=mask_pngs)


def cmd_segment_image(args) -> int:
    from anastylos_sam import client as CL  # noqa: PLC0415
    _require_live(args)
    key = CL.load_key(args.api_key_stdin)
    path = Path(args.image)
    width, height, fmt = _media_size(path)
    text, prompt = _prompt_from_args(args)
    cost = EV.estimate_cost(media_kind="image", frames=1)
    CL.check_budget(args.spent_usd, cost["usd"], args.budget_usd)
    payload_bytes = path.read_bytes()
    media_part = {"type": "input_image",
                  "image_url": ("data:image/" + fmt + ";base64," +
                                base64.b64encode(payload_bytes).decode())}
    payload = _request_payload(
        text=text, media_part=media_part,
        confidence=not args.no_confidence,
        mask_encoding=args.mask_encoding, direction=None)
    events, meta = CL.post_responses_stream(payload, key)
    media = {"kind": "image", "path": str(path), "sha256": sha256_file(path),
             "bytes": len(payload_bytes), "width": width, "height": height,
             "format": fmt}
    params = {"stream": True, "include_confidence": not args.no_confidence,
              "mask_encoding": args.mask_encoding,
              "propagation_direction": None}
    request_id = EV.new_request_id(slug(args.tag or prompt.get("concept", "img")))
    request_dir = _finalize_request(
        out=Path(args.out), request_id=request_id, created_utc=utc_now_iso(),
        prompt=prompt, media=media, params=params, events=events, meta=meta,
        cost=cost, notes=[])
    if args.ledger:
        CL.append_ledger(Path(args.ledger),
                         {"kind": "segment-image", "request_id": request_id,
                          "model": MODEL_ID, "http": meta["http"],
                          "terminal": meta.get("terminal"),
                          "prompt_kind": prompt["kind"],
                          "media_sha256": media["sha256"], "cost": cost,
                          "latency_s": meta["latency_s"]})
    print(json.dumps({"request_dir": str(request_dir),
                      "records": len(list(request_dir.glob("masks/*.png")))},
                     indent=2))
    return 0


def cmd_segment_video(args) -> int:
    from anastylos_sam import client as CL  # noqa: PLC0415
    _require_live(args)
    key = CL.load_key(args.api_key_stdin)
    clip = Path(args.clip)
    if not _has_faststart(clip):
        _die(f"{clip}: MP4 is not faststart; run cut-clip first")
    probe = _ffprobe(clip)
    stream = probe.get("streams", [{}])[0]
    frames = int(stream.get("nb_frames") or 0)
    text, prompt = _prompt_from_args(args)
    cost = EV.estimate_cost(media_kind="video", frames=frames)
    CL.check_budget(args.spent_usd, cost["usd"], args.budget_usd)
    upload = CL.upload_file(clip, key)
    file_id = (upload.get("object") or {}).get("id")
    if not file_id:
        _die(f"upload returned no file id: {json.dumps(upload)[:500]}")
    payload = _request_payload(
        text=text, media_part={"type": "input_video", "file_id": file_id},
        confidence=not args.no_confidence,
        mask_encoding=args.mask_encoding, direction=args.direction)
    events, meta = CL.post_responses_stream(payload, key)
    meta["file_id"] = file_id
    media = {"kind": "video", "path": str(clip), "sha256": sha256_file(clip),
             "bytes": clip.stat().st_size, "width": stream.get("width"),
             "height": stream.get("height"), "nb_frames": frames,
             "faststart": True, "file_id": file_id}
    params = {"stream": True, "include_confidence": not args.no_confidence,
              "mask_encoding": args.mask_encoding,
              "propagation_direction": args.direction or "both(default)"}
    request_id = EV.new_request_id(slug(args.tag or "vid"))
    notes = []
    clip_meta = clip.with_suffix(".clip.json")
    if clip_meta.is_file():
        notes.append(f"clip provenance: {clip_meta.name}")
        media["clip"] = load_json(clip_meta)
    request_dir = _finalize_request(
        out=Path(args.out), request_id=request_id, created_utc=utc_now_iso(),
        prompt=prompt, media=media, params=params, events=events, meta=meta,
        cost=cost, notes=notes)
    if args.ledger:
        CL.append_ledger(Path(args.ledger),
                         {"kind": "segment-video", "request_id": request_id,
                          "model": MODEL_ID, "http": meta["http"],
                          "terminal": meta.get("terminal"),
                          "prompt_kind": prompt["kind"],
                          "media_sha256": media["sha256"],
                          "frames": frames, "cost": cost,
                          "latency_s": meta["latency_s"]})
    print(json.dumps({"request_dir": str(request_dir), "file_id": file_id,
                      "outcome": "see evidence.json"}, indent=2))
    return 0


def cmd_replay(args) -> int:
    request_dir = Path(args.request_dir)
    check = EV.verify_request(request_dir)
    if not check["ok"]:
        _die(f"verify failed: {check['problems']}")
    record = load_json(request_dir / "evidence.json")
    events = [json.loads(line) for line in
              (request_dir / "transcript.jsonl").read_text(
                  encoding="utf-8").splitlines() if line.strip()]
    parsed = PA.parse_events(events, record["media"]["kind"])
    same_outcome = parsed["outcome"] == record["response"]["outcome"]
    same_count = len(parsed["records"]) == len(record["records"])
    print(json.dumps({"verify": check, "outcome_match": same_outcome,
                      "record_count_match": same_count,
                      "n_records": len(parsed["records"])}, indent=2))
    if args.render:
        args.media = args.media or record["media"]["path"]
        args.request_dirs = [str(request_dir)]
        return cmd_render(args)
    return 0 if (same_outcome and same_count) else 1


def cmd_render(args) -> int:
    from anastylos_sam import render as RE  # noqa: PLC0415
    import cv2  # noqa: PLC0415
    request_dirs = [Path(path) for path in args.request_dirs]
    media = Path(args.media)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    records_by_frame: dict[int, list[dict]] = {}
    png_cache: dict[str, tuple[int, int, bytes]] = {}
    for request_dir in request_dirs:
        record = load_json(request_dir / "evidence.json")
        for item in record.get("records", []):
            records_by_frame.setdefault(item["frame"], []).append(item)

    def raster_of(item: dict):
        ref = (item.get("mask") or {}).get("png", "")
        for request_dir in request_dirs:
            candidate = request_dir / ref
            if candidate.is_file():
                key = str(candidate)
                if key not in png_cache:
                    width, height, raster = PNG.decode_gray8(
                        candidate.read_bytes())
                    png_cache[key] = (width, height, raster)
                return png_cache[key]
        return None

    suffix = media.suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png"):
        image = cv2.imread(str(media))
        if image is None:
            _die(f"cannot decode {media}")
        items: list[dict] = []
        for frame_items in records_by_frame.values():
            items.extend(frame_items)
        overlay = RE.draw_overlay(image, items, raster_of)
        cv2.imwrite(str(out / "overlay.jpg"), overlay,
                    [cv2.IMWRITE_JPEG_QUALITY, 90])
        frames = sorted(records_by_frame,
                        key=lambda f: -1 if f is None else f)
        print(json.dumps({"frames": frames, "out": str(out)}, indent=2))
        return 0
    capture = cv2.VideoCapture(str(media))
    if not capture.isOpened():
        _die(f"cannot open {media}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    every = max(1, args.every)
    overlays = []
    index = 0
    while True:
        ok, frame_img = capture.read()
        if not ok:
            break
        if index % every == 0:
            items = records_by_frame.get(index, [])
            overlays.append(RE.draw_overlay(frame_img, items, raster_of))
            if args.save_frames:
                cv2.imwrite(str(out / f"overlay_f{index:04d}.jpg"),
                            overlays[-1], [cv2.IMWRITE_JPEG_QUALITY, 85])
        index += 1
        if total and index >= total:
            break
    capture.release()
    if args.mp4:
        RE.write_mp4(out / "review.mp4", overlays, fps / every)
    print(json.dumps({"frames_read": index, "frames_with_records":
                      sorted(records_by_frame), "out": str(out)}, indent=2))
    return 0


def cmd_fuse(args) -> int:
    config = load_json(args.config)
    src_evidence = FU.load_request(config["src_request"])
    dst_evidence = FU.load_request(config["dst_request"]) \
        if config.get("dst_request") else None
    hands = [FU.load_request(path) for path in config.get("hands", [])]
    result = FU.run_hypothesis(
        src_evidence=src_evidence, src_id=config["src_id"],
        src_region=config.get("src_region"),
        dst_region=config.get("dst_region"), hands=hands,
        dst_evidence=dst_evidence, dst_id=config.get("dst_id"))
    payload = {"schema": FU.SCHEMA, "config": config, "gates": FU.GATES,
               "created_utc": utc_now_iso(), **result}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out / "fusion.json", payload)
    print(json.dumps({"verdict": result["verdict"],
                      "reasons": result["reasons"],
                      "out": str(out / "fusion.json")}, indent=2))
    return 0


def cmd_eval_seg(args) -> int:
    gt = load_json(args.gt)
    if gt.get("schema") != SC.GT_SCHEMA:
        _die(f"gt schema is {gt.get('schema')!r}, want {SC.GT_SCHEMA!r}")
    if args.require_seal:
        seal = load_json(args.require_seal)
        actual = sha256_file(args.gt)
        want = seal.get("gt_sha256")
        if want is None:
            files = seal.get("files") or {}
            want = files.get(Path(args.gt).name)
        if want is None:
            _die("seal file names no hash for this GT file; "
                 "refusing to score")
        if str(want).lower() != actual.lower():
            _die("GT file changed since sealing; refusing to score")
    per_image: list[dict] = []
    by_sha: dict[str, list[Path]] = {}
    for evidence_path in sorted(Path(args.evidence).rglob("evidence.json")):
        try:
            record = load_json(evidence_path)
        except (OSError, ValueError):
            continue
        sha = ((record.get("media") or {}).get("sha256") or "").lower()
        if sha:
            by_sha.setdefault(sha, []).append(evidence_path.parent)
    for entry in gt["images"]:
        if entry.get("scored_by", "box") != "box":
            per_image.append({"image": entry["image"],
                              "concept": entry.get("concept"),
                              "status": "SKIPPED_MANUAL",
                              "note": "count/region review, not box IoU"})
            continue
        key = (entry.get("sha256") or "").lower()
        want = (entry.get("concept") or "").lower()
        request_dir = None
        for candidate in by_sha.get(key, []):
            got = ((load_json(candidate / "evidence.json").get("prompt")
                    or {}).get("concept") or "").lower()
            if not want or got == want:
                request_dir = candidate
                break
        if request_dir is None:
            per_image.append({"image": entry["image"],
                              "concept": entry.get("concept"),
                              "request": None,
                              "status": "NO_EVIDENCE_FOR_SHA"})
            continue
        record = load_json(request_dir / "evidence.json")
        records = [item for item in record["records"]
                   if item.get("box") is not None]
        score = SC.score_segmentation(records, entry["boxes"])
        per_image.append({"image": entry["image"],
                          "concept": entry.get("concept"),
                          "request": request_dir.name, **score})
    payload = {"schema": "sam-evidence-v1/eval-seg@1",
               "gt": str(args.gt), "gt_sha256": sha256_file(args.gt),
               "created_utc": utc_now_iso(), "per_image": per_image}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out / "eval_seg.json", payload)
    lines = ["# SAM-01A segmentation eval", "",
             f"GT: `{args.gt}` sha256 `{payload['gt_sha256'][:16]}…`", ""]
    for item in per_image:
        if "hits" in item:
            lines.append(
                f"- {item['image']} [{item.get('concept')}]: "
                f"{item['hits']}/{item['n_gt']} hit, "
                f"{item['false_positives']} FP")
        else:
            lines.append(f"- {item['image']} [{item.get('concept')}]: "
                         f"{item.get('status')}")
    (out / "eval_seg.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"images": len(per_image), "out": str(out)}, indent=2))
    return 0


def cmd_report(args) -> int:
    run = Path(args.run_dir)
    total = 0.0
    requests = 0
    rows = []
    for evidence_path in sorted(run.rglob("evidence.json")):
        record = load_json(evidence_path)
        if record.get("schema") != EVIDENCE_SCHEMA:
            continue
        usage = record.get("usage", {})
        total += float(usage.get("usd") or 0.0)
        requests += 1
        rows.append({"request": record["request_id"],
                     "kind": record["prompt"]["kind"],
                     "media": record["media"]["kind"],
                     "records": len(record["records"]),
                     "usd": usage.get("usd")})
    payload = {"schema": "sam-evidence-v1/report@1", "run": str(run),
               "created_utc": utc_now_iso(), "requests": requests,
               "total_usd": round(total, 6),
               "cost_basis": EV.COST_BASIS, "rows": rows}
    print(json.dumps(payload, indent=2))
    if args.out:
        atomic_write_json(Path(args.out), payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key-stdin", action="store_true")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--budget-usd", type=float, default=3.0)
    parser.add_argument("--spent-usd", type=float, default=0.0)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--upload-ok", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("models")
    cut = sub.add_parser("cut-clip")
    cut.add_argument("--video", required=True)
    cut.add_argument("--t-start", type=float, required=True)
    cut.add_argument("--seconds", type=float, required=True)
    cut.add_argument("--out", required=True)
    for name in ("plan-image", "segment-image"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--image", required=True)
        cmd.add_argument("--concept", default=None)
        cmd.add_argument("--objects-json", default=None)
        cmd.add_argument("--mask-encoding", default="one_bit",
                         choices=["one_bit", "lossless"])
        cmd.add_argument("--no-confidence", action="store_true")
        cmd.add_argument("--tag", default=None)
        cmd.add_argument("--out", required=True)
    for name in ("plan-video", "segment-video"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--clip", required=True)
        cmd.add_argument("--concept", default=None)
        cmd.add_argument("--objects-json", default=None)
        cmd.add_argument("--direction", default=None,
                         choices=["forward", "backward", "both"])
        cmd.add_argument("--mask-encoding", default="one_bit",
                         choices=["one_bit", "lossless"])
        cmd.add_argument("--no-confidence", action="store_true")
        cmd.add_argument("--tag", default=None)
        cmd.add_argument("--out", required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("--request-dir", required=True)
    replay.add_argument("--render", action="store_true")
    replay.add_argument("--media", default=None)
    replay.add_argument("--out", default=None)
    replay.add_argument("--every", type=int, default=1)
    replay.add_argument("--save-frames", action="store_true")
    replay.add_argument("--mp4", action="store_true")
    render = sub.add_parser("render")
    render.add_argument("--request-dirs", nargs="+", required=True)
    render.add_argument("--media", required=True)
    render.add_argument("--out", required=True)
    render.add_argument("--every", type=int, default=1)
    render.add_argument("--save-frames", action="store_true")
    render.add_argument("--mp4", action="store_true")
    fuse = sub.add_parser("fuse")
    fuse.add_argument("--config", required=True)
    fuse.add_argument("--out", required=True)
    evs = sub.add_parser("eval-seg")
    evs.add_argument("--gt", required=True)
    evs.add_argument("--evidence", required=True)
    evs.add_argument("--out", required=True)
    evs.add_argument("--require-seal", default=None)
    rep = sub.add_parser("report")
    rep.add_argument("--run-dir", required=True)
    rep.add_argument("--out", default=None)
    return parser


_COMMANDS = {
    "models": cmd_models, "cut-clip": cmd_cut_clip,
    "plan-image": cmd_plan_image, "plan-video": cmd_plan_video,
    "segment-image": cmd_segment_image, "segment-video": cmd_segment_video,
    "replay": cmd_replay, "render": cmd_render, "fuse": cmd_fuse,
    "eval-seg": cmd_eval_seg, "report": cmd_report,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    begun = time.monotonic()
    code = _COMMANDS[args.command](args)
    _ = begun
    return code


if __name__ == "__main__":
    raise SystemExit(main())
