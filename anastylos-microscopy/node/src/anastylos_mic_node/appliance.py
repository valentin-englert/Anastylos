"""Systemd appliance supervisor for the shared capture/preview pipeline."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import re

from anastylos_mic_node.control import ControlServer
from anastylos_mic_node.display import StatusDisplay, probe_display
from anastylos_mic_node.pipeline import (
    CaptureUnavailable,
    GStreamerAcquisition,
    LatestFrameSlot,
    PipelineError,
    PreviewError,
)
from anastylos_mic_node.v4l2 import CaptureDevice, V4L2Error, discover_ugreen

LOG = logging.getLogger("anastylos.mic_node")
RECOVERY_START_SECONDS = 1.0
RECOVERY_MAX_SECONDS = 30.0
PREVIEW_RETRY_SECONDS = 30.0
STATUS_WRITE_INTERVAL_SECONDS = 0.5


class ApplianceService:
    """Own the camera for preview and evidence, and supervise recovery."""

    def __init__(
        self,
        *,
        socket_path: str | Path = "/run/anastylos-mic-node/control.sock",
        status_path: str | Path = "/run/anastylos-mic-node/status.json",
        logo_path: str | Path = "/opt/anastylos-microscope/node/assets/anastylos_logo.png",
        device_discovery=discover_ugreen,
        display_probe=probe_display,
        display_factory=StatusDisplay,
        engine_factory=GStreamerAcquisition,
        recovery_start: float = RECOVERY_START_SECONDS,
        recovery_max: float = RECOVERY_MAX_SECONDS,
        preview_retry: float = PREVIEW_RETRY_SECONDS,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.status_path = Path(status_path)
        self.logo_path = Path(logo_path)
        self.device_discovery = device_discovery
        self.display_probe = display_probe
        self.display = display_factory(self.logo_path)
        self.engine_factory = engine_factory
        self.recovery_start = recovery_start
        self.recovery_max = recovery_max
        self.preview_retry = preview_retry
        self.stop_event = threading.Event()
        self.frames = LatestFrameSlot()
        self._status_lock = threading.Lock()
        self._analysis_lock = threading.Lock()
        self._analysis = None
        self._status = {
            "state": "booting",
            "camera": None,
            "display": {"connected": False, "preview_available": False},
            "capture_available": False,
            "frames_received": 0,
            "preview_stats": None,
            "reconnect_count": 0,
            "last_error": None,
            "updated_utc": None,
        }
        self.control = ControlServer(self.socket_path, self.status, self._fresh_frame, self._receive_analysis)

    def request_stop(self) -> None:
        self.stop_event.set()

    def status(self) -> dict:
        with self._status_lock:
            return json.loads(json.dumps(self._status))

    def _fresh_frame(self, timeout: float):
        deadline = time.monotonic() + timeout
        while not self.stop_event.is_set():
            frame = self.frames.get(max_age_seconds=4)
            if frame is not None:
                return frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.frames.wait(min(remaining, 0.25), max_age_seconds=4)
        return None

    def _receive_analysis(self, request: dict) -> None:
        if request.get("clear") is True:
            with self._analysis_lock:
                self._analysis = None
            return
        sequence = request.get("sequence")
        stamp = request.get("captured_utc")
        state = request.get("state")
        candidate = request.get("candidate", "")
        if (type(sequence) is not int or sequence < 0 or state not in
                ("processing", "unknown", "ambiguous", "candidate", "unavailable", "reader_error")
                or not isinstance(stamp, str) or not isinstance(candidate, str)
                or len(candidate) > 80 or not re.fullmatch(r"[A-Za-z0-9 .+%()/?_-]*", candidate)):
            raise ValueError("invalid analysis result")
        try:
            captured = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - captured).total_seconds()
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid analysis timestamp") from exc
        frame = self.frames.get(max_age_seconds=1)
        if (frame is None or sequence > frame.sequence or not 0 <= age <= 1.5
                or not 0 <= (frame.captured_utc - captured).total_seconds() <= 1.5):
            raise ValueError("analysis frame is no longer fresh")
        with self._analysis_lock:
            self._analysis = (time.monotonic(), age, state, candidate)

    def _analysis_text(self) -> str:
        with self._analysis_lock:
            result = self._analysis
        if result is None or time.monotonic() - result[0] > 1:
            return "CV RESISTOR EXPERIMENTAL | OFFLINE / WAITING"
        received, age, state, candidate = result
        freshness = age + time.monotonic() - received
        label = f" | {candidate}" if candidate else ""
        return f"CV RESISTOR EXPERIMENTAL | {state.upper()}{label} | {freshness:.1f}s"

    def _update_status(self, **changes) -> dict:
        with self._status_lock:
            self._status.update(changes)
            self._status["updated_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            snapshot = json.loads(json.dumps(self._status))
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.status_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.status_path)
        except OSError as exc:
            LOG.warning("cannot update status file %s: %s", self.status_path, exc)
        return snapshot

    @staticmethod
    def _camera_status(device: CaptureDevice | None) -> dict | None:
        if device is None:
            return None
        return {
            "device": device.path,
            "usb_vendor_id": device.usb_vendor_id,
            "usb_product_id": device.usb_product_id,
            "capture_format": "MJPG",
            "resolution": [device.mode.width, device.mode.height],
            "fps": device.mode.fps,
        }

    @staticmethod
    def _display_status(target, *, preview_available: bool) -> dict:
        return {
            "connected": target is not None,
            "connector": target.connector if target else None,
            "resolution": [target.width, target.height] if target else None,
            "preview_available": preview_available,
        }

    def run(self) -> None:
        self._update_status(state="booting", last_error=None)
        self.control.start()
        backoff = self.recovery_start
        preview_retry_at = 0.0
        current_stop: threading.Event | None = None
        current_thread: threading.Thread | None = None
        try:
            while not self.stop_event.is_set():
                target = self.display_probe()
                splash_text = (
                    "INITIALIZING CAMERA"
                    if time.monotonic() >= preview_retry_at
                    else "CAPTURE AVAILABLE - PREVIEW RECOVERING"
                )
                self.display.show(splash_text, target)
                try:
                    device = self.device_discovery()
                except V4L2Error as exc:
                    self._update_status(
                        state="camera_disconnected",
                        camera=None,
                        display=self._display_status(target, preview_available=False),
                        capture_available=False,
                        last_error=str(exc),
                    )
                    self.display.show("CAMERA DISCONNECTED", target)
                    self._wait(backoff)
                    backoff = min(backoff * 2, self.recovery_max)
                    continue

                backoff = self.recovery_start
                self.frames.clear()
                with self._analysis_lock:
                    self._analysis = None
                use_preview = target is not None and time.monotonic() >= preview_retry_at
                engine = self.engine_factory(device, slot=self.frames)
                engine.display_target = target
                current_stop = threading.Event()
                outcome: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)
                current_thread = threading.Thread(
                    target=self._run_engine,
                    args=(engine, current_stop, use_preview, outcome),
                    name="mic-node-gstreamer",
                    daemon=True,
                )
                self._update_status(
                    state="initializing_camera",
                    camera=self._camera_status(device),
                    display=self._display_status(target, preview_available=use_preview),
                    capture_available=False,
                    frames_received=0,
                    preview_stats=None,
                    last_error=None,
                )
                if use_preview:
                    self.display.stop()
                current_thread.start()
                restart_reason = None
                last_display_check = 0.0
                while not self.stop_event.is_set() and current_thread.is_alive():
                    frame = self.frames.get(max_age_seconds=4)
                    if frame is not None:
                        self._update_status(
                            state="ready",
                            capture_available=True,
                            frames_received=frame.sequence,
                            preview_stats=engine.preview_stats() if hasattr(engine, "preview_stats") else None,
                            last_error=None,
                        )
                        engine.set_status_text(self._analysis_text())
                        if not use_preview:
                            self.display.show("CAPTURE READY", self.display_probe())
                    else:
                        self._update_status(
                            state="initializing_camera",
                            capture_available=False,
                        )

                    now = time.monotonic()
                    if now - last_display_check >= 2:
                        last_display_check = now
                        fresh_target = self.display_probe()
                        if use_preview and fresh_target != target:
                            restart_reason = "display_disconnected"
                            self._update_status(
                                state="ready" if frame else "initializing_camera",
                                display=self._display_status(None, preview_available=False),
                                capture_available=frame is not None,
                            )
                            current_stop.set()
                            break
                        if not use_preview and fresh_target is not None and now >= preview_retry_at:
                            restart_reason = "retry_preview"
                            current_stop.set()
                            break
                    self._write_status_snapshot()
                    self.stop_event.wait(STATUS_WRITE_INTERVAL_SECONDS)

                if current_stop is not None:
                    current_stop.set()
                if current_thread is not None:
                    current_thread.join(timeout=8)
                if self.stop_event.is_set():
                    break
                if restart_reason == "display_disconnected":
                    preview_retry_at = time.monotonic() + self.preview_retry
                    self._update_status(
                        state="ready" if self._fresh_frame(0) else "initializing_camera",
                        display=self._display_status(None, preview_available=False),
                    )
                    continue
                if restart_reason == "retry_preview":
                    preview_retry_at = 0
                    continue

                error = outcome.get_nowait() if not outcome.empty() else None
                if isinstance(error, PreviewError) and use_preview:
                    preview_retry_at = time.monotonic() + self.preview_retry
                    target = self.display_probe()
                    frame = self.frames.get(max_age_seconds=4)
                    self._update_status(
                        state="ready" if frame else "capture_unavailable",
                        display=self._display_status(target, preview_available=False),
                        capture_available=frame is not None,
                        last_error=f"HDMI preview unavailable; capture will continue: {error}",
                    )
                    self.display.show("PREVIEW UNAVAILABLE", target)
                    continue
                if error is None:
                    if not self.stop_event.is_set():
                        LOG.warning("camera pipeline stopped without a shutdown request")
                        error = PipelineError("camera pipeline stopped unexpectedly")
                    else:
                        break
                state = "capture_unavailable" if isinstance(error, CaptureUnavailable) else (
                    "recovering" if isinstance(error, PipelineError) else "capture_unavailable"
                )
                self._update_status(
                    state=state,
                    display=self._display_status(self.display_probe(), preview_available=False),
                    capture_available=False,
                    reconnect_count=self._status["reconnect_count"] + 1,
                    last_error=str(error),
                )
                self.display.show("RECOVERING", self.display_probe())
                self._wait(backoff)
                backoff = min(backoff * 2, self.recovery_max)
        finally:
            self.stop_event.set()
            if current_stop is not None:
                current_stop.set()
            if current_thread is not None:
                current_thread.join(timeout=8)
            self.display.show("SHUTDOWN", self.display_probe())
            self.stop_event.wait(0.5)
            self.display.stop()
            self._update_status(state="shutdown", capture_available=False)
            self.control.stop()

    def _run_engine(self, engine, stop_event: threading.Event, preview: bool,
                    outcome: queue.Queue[BaseException | None]) -> None:
        try:
            engine.run(stop_event, preview=preview)
        except Exception as exc:
            outcome.put(exc)
        else:
            outcome.put(None)

    def _wait(self, seconds: float) -> None:
        self.stop_event.wait(max(0, seconds))

    def _write_status_snapshot(self) -> None:
        snapshot = self.status()
        try:
            temporary = self.status_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.status_path)
        except OSError as exc:
            LOG.warning("cannot refresh status file %s: %s", self.status_path, exc)
