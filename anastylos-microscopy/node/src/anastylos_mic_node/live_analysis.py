"""Live resistor-reader processing for the external Windows workstation."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
from pathlib import Path

from anastylos_mic_node.live_client import LiveClient, LiveStreamError, run_live

LOG = logging.getLogger("anastylos.microscopy.live")


def decode_frame(payload: bytes):
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("cannot decode live JPEG")
    return image


def scene_changed(first, second) -> bool:
    """Conservative coarse comparison; it is not object tracking."""
    import cv2
    import numpy as np

    if first.shape != second.shape:
        return True
    def thumbnail(image):
        return cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (160, 90),
                          interpolation=cv2.INTER_AREA)
    difference = cv2.absdiff(thumbnail(first), thumbnail(second))
    return float(np.mean(difference)) > 3.0 or float(np.mean(difference > 20)) > 0.002


def analyze_resistor(image) -> tuple[str, str]:
    from anastylos_microscopy.adapters import REGISTRY, core_status
    from anastylos_microscopy.orchestrator import _apply_core_assessment

    adapter = REGISTRY["resistor"]
    native = adapter.run(image, None, {"image_name": "live"})
    normalized = adapter.normalize(native)
    native_status = native.get("status", native.get("verdict"))
    block = {"status": core_status(native_status), "native_status": native_status}
    _apply_core_assessment("resistor", block, native, normalized, image)
    candidates = normalized.get("candidates") or []
    if not candidates:
        return ("ambiguous" if block["status"] == "ambiguous" else "unknown"), ""
    value = (str(candidates[0].get("value") or "")
             .replace("Ω", "ohm").replace("Ω", "ohm")
             .replace("µ", "u").replace("μ", "u").replace("\u00a0", " "))
    if not value or len(value) > 70 or not re.fullmatch(r"[A-Za-z0-9 .+%()/?_-]+", value):
        return "unknown", ""
    if block["status"] == "ok":
        return "candidate", value
    if block["status"] == "ambiguous":
        return "ambiguous", "candidate " + value
    return "unknown", "candidate " + value


def run(host: str, user: str, alias: str, stop: threading.Event) -> None:
    from anastylos_microscopy import env

    env.activate(env.resolve_root())
    client = LiveClient(host, user, alias)
    last_image = None
    measurement_start = time.monotonic()
    completed = 0
    attempts = 0
    reader_seconds = 0.0
    result_seconds = 0.0

    def process(payload, metadata):
        nonlocal last_image, measurement_start, completed, attempts, reader_seconds, result_seconds
        processing_started = time.monotonic()
        source = {"sequence": metadata["sequence"], "captured_utc": metadata["captured_utc"]}
        try:
            image = decode_frame(payload)
        except ValueError:
            client.analysis({"clear": True})
            last_image = None
            return
        if last_image is None or scene_changed(last_image, image):
            client.analysis({**source, "state": "processing"})
            last_image = None
        try:
            reader_started = time.monotonic()
            state, candidate = analyze_resistor(image)
        except Exception as exc:
            LOG.warning("reader unavailable: %s", exc)
            state, candidate = "reader_error", ""
        reader_seconds += time.monotonic() - reader_started
        attempts += 1
        latest, _ = client.frame()
        try:
            changed = scene_changed(image, decode_frame(latest))
        except ValueError:
            changed = True
        if changed:
            client.analysis({"clear": True})
            last_image = None
        else:
            client.analysis({**source, "state": state, "candidate": candidate})
            last_image = image
            completed += 1
            result_seconds += time.monotonic() - processing_started
        elapsed = time.monotonic() - measurement_start
        if elapsed >= 10:
            LOG.info("updates=%.2f/s mean_reader_ms=%.0f mean_post_receive_ms=%.0f",
                     completed / elapsed, reader_seconds * 1000 / max(attempts, 1),
                     result_seconds * 1000 / max(completed, 1))
            measurement_start = time.monotonic()
            completed = 0
            attempts = 0
            reader_seconds = result_seconds = 0.0

    def status_changed(status):
        nonlocal last_image
        if status != "connected":
            last_image = None
        LOG.info("%s", status)

    run_live(client, stop, process, status_changed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run live microscope resistor analysis")
    parser.add_argument("--host", default="anastylos.local")
    parser.add_argument("--user", default="anastylos")
    parser.add_argument("--host-key-alias", default="anastylos.local")
    parser.add_argument("--stop", action="store_true", help="stop the Windows live worker")
    args = parser.parse_args()
    if os.name == "nt" and sys.stderr is None:
        log_dir = Path.home() / "Documents" / "Anastylos-artifacts" / "microscopy"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "live-windows.log", maxBytes=256 * 1024, backupCount=2, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO)
    mutex = None
    shutdown = None
    stop = threading.Event()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateEventW.restype = wintypes.HANDLE
        kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenEventW.restype = wintypes.HANDLE
        kernel.SetEvent.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        event_name = "Local\\ANASTYLOS-Microscopy-Live-Stop"
        if args.stop:
            shutdown = kernel.OpenEventW(0x0002, False, event_name)
            if shutdown:
                kernel.SetEvent(shutdown)
                kernel.CloseHandle(shutdown)
            return
        mutex = kernel.CreateMutexW(None, False, "Local\\ANASTYLOS-Microscopy-Live")
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            LOG.warning("another live worker is already running")
            kernel.CloseHandle(mutex)
            return
        shutdown = kernel.CreateEventW(None, True, False, event_name)
        if not shutdown:
            kernel.CloseHandle(mutex)
            raise ctypes.WinError(ctypes.get_last_error())
        def await_shutdown():
            if kernel.WaitForSingleObject(shutdown, 0xFFFFFFFF) == 0:
                stop.set()
        watcher = threading.Thread(target=await_shutdown, daemon=True)
        watcher.start()
    elif args.stop:
        parser.error("--stop is supported only on Windows")
    try:
        try:
            run(args.host, args.user, args.host_key_alias, stop)
        except (KeyboardInterrupt, LiveStreamError):
            LOG.info("live worker stopped")
    finally:
        if mutex:
            kernel.SetEvent(shutdown)
            watcher.join()
            kernel.CloseHandle(shutdown)
            kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()
