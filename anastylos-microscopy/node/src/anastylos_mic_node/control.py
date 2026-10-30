"""Bounded local Unix-socket control protocol for status and evidence capture."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from anastylos_mic_node.pipeline import CapturedFrame
from anastylos_mic_node.session import MAX_FRAME_BYTES, validate_jpeg

MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_HEADER_BYTES = 8192
FRAME_WAIT_SECONDS = 5


class ControlError(RuntimeError):
    """The appliance's local control socket rejected or could not serve a request."""


def _read_line(connection: socket.socket, limit: int) -> bytes:
    data = bytearray()
    while len(data) <= limit:
        chunk = connection.recv(1)
        if not chunk:
            break
        if chunk == b"\n":
            return bytes(data)
        data.extend(chunk)
    raise ControlError("control message is incomplete or exceeds its size limit")


def _send_json(connection: socket.socket, value: dict) -> None:
    connection.sendall(json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n")


def _receive_exact(connection: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(min(1024 * 1024, length - len(data)))
        if not chunk:
            raise ControlError("capture response ended before the complete JPEG payload arrived")
        data.extend(chunk)
    return bytes(data)


class ControlServer:
    """Serve one bounded request per local Unix-socket connection."""

    def __init__(self, socket_path: str | os.PathLike[str], status_provider: Callable[[], dict],
                 frame_provider: Callable[[float], CapturedFrame | None],
                 analysis_provider: Callable[[dict], None] | None = None) -> None:
        self.socket_path = Path(socket_path)
        self.status_provider = status_provider
        self.frame_provider = frame_provider
        self.analysis_provider = analysis_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._ready_event = threading.Event()
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = self.socket_path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(existing.st_mode):
                raise ControlError(f"refusing to replace non-socket path: {self.socket_path}")
            self.socket_path.unlink()
        self._stop.clear()
        self._ready_event.clear()
        self.last_error = None
        self._thread = threading.Thread(target=self._serve, name="mic-node-control", daemon=True)
        self._thread.start()
        if not self._ready_event.wait(timeout=2):
            raise ControlError(self.last_error or "control socket did not start")
        if self.last_error:
            raise ControlError(self.last_error)

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        try:
            if stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def _serve(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket = server
        try:
            server.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o660)
            server.listen(4)
            server.settimeout(0.5)
            self._ready_event.set()
            while not self._stop.is_set():
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if not self._stop.is_set():
                        raise
                    return
                with connection:
                    connection.settimeout(10)
                    self._handle(connection)
        except (OSError, ControlError) as exc:
            self.last_error = str(exc)
            self._ready_event.set()
        finally:
            try:
                server.close()
            except OSError:
                pass
            self._socket = None
            try:
                if stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                    self.socket_path.unlink()
            except FileNotFoundError:
                pass

    def _handle(self, connection: socket.socket) -> None:
        try:
            request = json.loads(_read_line(connection, MAX_REQUEST_BYTES))
            if not isinstance(request, dict):
                raise ControlError("request must be a JSON object")
            operation = request.get("operation")
            if operation == "status":
                _send_json(connection, {"ok": True, "status": self.status_provider()})
                return
            if operation == "analysis" and self.analysis_provider is not None:
                self.analysis_provider(request)
                _send_json(connection, {"ok": True})
                return
            if operation != "capture":
                raise ControlError("unsupported control operation")
            frame = self.frame_provider(FRAME_WAIT_SECONDS)
            if frame is None:
                raise ControlError("no fresh camera frame is available")
            validate_jpeg(frame.data)
            if len(frame.data) > MAX_FRAME_BYTES:
                raise ControlError("camera frame exceeds the allowed size")
            header = {
                "ok": True,
                "frame_bytes": len(frame.data),
                "captured_utc": frame.captured_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "sequence": frame.sequence,
                "device": frame.device.path,
                "capture_format": "MJPG",
                "capture_resolution": [frame.device.mode.width, frame.device.mode.height],
                "capture_fps": frame.device.mode.fps,
            }
            _send_json(connection, header)
            connection.sendall(frame.data)
        except (ControlError, OSError, ValueError, json.JSONDecodeError) as exc:
            _send_json(connection, {"ok": False, "error": str(exc)[:1000]})


def request_status(socket_path: str | os.PathLike[str], *, timeout: float = 3) -> dict:
    response = _request(socket_path, {"operation": "status"}, timeout=timeout)
    status = response.get("status")
    if not isinstance(status, dict):
        raise ControlError("status response is malformed")
    return status


def request_capture(socket_path: str | os.PathLike[str], *, timeout: float = 10) -> tuple[bytes, dict]:
    response = _request(socket_path, {"operation": "capture"}, timeout=timeout, read_payload=True)
    payload = response.pop("_payload")
    validate_jpeg(payload)
    return payload, response


def request_analysis(socket_path: str | os.PathLike[str], result: dict, *, timeout: float = 3) -> None:
    _request(socket_path, {**result, "operation": "analysis"}, timeout=timeout)


def _request(socket_path: str | os.PathLike[str], request: dict, *, timeout: float,
             read_payload: bool = False) -> dict:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(socket_path))
        client.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
        raw_header = _read_line(client, MAX_RESPONSE_HEADER_BYTES)
        response = json.loads(raw_header)
        if not isinstance(response, dict) or response.get("ok") is not True:
            message = response.get("error", "malformed control response") if isinstance(response, dict) else "malformed control response"
            raise ControlError(str(message))
        if not read_payload:
            return response
        length = response.get("frame_bytes")
        if not isinstance(length, int) or isinstance(length, bool) or not 4 <= length <= MAX_FRAME_BYTES:
            raise ControlError("capture response has an invalid frame length")
        response["_payload"] = _receive_exact(client, length)
        return response
    except OSError as exc:
        raise ControlError(f"cannot reach appliance control socket {socket_path}: {exc}") from exc
    finally:
        client.close()
