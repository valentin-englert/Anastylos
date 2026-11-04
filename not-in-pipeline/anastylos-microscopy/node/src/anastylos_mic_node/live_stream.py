"""Authenticated SSH stdio bridge to the appliance's existing frame socket."""

from __future__ import annotations

import json
import sys

from anastylos_mic_node.control import ControlError, request_analysis, request_capture

SOCKET_PATH = "/run/anastylos-mic-node/control.sock"


def serve(input_stream, output_stream, *, socket_path=SOCKET_PATH, capture=request_capture,
          publish=request_analysis):
    """Send only one fresh-slot frame in response to each bounded request."""
    while True:
        request = input_stream.readline(2049)
        if not request:
            return
        try:
            if request == b"frame\n":
                frame, metadata = capture(socket_path)
                header = {"ok": True, **metadata, "frame_bytes": len(frame)}
            elif request.startswith(b"analysis ") and request.endswith(b"\n") and len(request) <= 2048:
                result = json.loads(request[9:])
                if not isinstance(result, dict):
                    raise ValueError("analysis must be an object")
                publish(socket_path, result)
                frame = b""
                header = {"ok": True}
            else:
                return
        except (ControlError, OSError, ValueError, json.JSONDecodeError) as exc:
            frame = b""
            header = {"ok": False, "error": str(exc)[:256]}
        output_stream.write(json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n")
        if frame:
            output_stream.write(frame)
        output_stream.flush()


if __name__ == "__main__":
    serve(sys.stdin.buffer, sys.stdout.buffer)
