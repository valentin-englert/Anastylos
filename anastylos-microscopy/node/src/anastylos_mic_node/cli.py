"""Command line entry point for the independent Microscopy capture node."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import signal
import sys

from anastylos_mic_node.appliance import ApplianceService
from anastylos_mic_node.control import ControlError, request_capture, request_status
from anastylos_mic_node.session import SessionError, create_session, package_files, verify_manifest
from anastylos_mic_node.v4l2 import V4L2Error, probe

DEFAULT_SOCKET_PATH = os.environ.get(
    "ANASTYLOS_MIC_NODE_SOCKET", "/run/anastylos-mic-node/control.sock"
)
DEFAULT_STATUS_PATH = os.environ.get(
    "ANASTYLOS_MIC_NODE_STATUS", "/run/anastylos-mic-node/status.json"
)
DEFAULT_LOGO_PATH = os.environ.get(
    "ANASTYLOS_MIC_NODE_LOGO", "/opt/anastylos-microscope/node/assets/anastylos_logo.png"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anastylos-mic-node")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("probe", help="list V4L2 devices, supported formats and active format")

    capture = subparsers.add_parser("capture", help="capture one unchanged active MJPEG frame")
    capture.add_argument("--device", help="optional check against the service-selected device path")
    capture.add_argument("--camera-id", required=True, help="operator-provided camera label")
    capture.add_argument("--output-root", required=True)
    capture.add_argument("--validation-class", choices=("synthetic", "sample", "real_microscope"),
                         required=True)
    capture.add_argument("--node-id")
    capture.add_argument("--optical-profile", help="only set when the operator has verified it")
    capture.add_argument("--timeout", type=int, default=15, help="bounded device timeout, 1..30 seconds")

    package = subparsers.add_parser("package", help="package existing JPEG files without modifying bytes")
    package.add_argument("--input", action="append", required=True, help="JPEG input; repeat in sequence order")
    package.add_argument("--camera-id", required=True)
    package.add_argument("--output-root", required=True)
    package.add_argument("--validation-class", choices=("synthetic", "sample", "real_microscope"),
                         required=True)
    package.add_argument("--node-id")
    package.add_argument("--optical-profile", help="only set when the operator has verified it")

    verify = subparsers.add_parser("verify", help="validate schema, confinement, JPEG integrity and hashes")
    verify.add_argument("manifest")

    appliance = subparsers.add_parser("appliance", help="run the systemd-owned preview/capture service")
    appliance.add_argument("--socket", default=DEFAULT_SOCKET_PATH)
    appliance.add_argument("--status-file", default=DEFAULT_STATUS_PATH)
    appliance.add_argument("--logo", default=DEFAULT_LOGO_PATH)

    status = subparsers.add_parser("status", help="read appliance status over its local control socket")
    status.add_argument("--socket", default=DEFAULT_SOCKET_PATH)

    capture.add_argument("--socket", default=DEFAULT_SOCKET_PATH,
                         help="shared appliance socket; capture never opens the camera directly")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "probe":
            print(json.dumps(probe(), indent=2))
            return 0
        if args.operation == "appliance":
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
            service = ApplianceService(
                socket_path=args.socket,
                status_path=args.status_file,
                logo_path=args.logo,
            )
            signal.signal(signal.SIGTERM, lambda *_: service.request_stop())
            signal.signal(signal.SIGINT, lambda *_: service.request_stop())
            service.run()
            return 0
        if args.operation == "status":
            print(json.dumps(request_status(args.socket), indent=2))
            return 0
        if args.operation == "capture":
            payload, metadata = request_capture(args.socket, timeout=args.timeout)
            if args.device and args.device != metadata.get("device"):
                raise ControlError(
                    f"requested device {args.device} does not match the appliance-owned device "
                    f"{metadata.get('device')}"
                )
            captured_utc = dt.datetime.fromisoformat(
                metadata["captured_utc"].replace("Z", "+00:00")
            )
            note = (
                "Frame bytes were copied unchanged from the shared GStreamer MJPEG appsink. "
                "captured_utc is the host-observed appsink arrival time, not sensor exposure time; "
                "camera controls were not changed."
            )
            manifest = create_session(
                output_root=args.output_root,
                camera_id=args.camera_id,
                validation_class=args.validation_class,
                frames=[(payload, captured_utc)],
                capture_device=metadata["device"],
                capture_format=metadata["capture_format"],
                capture_resolution=metadata["capture_resolution"],
                optical_profile=args.optical_profile,
                node_id=args.node_id,
                camera_settings_note=note,
            )
            print(manifest)
            return 0
        if args.operation == "package":
            manifest = package_files(
                args.input,
                output_root=args.output_root,
                camera_id=args.camera_id,
                validation_class=args.validation_class,
                node_id=args.node_id,
                optical_profile=args.optical_profile,
            )
            print(manifest)
            return 0
        report = verify_manifest(args.manifest)
        print(json.dumps(report, indent=2))
        return 0
    except (SessionError, V4L2Error, ControlError, RuntimeError, OSError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
