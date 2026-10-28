"""Wire events -> normalized records via the official parser.

Sync wrapper around the vendored async stream adapter. Every failure
mode is explicit: terminal API errors raise, while ``incomplete``
outcomes and empty lanes (zero matches) return as data.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor"))

from meta_sam_parser import (  # noqa: E402
    ResponsesStreamError,
    decode_mask_to_raster,
    image_segmentation_format,
    parse_responses_stream,
    video_segmentation_format,
)


class ParseFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _lane_id(event: dict) -> tuple:
    return (event.get("item_id"), event.get("output_index"),
            event.get("content_index"))


def events_from_completed(data: dict) -> list[dict]:
    """Synthesize lane events from a non-streaming completed response.

    Reads the single output_text content; raises ParseFailed when the
    response is failed/incomplete or carries no usable text lane.
    """
    status = data.get("status")
    if status == "failed":
        raise ParseFailed("response_failed",
                          f"response failed: {data.get('error')}")
    if status == "incomplete":
        raise ParseFailed("response_incomplete",
                          f"response incomplete: {data.get('incomplete_details')}")
    texts: list[str] = []
    for item in data.get("output", []) or []:
        for part in (item.get("content") or []) if isinstance(item, dict) else []:
            if isinstance(part, dict) and part.get("type") == "output_text" \
                    and isinstance(part.get("text"), str):
                texts.append(part["text"])
    if len(texts) != 1:
        raise ParseFailed("lane_missing",
                          f"expected exactly one output_text lane, found {len(texts)}")
    lane = {"item_id": "synth-0", "output_index": 0, "content_index": 0}
    events = [{**lane, "type": "response.output_text.delta", "delta": line + "\n"}
              for line in texts[0].splitlines()]
    events.append({**lane, "type": "response.output_text.done",
                   "text": texts[0]})
    events.append({"type": "response.completed",
                   "response": {"id": data.get("id"), "status": status}})
    return events


async def _collect(events: list[dict], media: str) -> Any:
    async def source():
        for event in events:
            yield event

    fmt = image_segmentation_format() if media == "image" else video_segmentation_format()
    stream = parse_responses_stream(source(), fmt)
    async for _snapshot in stream:
        pass
    return await stream.final_result()


def _int_or_float(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _rle_from_raster(raster: bytes, width: int, height: int) -> dict[str, Any]:
    """COCO RLE ``{"size", "counts"}`` for an already-decoded raster.

    ``raster`` is row-major 0/1 bytes exactly as
    ``decode_mask_to_raster`` returns. The ``counts`` string is
    byte-identical to the vendored ``decode_mask_to_rle`` output for
    the same mask (column-major runs with a leading zero run when the
    first pixel is set, alternate-run deltas, 5-bit LE varints), but
    costs one linear pass instead of a second range-decode.
    """
    area = width * height
    if len(raster) != area:
        raise ValueError(
            f"raster has {len(raster)} bytes, want {area}")
    runs: list[int] = []
    run = 0
    first = True
    value = 0
    for col in range(width):
        for pixel in raster[col::width]:
            bit = 1 if pixel else 0
            if first:
                if bit:
                    runs.append(0)
                first = False
                value = bit
                run = 1
            elif bit == value:
                run += 1
            else:
                runs.append(run)
                value = bit
                run = 1
    runs.append(run)
    out: list[str] = []
    for i, count in enumerate(runs):
        delta = count - runs[i - 2] if i > 2 else count
        delta &= 0xFFFFFFFF
        if delta >= 1 << 31:
            delta -= 1 << 32
        more = True
        while more:
            character = delta & 0x1F
            delta >>= 5
            more = (delta != -1) if (character & 0x10) else (delta != 0)
            if more:
                character |= 0x20
            out.append(chr(character + 48))
    return {"size": [height, width], "counts": "".join(out)}


def parse_events(events: list[dict], media: str) -> dict[str, Any]:
    """Run the official parser; return normalized records + diagnostics."""
    if media not in ("image", "video"):
        raise ValueError(f"media must be image|video, got {media!r}")
    try:
        result = asyncio.run(_collect(events, media))
    except ResponsesStreamError as exc:
        raise ParseFailed(type(exc).__name__, str(exc)[:500]) from exc
    outcome = result.outcome
    status = outcome.status
    reason = getattr(outcome, "reason", None)
    detail = getattr(outcome, "detail", None)
    boxes: dict[tuple, dict] = {}
    records: list[dict[str, Any]] = []
    for record in result.records:
        kind = record.kind
        if kind == "box":
            boxes[(record.frame.frame_index if record.frame else None,
                   record.object_id, record.order)] = {
                "box": {key: _int_or_float(getattr(record, key))
                        for key in ("left", "top", "right", "bottom")},
                "confidence": record.confidence,
            }
        elif kind == "mask":
            frame = record.frame.frame_index if record.frame else None
            raster = bytes(decode_mask_to_raster(record.mask))
            key = (frame, record.object_id, record.order - 1)
            box_info = boxes.get(key, {})
            records.append({
                "frame": frame,
                "object_id": str(record.object_id),
                "box": box_info.get("box"),
                "confidence": box_info.get("confidence"),
                "mask_confidence": record.confidence,
                "mask": {
                    "height": record.mask.height,
                    "width": record.mask.width,
                    "encoding": record.mask.encoding,
                    "raster": raster,
                    "rle": _rle_from_raster(
                        raster, record.mask.width, record.mask.height),
                },
            })
    records.sort(key=lambda item: ((item["frame"] is None, item["frame"] or 0),
                                     item["object_id"]))
    diagnostics = [{"severity": item.severity, "code": item.code,
                    "message": item.message, "line": item.line}
                   for item in result.diagnostics]
    return {"outcome": {"status": status, "reason": reason, "detail": detail},
            "records": records, "diagnostics": diagnostics,
            "raw_output_chars": len(result.raw_output)}
