"""EDID-aware branded splash screen; it never touches the capture device."""

from __future__ import annotations

import struct
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DisplayTarget:
    connector: str
    width: int
    height: int


def probe_display(sysfs_root: str | Path = "/sys/class/drm") -> DisplayTarget | None:
    """Use the first advertised progressive HDMI mode."""
    root = Path(sysfs_root)
    for connector in sorted(root.glob("card*-HDMI-*"), key=lambda path: path.name):
        try:
            status = (connector / "status").read_text(encoding="ascii").strip()
            if status != "connected":
                continue
            mode_lines = (connector / "modes").read_text(encoding="ascii").splitlines()
        except OSError:
            continue
        modes = []
        for line in mode_lines:
            match = re.fullmatch(r"(\d{2,4})x(\d{2,4})", line.strip())
            if match:
                width, height = map(int, match.groups())
                if 1 <= width <= 4096 and 1 <= height <= 4096:
                    modes.append((width, height))
        if modes:
            width, height = modes[0]
            return DisplayTarget(connector.name, width, height)
    return None


def _gst_path(path: str | Path) -> str:
    text = str(path)
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ValueError("overlay path contains an invalid control character")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_status_pipeline(target: DisplayTarget, logo_path: str | Path) -> str:
    """Make a full-screen black splash using the detected monitor mode."""
    width, height = target.width, target.height
    if not (1 <= width <= 8192 and 1 <= height <= 8192):
        raise ValueError("display mode is outside the supported bounds")
    with Path(logo_path).open("rb") as logo:
        header = logo.read(24)
    if (len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n"
            or header[12:16] != b"IHDR"):
        raise ValueError("brand logo must be a valid PNG")
    source_width, source_height = struct.unpack(">II", header[16:24])
    if not source_width or not source_height:
        raise ValueError("brand logo has invalid dimensions")
    scale = min(width * 0.6 / source_width, height * 0.35 / source_height)
    logo_width = max(1, round(source_width * scale))
    logo_height = max(1, round(source_height * scale))
    offset_x = (width - logo_width) // 2
    offset_y = height // 6
    slogan_y = offset_y + logo_height + max(12, height // 25)
    slogan_size = max(6, min(30, width // 45, height // 30))
    return (
        "videotestsrc is-live=true pattern=black ! "
        f"video/x-raw,format=RGBA,width={width},height={height},framerate=25/1 ! "
        f"gdkpixbufoverlay location={_gst_path(logo_path)} overlay-width={logo_width} "
        f"overlay-height={logo_height} "
        f"offset-x={offset_x} offset-y={offset_y} ! "
        'textoverlay name=slogan text="A workbench that remembers what you took apart." '
        f'halignment=center valignment=top ypad={slogan_y} '
        f'auto-resize=false shaded-background=false font-desc="Sans, {slogan_size}" ! '
        'textoverlay name=status text="BOOTING" halignment=center valignment=bottom '
        'auto-resize=false shaded-background=true font-desc="Mono, 20" ! '
        "kmssink sync=false force-modesetting=true"
    )


class StatusDisplay:
    """Own the KMS splash pipeline while camera acquisition is unavailable."""

    def __init__(self, logo_path: str | Path) -> None:
        self.logo_path = Path(logo_path)
        self._pipeline = None
        self._status_overlay = None
        self._target: DisplayTarget | None = None
        self.last_error: str | None = None

    def show(self, status: str, target: DisplayTarget | None) -> bool:
        if target is None:
            self.stop()
            return False
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except (ImportError, ValueError) as exc:
            self.last_error = f"PyGObject GStreamer bindings unavailable: {exc}"
            return False

        Gst.init(None)
        if self._pipeline is not None and target == self._target:
            self._status_overlay.set_property("text", status[:120])
            bus = self._pipeline.get_bus()
            message = bus.timed_pop_filtered(0, Gst.MessageType.ERROR)
            if message is None:
                return True
            error, debug = message.parse_error()
            self.last_error = f"{error.message} ({debug})" if debug else str(error)
            self.stop()

        self.stop()
        if not self.logo_path.is_file():
            self.last_error = f"brand logo is missing: {self.logo_path}"
            return False
        try:
            pipeline = Gst.parse_launch(build_status_pipeline(target, self.logo_path))
            result = pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("GStreamer refused the branded status display")
            state_result, _, _ = pipeline.get_state(5 * Gst.SECOND)
            if state_result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("GStreamer did not start the branded status display")
            overlay = pipeline.get_by_name("status")
            if overlay is None:
                raise RuntimeError("status text overlay is missing from the splash pipeline")
            overlay.set_property("text", status[:120])
            self._pipeline = pipeline
            self._status_overlay = overlay
            self._target = target
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.stop()
            return False

    def stop(self) -> None:
        pipeline, self._pipeline = self._pipeline, None
        self._status_overlay = None
        self._target = None
        if pipeline is not None:
            try:
                from gi.repository import Gst

                pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
