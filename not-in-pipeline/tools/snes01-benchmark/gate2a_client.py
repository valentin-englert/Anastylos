"""Gate 2A minimal Meta Model API client (stdlib only, no retries).

Reads the API key ONLY from the MODEL_API_KEY environment variable or
from stdin via --api-key-stdin (pipe it; never paste it on a command
line). The key is used in-memory for the Authorization header and is
NEVER printed, logged, or written to any output file.

Subcommands:
  models   GET /v1/models (list available models; no inference cost)
  text     minimal text completion probe (tiny max_tokens)
  vision   ONE image-input request (data-URL JPEG, tiny max_tokens)

Every call appends a redacted ledger line (no key, no prompt text, no
image bytes): timestamp, model, endpoint, HTTP status, request id,
token usage, latency. Full prompts/responses are the caller's
responsibility (see gate2a_run.py) and never include credentials.

Pricing is NOT known to this client: it records raw token usage and
reports cost as NOT_KNOWN unless --price-in/--price-out (USD per
million tokens) are supplied, in which case estimates are labeled
ESTIMATED_FROM_PUBLIC_RATES and must cite their source.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
import typing
import urllib.request
import urllib.error
from pathlib import Path

BASE_URL = "https://api.meta.ai/v1"


def _die(message: str) -> "typing.NoReturn":
    print(f"gate2a_client: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def load_key(args) -> str:
    import os
    if args.api_key_stdin:
        data = sys.stdin.read().strip()
        if not data:
            _die("no key received on stdin")
        return data
    key = os.environ.get("MODEL_API_KEY", "").strip()
    if not key:
        _die("set MODEL_API_KEY or use --api-key-stdin")
    return key


def _request(method: str, url: str, key: str, payload: dict | None,
             timeout_s: float = 120.0) -> tuple[int, dict, float]:
    body = None
    headers = {"Authorization": "Bearer " + key}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method=method)
    begun = time.monotonic()
    try:
        with urllib.request.urlopen(request,
                                    timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, json.loads(raw), \
                time.monotonic() - begun
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")[:2000]
        try:
            detail = json.loads(raw)
        except ValueError:
            detail = {"raw": raw}
        raise ApiError(exc.code, detail, time.monotonic() - begun)


class ApiError(Exception):
    def __init__(self, status: int, detail: dict, latency_s: float):
        super().__init__(f"HTTP {status}: {json.dumps(detail)[:500]}")
        self.status = status
        self.detail = detail
        self.latency_s = latency_s


def append_ledger(path: Path, entry: dict):
    entry = {"ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                     time.gmtime()),
             **entry}
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def estimate_cost(tokens_in: int, tokens_out: int,
                  price_in: float | None,
                  price_out: float | None) -> dict:
    if price_in is None or price_out is None:
        return {"usd": None, "basis": "NOT_KNOWN"}
    usd = tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out
    return {"usd": round(usd, 6), "basis": "ESTIMATED_FROM_PUBLIC_RATES"}


def cmd_models(args, key: str) -> int:
    status, data, latency = _request("GET", BASE_URL + "/models", key,
                                     None)
    models = data.get("data", data)
    print(json.dumps({"http": status, "latency_s": round(latency, 2),
                      "models": models}, indent=2)[:6000])
    return 0


def _completion(args, key: str, messages: list) -> dict:
    payload = {"model": args.model, "messages": messages,
               "max_tokens": args.max_tokens}
    if getattr(args, "json_mode", False):
        payload["response_format"] = {"type": "json_object"}
    status, data, latency = _request("POST",
                                     BASE_URL + "/chat/completions",
                                     key, payload)
    usage = data.get("usage", {}) or {}
    details = usage.get("completion_tokens_details", {}) or {}
    choice = {}
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError):
        choice = {}
    content = (choice.get("message") or {}).get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict))
    return {"http": status, "latency_s": round(latency, 2),
            "request_id": data.get("id"),
            "model": data.get("model", args.model),
            "finish_reason": choice.get("finish_reason"),
            "tokens_in": usage.get("prompt_tokens"),
            "tokens_out": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "text": content, "raw": data}


def _maybe_save_raw(args, data: dict):
    if getattr(args, "save_raw", None):
        Path(args.save_raw).write_text(json.dumps(data, indent=2),
                                       encoding="utf-8")


def cmd_text(args, key: str) -> int:
    try:
        result = _completion(
            args, key, [{"role": "user", "content": args.prompt}])
    except ApiError as exc:
        print(json.dumps({"http": exc.status, "detail": exc.detail,
                          "latency_s": round(exc.latency_s, 2)},
                         indent=2))
        return 3
    _maybe_save_raw(args, result["raw"])
    cost = estimate_cost(result["tokens_in"] or 0, result["tokens_out"] or 0,
                         args.price_in, args.price_out)
    print(json.dumps({**result, "cost": cost,
                      "raw": "[withheld in probe output]"}, indent=2))
    if args.ledger:
        append_ledger(Path(args.ledger),
                      {"kind": "text-probe", "http": result["http"],
                       "request_id": result["request_id"],
                       "model": result["model"],
                       "tokens_in": result["tokens_in"],
                       "tokens_out": result["tokens_out"],
                       "latency_s": result["latency_s"], "cost": cost})
    return 0


def encode_image(path: Path, max_side: int = 512) -> tuple[str, dict]:
    """Downscale (aspect kept) + JPEG encode + base64 data URL.
    Pure stdlib + OpenCV if present, else raw-file fallback with a
    loud note (no silent quality claims)."""
    data = path.read_bytes()
    info = {"source": str(path), "source_bytes": len(data),
            "resized": False}
    try:
        import cv2
        import numpy as np
        image = cv2.imdecode(np.frombuffer(data, np.uint8),
                             cv2.IMREAD_COLOR)
        if image is None:
            _die(f"cannot decode image {path}")
        height, width = image.shape[:2]
        scale = min(1.0, max_side / max(height, width))
        if scale < 1.0:
            image = cv2.resize(image, (int(width * scale),
                                       int(height * scale)),
                               interpolation=cv2.INTER_AREA)
            info["resized"] = True
        info["size_sent"] = [int(image.shape[1]), int(image.shape[0])]
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY,
                                               85])
        if not ok:
            _die("JPEG encode failed")
        payload = bytes(buf)
    except ImportError:
        info["note"] = "opencv missing: sent original bytes unchanged"
        payload = data
    info["bytes_sent"] = len(payload)
    url = "data:image/jpeg;base64," + base64.b64encode(payload).decode()
    return url, info


def cmd_vision(args, key: str) -> int:
    images = []
    for spec in args.image:
        url, info = encode_image(Path(spec), args.max_side)
        images.append({"url": url, "info": info})
    content: list = [{"type": "text", "text": args.prompt}]
    for item in images:
        content.append({"type": "image_url",
                        "image_url": {"url": item["url"]}})
    try:
        result = _completion(args, key,
                             [{"role": "user", "content": content}])
    except ApiError as exc:
        print(json.dumps({"http": exc.status, "detail": exc.detail,
                          "latency_s": round(exc.latency_s, 2)},
                         indent=2))
        return 3
    _maybe_save_raw(args, result["raw"])
    cost = estimate_cost(result["tokens_in"] or 0, result["tokens_out"] or 0,
                         args.price_in, args.price_out)
    print(json.dumps(
        {**result, "cost": cost, "images": [i["info"] for i in images],
         "raw": "[saved to --save-raw]" if getattr(
             args, "save_raw", None) else "[withheld]"}, indent=2))
    if args.ledger:
        append_ledger(Path(args.ledger),
                      {"kind": "vision", "http": result["http"],
                       "request_id": result["request_id"],
                       "model": result["model"],
                       "finish_reason": result["finish_reason"],
                       "tokens_in": result["tokens_in"],
                       "tokens_out": result["tokens_out"],
                       "reasoning_tokens": result["reasoning_tokens"],
                       "latency_s": result["latency_s"], "cost": cost,
                       "images": [i["info"] for i in images]})
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gate 2A API client.")
    parser.add_argument("--api-key-stdin", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("models")
    text = sub.add_parser("text")
    text.add_argument("--model", required=True)
    text.add_argument("--prompt", required=True)
    text.add_argument("--max-tokens", type=int, default=50)
    text.add_argument("--save-raw", default=None)
    text.add_argument("--ledger", default=None)
    text.add_argument("--price-in", type=float, default=None)
    text.add_argument("--price-out", type=float, default=None)
    vision = sub.add_parser("vision")
    vision.add_argument("--model", required=True)
    vision.add_argument("--prompt", required=True)
    vision.add_argument("--image", action="append", required=True)
    vision.add_argument("--max-side", type=int, default=512)
    vision.add_argument("--max-tokens", type=int, default=100)
    vision.add_argument("--json-mode", action="store_true")
    vision.add_argument("--save-raw", default=None)
    vision.add_argument("--ledger", default=None)
    vision.add_argument("--price-in", type=float, default=None)
    vision.add_argument("--price-out", type=float, default=None)
    args = parser.parse_args(argv)
    key = load_key(args)
    if args.command == "models":
        return cmd_models(args, key)
    if args.command == "text":
        return cmd_text(args, key)
    return cmd_vision(args, key)


if __name__ == "__main__":
    sys.exit(main())
