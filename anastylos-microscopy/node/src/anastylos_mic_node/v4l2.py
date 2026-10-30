"""Read-only V4L2 discovery and byte-preserving MJPEG acquisition via v4l2-ctl."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from anastylos_mic_node.session import MAX_FRAME_BYTES, SessionError, validate_jpeg

COMMAND_TIMEOUT_SECONDS = 15
MAX_PROBED_DEVICES = 64


class V4L2Error(RuntimeError):
    """A device is unavailable or cannot provide a verified MJPEG frame."""


@dataclass(frozen=True)
class DeviceFormat:
    pixel_format: str
    width: int
    height: int


@dataclass(frozen=True)
class CaptureMode:
    width: int
    height: int
    fps: int


@dataclass(frozen=True)
class CaptureDevice:
    path: str
    mode: CaptureMode
    usb_vendor_id: str
    usb_product_id: str


UGREEN_VENDOR_ID = "2b89"
UGREEN_PRODUCT_ID = "5854"


def _run(command: list[str], timeout: int, runner: Callable = subprocess.run) -> subprocess.CompletedProcess:
    try:
        result = runner(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise V4L2Error("v4l2-ctl is not installed or is not available on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise V4L2Error(f"v4l2-ctl operation timed out after {timeout}s") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "no diagnostic").strip()
        raise V4L2Error(f"command failed ({result.returncode}): {message}")
    return result


def list_video_devices(output: str) -> list[str]:
    devices = []
    for line in output.splitlines():
        match = re.match(r"^\s*(/dev/video\d+)\s*$", line)
        if match and match.group(1) not in devices:
            devices.append(match.group(1))
    return devices


def parse_active_format(output: str) -> DeviceFormat:
    pixel = re.search(r"Pixel Format\s*:\s*'([^']+)'", output)
    resolution = re.search(r"Width/Height\s*:\s*(\d+)\s*/\s*(\d+)", output)
    if not pixel or not resolution:
        raise V4L2Error("could not read the active V4L2 pixel format and resolution")
    width, height = int(resolution.group(1)), int(resolution.group(2))
    if width < 1 or height < 1:
        raise V4L2Error("device reported an invalid active resolution")
    return DeviceFormat(pixel.group(1), width, height)


def parse_mjpeg_modes(output: str) -> list[CaptureMode]:
    """Parse only discrete MJPEG sizes/rates advertised by v4l2-ctl."""
    modes: set[CaptureMode] = set()
    in_mjpeg = False
    size: tuple[int, int] | None = None
    for line in output.splitlines():
        if re.match(r"^\s*\[\d+\]:", line):
            in_mjpeg = "'MJPG'" in line
            size = None
            continue
        if not in_mjpeg:
            continue
        dimensions = re.search(r"Size:\s*Discrete\s+(\d+)x(\d+)", line)
        if dimensions:
            size = (int(dimensions.group(1)), int(dimensions.group(2)))
            continue
        rate = re.search(r"Interval:.*\(([\d.]+)\s+fps\)", line)
        if rate and size:
            fps = int(round(float(rate.group(1))))
            if 1 <= fps <= 120:
                modes.add(CaptureMode(size[0], size[1], fps))
    return sorted(modes, key=lambda mode: (mode.width * mode.height, mode.fps))


def _usb_ids(video_entry: Path) -> tuple[str, str] | None:
    try:
        resolved = (video_entry / "device").resolve(strict=True)
    except OSError:
        return None
    for parent in (resolved, *resolved.parents):
        try:
            vendor = (parent / "idVendor").read_text(encoding="ascii").strip().lower()
            product = (parent / "idProduct").read_text(encoding="ascii").strip().lower()
        except OSError:
            continue
        if re.fullmatch(r"[0-9a-f]{4}", vendor) and re.fullmatch(r"[0-9a-f]{4}", product):
            return vendor, product
    return None


def discover_ugreen(
    *,
    sysfs_root: str | os.PathLike[str] = "/sys/class/video4linux",
    dev_root: str | os.PathLike[str] = "/dev",
    runner: Callable = subprocess.run,
) -> CaptureDevice:
    """Select the UGREEN USB capture interface by identity and MJPEG capability.

    Device numbers are taken from sysfs and are never assumed. The highest
    advertised MJPEG resolution at 30 fps is selected; the current V4L2 format
    is left untouched until GStreamer negotiates that advertised mode.
    """
    sysfs = Path(sysfs_root)
    devs = Path(dev_root)
    candidates: list[CaptureDevice] = []
    try:
        entries = sorted(sysfs.glob("video*"), key=lambda entry: entry.name)
    except OSError as exc:
        raise V4L2Error(f"cannot enumerate V4L2 sysfs entries: {exc}") from exc
    for entry in entries:
        if not re.fullmatch(r"video\d+", entry.name):
            continue
        ids = _usb_ids(entry)
        if ids != (UGREEN_VENDOR_ID, UGREEN_PRODUCT_ID):
            continue
        path = devs / entry.name
        if not path.exists():
            continue
        device_path = str(path)
        try:
            formats = _run(
                ["v4l2-ctl", "--device", device_path, "--list-formats-ext"],
                COMMAND_TIMEOUT_SECONDS,
                runner,
            )
        except V4L2Error:
            continue
        modes = parse_mjpeg_modes(formats.stdout)
        if not modes:
            continue
        preferred = max(modes, key=lambda mode: (
            mode.fps == 30,
            mode.fps >= 25,
            mode.width * mode.height,
            mode.fps,
        ))
        candidates.append(CaptureDevice(device_path, preferred, *ids))
    if not candidates:
        raise V4L2Error("UGREEN 25854 capture interface with MJPEG support was not found")
    if len(candidates) > 1:
        paths = ", ".join(device.path for device in candidates)
        raise V4L2Error(f"multiple UGREEN MJPEG capture interfaces found; refusing to guess: {paths}")
    return candidates[0]


def probe(*, runner: Callable = subprocess.run) -> list[dict]:
    """Enumerate device nodes, supported formats and active format without setting them."""
    result = _run(["v4l2-ctl", "--list-devices"], COMMAND_TIMEOUT_SECONDS, runner)
    devices = list_video_devices(result.stdout)
    if len(devices) > MAX_PROBED_DEVICES:
        raise V4L2Error(f"device listing exceeds the {MAX_PROBED_DEVICES}-device probe limit")
    discovered = []
    for device in devices:
        try:
            formats = _run(["v4l2-ctl", "-d", device, "--list-formats-ext"],
                           COMMAND_TIMEOUT_SECONDS, runner)
            supported_formats = formats.stdout.strip()
            format_error = None
        except V4L2Error as exc:
            supported_formats = None
            format_error = str(exc)
        try:
            current = _run(["v4l2-ctl", "-d", device, "--get-fmt-video"],
                           COMMAND_TIMEOUT_SECONDS, runner)
            active = parse_active_format(current.stdout)
        except V4L2Error as exc:
            active = None
            active_error = str(exc)
        else:
            active_error = None
        discovered.append({
            "device": device,
            "supported_formats": supported_formats,
            "supported_formats_error": format_error,
            "active_format": ({"pixel_format": active.pixel_format,
                               "width": active.width, "height": active.height}
                              if active else None),
            "active_format_error": active_error,
        })
    return discovered


def _active_format(device: str, runner: Callable, timeout: int) -> DeviceFormat:
    result = _run(["v4l2-ctl", "-d", device, "--get-fmt-video"], timeout, runner)
    return parse_active_format(result.stdout)


def capture_mjpeg(
    device: str,
    *,
    timeout: int = 15,
    runner: Callable = subprocess.run,
    tempdir_factory: Callable = tempfile.TemporaryDirectory,
) -> tuple[bytes, DeviceFormat, datetime]:
    """Read exactly one currently configured MJPEG frame; never negotiate or transcode."""
    if (not isinstance(device, str) or not device.startswith("/dev/")
            or any(part in ("", ".", "..") for part in device[1:].split("/"))
            or any(ord(char) < 32 for char in device)):
        raise V4L2Error("device must be a normalized /dev/... video path from probe output")
    if not 1 <= timeout <= 30:
        raise V4L2Error("capture timeout must be between 1 and 30 seconds")
    deadline = time.monotonic() + timeout
    before = _active_format(device, runner, min(timeout, COMMAND_TIMEOUT_SECONDS))
    if before.pixel_format != "MJPG":
        raise V4L2Error(
            f"active format is {before.pixel_format!r}; refusing conversion (select MJPG externally)"
        )
    try:
        with tempdir_factory(prefix="anastylos-mic-node-") as temporary:
            payload_path = Path(temporary) / "frame.mjpg"
            command = ["v4l2-ctl", "-d", device, "--stream-mmap", "--stream-count=1",
                       f"--stream-to={payload_path}"]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise V4L2Error(f"capture operation timed out after {timeout}s")
            _run(command, max(1, min(int(remaining), timeout)), runner)
            completed_utc = datetime.now(timezone.utc).replace(microsecond=0)
            try:
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(payload_path, flags)
                try:
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode):
                        raise V4L2Error("v4l2-ctl output is not a regular file")
                    if not 1 <= info.st_size <= MAX_FRAME_BYTES:
                        raise V4L2Error(f"captured frame size {info.st_size} is outside the allowed bound")
                    with os.fdopen(descriptor, "rb", closefd=False) as stream:
                        data = stream.read(MAX_FRAME_BYTES + 1)
                    if len(data) > MAX_FRAME_BYTES or len(data) != info.st_size:
                        raise V4L2Error("captured frame changed or exceeded the allowed bound while reading")
                finally:
                    os.close(descriptor)
            except OSError as exc:
                raise V4L2Error(f"v4l2-ctl did not produce a readable frame: {exc}") from exc
            try:
                validate_jpeg(data)
            except SessionError as exc:
                raise V4L2Error(f"device output is not one complete JPEG payload: {exc}") from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise V4L2Error(f"capture operation timed out after {timeout}s")
            after = _active_format(device, runner, max(1, min(int(remaining), COMMAND_TIMEOUT_SECONDS)))
            if after != before:
                raise V4L2Error("active device format changed during acquisition; frame rejected")
            return data, before, completed_utc
    except OSError as exc:
        raise V4L2Error(f"cannot create temporary capture file: {exc}") from exc
