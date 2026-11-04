"""Single-owner GStreamer acquisition with separate evidence and display branches."""

from __future__ import annotations

import re
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from anastylos_mic_node.session import MAX_FRAME_BYTES, validate_jpeg
from anastylos_mic_node.v4l2 import CaptureDevice

STATE_TIMEOUT_SECONDS = 5
FIRST_FRAME_TIMEOUT_SECONDS = 15
MAX_FRAME_AGE_SECONDS = 4
LOG = logging.getLogger("anastylos.mic_node.pipeline")


class PipelineError(RuntimeError):
    """The capture pipeline could not stay healthy."""


class CaptureUnavailable(PipelineError):
    """Capture cannot start because a required source or runtime is unavailable."""


class PreviewError(PipelineError):
    """The HDMI branch failed while the capture branch may remain usable."""


@dataclass(frozen=True)
class CapturedFrame:
    data: bytes
    captured_utc: datetime
    sequence: int
    device: CaptureDevice


class LatestFrameSlot:
    """Bounded, thread-safe handoff for the newest complete MJPEG frame."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._frame: CapturedFrame | None = None
        self._received_monotonic = 0.0

    def put(self, frame: CapturedFrame) -> None:
        with self._condition:
            self._frame = frame
            self._received_monotonic = time.monotonic()
            self._condition.notify_all()

    def get(self, *, max_age_seconds: float | None = None) -> CapturedFrame | None:
        with self._condition:
            if self._frame is None:
                return None
            if (max_age_seconds is not None
                    and time.monotonic() - self._received_monotonic > max_age_seconds):
                return None
            return self._frame

    def wait(self, timeout_seconds: float, *, max_age_seconds: float | None = None) -> CapturedFrame | None:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while True:
                if self._frame is not None and (
                    max_age_seconds is None
                    or time.monotonic() - self._received_monotonic <= max_age_seconds
                ):
                    return self._frame
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def clear(self) -> None:
        with self._condition:
            self._frame = None
            self._received_monotonic = 0.0


def build_pipeline_description(device: CaptureDevice, *, preview: bool = True,
                               display_size: tuple[int, int] = (1920, 1080)) -> str:
    """Build the owner-authorized tee pipeline using probed device settings."""
    if not re.fullmatch(r"/dev/video\d+", device.path):
        raise ValueError("capture device must be a normalized /dev/videoN path")
    mode = device.mode
    if not (1 <= mode.width <= 4096 and 1 <= mode.height <= 4096 and 1 <= mode.fps <= 120):
        raise ValueError("capture mode is outside the supported bounds")

    description = (
        f"v4l2src device={device.path} io-mode=mmap ! "
        f"image/jpeg,width={mode.width},height={mode.height},framerate={mode.fps}/1 ! "
        "tee name=frames "
        "frames. ! queue leaky=downstream max-size-buffers=2 ! "
        "appsink name=evidence emit-signals=true max-buffers=2 drop=true sync=false "
        "caps=image/jpeg "
    )
    if preview:
        width, height = display_size
        if not (1 <= width <= 4096 and 1 <= height <= 4096):
            raise ValueError("display mode is outside the supported bounds")
        description += (
            "frames. ! queue leaky=downstream max-size-buffers=3 ! "
            "jpegdec ! videoconvert ! videoscale add-borders=true ! "
            f"video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1 ! "
            'textoverlay name=brand text="ANASTYLOS" halignment=left valignment=top '
            'shaded-background=true font-desc="Sans, 16" ! '
            'textoverlay name=status text="INITIALIZING CAMERA" halignment=left '
            'valignment=bottom auto-resize=false shaded-background=true font-desc="Mono, 20" ! '
            "kmssink sync=false force-modesetting=true name=preview"
        )
    return description


class GStreamerAcquisition:
    """Own the selected V4L2 device and publish original MJPEG buffers."""

    def __init__(self, device: CaptureDevice, slot: LatestFrameSlot | None = None) -> None:
        self.device = device
        self.display_target = None
        self.slot = slot or LatestFrameSlot()
        self.preview_element = None
        self.preview_sink = None
        self._sequence = 0
        self._pipeline = None

    def set_status_text(self, text: str) -> None:
        if self.preview_element is not None:
            self.preview_element.set_property("text", text[:120])

    def preview_stats(self) -> dict | None:
        if self.preview_sink is None:
            return None
        try:
            stats = self.preview_sink.get_property("stats")
            return {"rendered": int(stats.get_value("rendered")),
                    "dropped": int(stats.get_value("dropped"))}
        except Exception as exc:
            LOG.warning("cannot read HDMI sink statistics: %s", exc)
            return None

    def run(self, stop_event: threading.Event, *, preview: bool) -> None:
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except (ImportError, ValueError) as exc:
            raise CaptureUnavailable(
                "PyGObject GStreamer bindings are unavailable; install the Pi OS "
                "packages python3-gi and python3-gst-1.0"
            ) from exc

        Gst.init(None)
        display_size = ((self.display_target.width, self.display_target.height)
                        if self.display_target is not None else (1920, 1080))
        description = build_pipeline_description(self.device, preview=preview, display_size=display_size)
        try:
            pipeline = Gst.parse_launch(description)
        except Exception as exc:
            details = str(exc).lower()
            if preview and any(name in details for name in
                               ("kmssink", "textoverlay", "gdkpixbuf", "jpegdec", "videoconvert", "videoscale")):
                raise PreviewError(f"cannot build HDMI preview branch: {exc}") from exc
            raise CaptureUnavailable(f"cannot build MJPEG acquisition pipeline: {exc}") from exc

        self._pipeline = pipeline
        sink = pipeline.get_by_name("evidence")
        if sink is None:
            pipeline.set_state(Gst.State.NULL)
            raise CaptureUnavailable("GStreamer pipeline did not create the evidence appsink")
        bus = pipeline.get_bus()
        try:
            sink.connect("new-sample", self._on_new_sample, Gst)
            self.preview_element = pipeline.get_by_name("status") if preview else None
            self.preview_sink = pipeline.get_by_name("preview") if preview else None
            state_change = pipeline.set_state(Gst.State.PLAYING)
            if state_change == Gst.StateChangeReturn.FAILURE:
                raise PreviewError("GStreamer refused the HDMI pipeline") if preview else CaptureUnavailable(
                    "GStreamer refused the capture pipeline"
                )
            result, _, _ = pipeline.get_state(STATE_TIMEOUT_SECONDS * Gst.SECOND)
            if result == Gst.StateChangeReturn.FAILURE:
                raise PreviewError("GStreamer did not enter PLAYING") if preview else CaptureUnavailable(
                    "GStreamer capture pipeline did not enter PLAYING"
                )

            started = time.monotonic()
            last_frame_at: float | None = None
            while not stop_event.is_set():
                message = bus.timed_pop_filtered(
                    250 * Gst.MSECOND,
                    Gst.MessageType.ERROR | Gst.MessageType.EOS,
                )
                if message is not None:
                    if message.type == Gst.MessageType.EOS:
                        raise PipelineError("camera stream ended unexpectedly")
                    error, debug = message.parse_error()
                    source_name = message.src.get_name() if message.src else ""
                    detail = f"{error.message} ({debug})" if debug else str(error)
                    if preview and self._is_preview_source(source_name):
                        raise PreviewError(detail)
                    raise PipelineError(detail)

                frame = self.slot.get(max_age_seconds=MAX_FRAME_AGE_SECONDS)
                if frame is None and time.monotonic() - started > FIRST_FRAME_TIMEOUT_SECONDS:
                    raise CaptureUnavailable("camera produced no complete MJPEG frame before timeout")
                if frame is not None:
                    last_frame_at = time.monotonic()
                elif last_frame_at is not None and time.monotonic() - last_frame_at > MAX_FRAME_AGE_SECONDS:
                    raise PipelineError("camera stopped producing fresh MJPEG frames")
        finally:
            self.preview_element = None
            self.preview_sink = None
            self._pipeline = None
            pipeline.set_state(Gst.State.NULL)

    @staticmethod
    def _is_preview_source(name: str) -> bool:
        normalized = name.lower()
        return any(element in normalized for element in
                   ("kmssink", "jpegdec", "videoconvert", "textoverlay"))

    def _on_new_sample(self, appsink, gst) -> int:
        try:
            sample = appsink.emit("pull-sample")
            if sample is None:
                return gst.FlowReturn.OK
            buffer = sample.get_buffer()
            if buffer is None:
                return gst.FlowReturn.OK
            size = int(buffer.get_size())
            if size < 4 or size > MAX_FRAME_BYTES:
                return gst.FlowReturn.OK
            payload = bytes(buffer.extract_dup(0, size))
            try:
                validate_jpeg(payload)
            except ValueError:
                return gst.FlowReturn.OK
            self._sequence += 1
            self.slot.put(CapturedFrame(
                data=payload,
                captured_utc=datetime.now(timezone.utc),
                sequence=self._sequence,
                device=self.device,
            ))
        except Exception as exc:
            # A malformed sample is dropped; bus errors supervise actual stream failures.
            LOG.warning("dropping unreadable GStreamer MJPEG sample: %s", exc)
            return gst.FlowReturn.OK
        return gst.FlowReturn.OK
