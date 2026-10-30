"""Pull bounded, latest-frame JPEGs through a host-key-verified SSH session."""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import threading

from anastylos_mic_node.control import MAX_RESPONSE_HEADER_BYTES
from anastylos_mic_node.session import MAX_FRAME_BYTES, validate_jpeg

LOG = logging.getLogger("anastylos.microscopy.live")

_windows_job = None


def _attach_windows_job(process) -> None:
    """Ensure Windows closes the SSH child even if the worker is terminated."""
    global _windows_job
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                     "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    if _windows_job is None:
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            kernel.CloseHandle(job)
            raise ctypes.WinError(error)
        _windows_job = job
    if not kernel.AssignProcessToJobObject(_windows_job, int(process._handle)):
        raise ctypes.WinError(ctypes.get_last_error())


class LiveStreamError(RuntimeError):
    """The authenticated live frame stream is unavailable or invalid."""


def ssh_command(host: str, user: str, host_key_alias: str) -> list[str]:
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host)
            or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host_key_alias)):
        raise ValueError("invalid SSH host, user or host key alias")
    remote = ("PYTHONPATH=/opt/anastylos-microscope/current/node/src:"
              "/opt/anastylos-microscope/current/src "
              "python3 -u -m anastylos_mic_node.live_stream")
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=2", "-o", f"HostKeyAlias={host_key_alias}",
            f"{user}@{host}", remote]


class LiveClient:
    """One request at a time; no frame queue accumulates during inference."""

    def __init__(self, host: str, user: str, host_key_alias: str) -> None:
        self.command = ssh_command(host, user, host_key_alias)
        self.process = None
        self._lock = threading.Lock()
        self._stderr_thread = None
        self._stderr_tail = bytearray()

    def connect(self) -> None:
        self.close()
        self._stderr_tail.clear()
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, bufsize=0, shell=False,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        process = self.process
        if os.name == "nt":
            try:
                _attach_windows_job(process)
            except OSError:
                self.close()
                raise
        def drain_stderr():
            while True:
                chunk = process.stderr.read(4096)
                if not chunk:
                    break
                self._stderr_tail.extend(chunk)
                del self._stderr_tail[:-4096]
        self._stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        self._stderr_thread.start()

    def frame(self, *, timeout: float = 15) -> tuple[bytes, dict]:
        return self._exchange(self._frame, timeout)

    def analysis(self, result: dict, *, timeout: float = 5) -> None:
        encoded = json.dumps(result, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 1800:
            raise ValueError("analysis result is too large")
        self._exchange(lambda process: self._publish(process, encoded), timeout)

    def _exchange(self, operation, timeout):
        with self._lock:
            if self.process is None or self.process.poll() is not None:
                raise LiveStreamError("SSH stream is not connected")
            results = queue.Queue(maxsize=1)
            process = self.process

            def receive():
                try:
                    results.put((True, operation(process)))
                except Exception as exc:
                    results.put((False, exc))

            thread = threading.Thread(target=receive, daemon=True)
            thread.start()
            try:
                success, result = results.get(timeout=timeout)
            except queue.Empty as exc:
                self.close()
                raise LiveStreamError("live frame request timed out") from exc
            if not success:
                self.close()
                raise LiveStreamError(f"live stream failed: {result}") from result
            return result

    @staticmethod
    def _publish(process, encoded: bytes) -> None:
        process.stdin.write(b"analysis " + encoded + b"\n")
        process.stdin.flush()
        line = process.stdout.readline(MAX_RESPONSE_HEADER_BYTES + 1)
        if not line.endswith(b"\n") or len(line) > MAX_RESPONSE_HEADER_BYTES:
            raise LiveStreamError("invalid analysis response")
        response = json.loads(line)
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise LiveStreamError("analysis result rejected or unavailable")

    @staticmethod
    def _frame(process) -> tuple[bytes, dict]:
        try:
            process.stdin.write(b"frame\n")
            process.stdin.flush()
            line = process.stdout.readline(MAX_RESPONSE_HEADER_BYTES + 1)
            if not line.endswith(b"\n") or len(line) > MAX_RESPONSE_HEADER_BYTES:
                raise LiveStreamError("invalid live frame header")
            header = json.loads(line)
            if not isinstance(header, dict) or header.get("ok") is not True:
                raise LiveStreamError("appliance has no fresh frame")
            length = header.get("frame_bytes")
            if not isinstance(length, int) or isinstance(length, bool) or not 4 <= length <= MAX_FRAME_BYTES:
                raise LiveStreamError("invalid live frame length")
            payload = _read_exact(process.stdout, length)
            validate_jpeg(payload)
            return payload, header
        except (OSError, EOFError, ValueError, json.JSONDecodeError) as exc:
            raise LiveStreamError(f"live stream failed: {exc}") from exc

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        process.kill()
        process.wait(timeout=5)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=5)
            self._stderr_thread = None
        if self._stderr_tail:
            LOG.warning("SSH: %s", self._stderr_tail.decode("utf-8", errors="replace").strip())
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _read_exact(stream, length: int) -> bytes:
    parts = []
    while length:
        part = stream.read(min(length, 1024 * 1024))
        if not part:
            raise EOFError("incomplete JPEG")
        parts.append(part)
        length -= len(part)
    return b"".join(parts)


def run_live(client: LiveClient, stop: threading.Event, on_frame, on_status) -> None:
    """Process the latest frames serially and reconnect after transport failures."""
    delay = 1.0
    while not stop.is_set():
        try:
            on_status("connecting")
            client.connect()
            previous = None
            connected = False
            while not stop.is_set():
                payload, metadata = client.frame()
                sequence = metadata.get("sequence")
                if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
                    raise LiveStreamError("invalid camera sequence")
                if not connected:
                    on_status("connected")
                    connected = True
                if sequence == previous:
                    stop.wait(0.02)
                    continue
                previous = sequence
                delay = 1.0
                on_frame(payload, metadata)
        except (LiveStreamError, OSError, subprocess.SubprocessError) as exc:
            on_status(f"offline: {exc}")
            client.close()
            stop.wait(delay)
            delay = min(delay * 2, 30.0)
        finally:
            client.close()
