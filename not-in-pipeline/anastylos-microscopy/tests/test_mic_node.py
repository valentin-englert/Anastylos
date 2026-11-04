from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

NODE_SOURCE = Path(__file__).resolve().parents[1] / "node" / "src"
if str(NODE_SOURCE) not in sys.path:
    sys.path.insert(0, str(NODE_SOURCE))

from anastylos_mic_node import session, v4l2
from anastylos_mic_node.appliance import ApplianceService
from anastylos_mic_node.control import ControlError, ControlServer, request_capture, request_status
from anastylos_mic_node.display import DisplayTarget, build_status_pipeline, probe_display
from anastylos_mic_node.live_client import LiveClient, LiveStreamError, run_live, ssh_command
from anastylos_mic_node.live_stream import serve
from anastylos_mic_node.pipeline import (
    CapturedFrame,
    GStreamerAcquisition,
    LatestFrameSlot,
    build_pipeline_description,
)
from anastylos_mic_node.v4l2 import CaptureDevice, CaptureMode


JPEG_A = b"\xff\xd8\xff\xc0synthetic-frame-a\xff\xd9"
JPEG_B = b"\xff\xd8\xff\xc0synthetic-frame-b\xff\xd9"
ACTIVE_MJPEG = "Width/Height : 1920/1080\nPixel Format : 'MJPG' (compressed)\n"
ACTIVE_YUYV = "Width/Height : 640/480\nPixel Format : 'YUYV'\n"


class FakeV4L2:
    def __init__(self, *, active=ACTIVE_MJPEG, payload=JPEG_A):
        self.active = active
        self.payload = payload
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if "--get-fmt-video" in command:
            output = self.active
        elif "--list-devices" in command:
            output = "HDMI capture:\n\t/dev/video4\n"
        elif "--list-formats-ext" in command:
            output = "[0]: 'MJPG' (Motion-JPEG, compressed)\n"
        elif any(value.startswith("--stream-to=") for value in command):
            destination = Path(next(value.split("=", 1)[1] for value in command
                                    if value.startswith("--stream-to=")))
            destination.write_bytes(self.payload)
            output = ""
        else:
            raise AssertionError(f"unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")


class FakeDiscoveryV4L2:
    def __init__(self):
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        device = command[command.index("--device") + 1]
        if device.endswith("video1"):
            output = "[0]: 'YUYV' (uncompressed)\n"
        else:
            output = (
                "[0]: 'MJPG' (Motion-JPEG, compressed)\n"
                "\tSize: Discrete 1920x1080\n"
                "\t\tInterval: Discrete 0.033s (30.000 fps)\n"
                "\t\tInterval: Discrete 0.017s (60.000 fps)\n"
                "\tSize: Discrete 1360x768\n"
                "\t\tInterval: Discrete 0.033s (30.000 fps)\n"
                "[1]: 'YUYV' (uncompressed)\n"
                "\tSize: Discrete 1920x1080\n"
                "\t\tInterval: Discrete 0.100s (10.000 fps)\n"
            )
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")


class FakeBuffer:
    def __init__(self, payload):
        self.payload = payload

    def get_size(self):
        return len(self.payload)

    def extract_dup(self, start, length):
        return self.payload[start:start + length]


class FakeSample:
    def __init__(self, payload):
        self.buffer = FakeBuffer(payload)

    def get_buffer(self):
        return self.buffer


class FakeAppSink:
    def __init__(self, payload):
        self.sample = FakeSample(payload)

    def emit(self, name):
        if name != "pull-sample":
            raise AssertionError(name)
        return self.sample


class MicNodeTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows process creation flags")
    def test_live_ssh_is_started_without_a_console(self):
        client = LiveClient("192.168.0.206", "anastylos", "anastylos.local")
        with patch("anastylos_mic_node.live_client.subprocess.Popen") as popen, \
                patch("anastylos_mic_node.live_client._attach_windows_job"):
            process = popen.return_value
            process.stderr.read.return_value = b""
            client.connect()
            self.assertEqual(popen.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertFalse(popen.call_args.kwargs["shell"])
            client.close()
            process.kill.assert_called_once()
            process.wait.assert_called_once()

    def test_live_retry_backoff_is_bounded(self):
        class Stop:
            def __init__(self):
                self.delays = []
            def is_set(self):
                return len(self.delays) == 7
            def wait(self, delay):
                self.delays.append(delay)
        class Client:
            def connect(self):
                raise LiveStreamError("offline")
            def close(self):
                pass
        stop = Stop()
        statuses = []
        run_live(Client(), stop, lambda *_: None, statuses.append)
        self.assertEqual(stop.delays, [1, 2, 4, 8, 16, 30, 30])
        self.assertEqual(sum(s.startswith("offline:") for s in statuses), 7)

    @unittest.skipUnless(os.name == "nt", "Windows job objects")
    def test_windows_job_ends_child_when_worker_is_terminated(self):
        import ctypes
        from ctypes import wintypes

        code = ("import subprocess, sys; "
                "from anastylos_mic_node.live_client import _attach_windows_job; "
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
                "creationflags=subprocess.CREATE_NO_WINDOW); "
                "_attach_windows_job(p); print(p.pid, flush=True); p.wait()")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(NODE_SOURCE) + os.pathsep + env.get("PYTHONPATH", "")
        worker = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = None
        try:
            line = worker.stdout.readline()
            if not line:
                self.fail(worker.stderr.read().decode(errors="replace"))
            handle = kernel.OpenProcess(0x00100000, False, int(line))
            self.assertTrue(handle)
            worker.kill()
            worker.wait(timeout=5)
            self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
        finally:
            if handle:
                kernel.CloseHandle(handle)
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=5)
            worker.stdout.close()
            worker.stderr.close()

    def test_live_stream_sends_one_original_frame_per_request(self):
        calls = []

        def capture(path):
            calls.append(path)
            return JPEG_A, {"sequence": len(calls)}

        output = io.BytesIO()
        serve(io.BytesIO(b"frame\nframe\n"), output, capture=capture)
        output.seek(0)
        for sequence in (1, 2):
            header = json.loads(output.readline())
            self.assertEqual(header["sequence"], sequence)
            self.assertEqual(output.read(header["frame_bytes"]), JPEG_A)
        self.assertEqual(output.read(), b"")
        self.assertEqual(len(calls), 2)

    def test_live_stream_rejects_unknown_command_and_untrusted_ssh_arguments(self):
        output = io.BytesIO()
        serve(io.BytesIO(b"unexpected\nframe\n"), output,
              capture=lambda _: self.fail("unexpected capture"))
        self.assertEqual(output.getvalue(), b"")
        with self.assertRaises(ValueError):
            ssh_command("-oProxyCommand=bad", "anastylos", "anastylos.local")

    def test_live_stream_publishes_bounded_analysis(self):
        calls = []
        output = io.BytesIO()
        serve(io.BytesIO(b'analysis {"clear":true}\n'), output,
              publish=lambda path, result: calls.append((path, result)))
        self.assertEqual(calls[0][1], {"clear": True})
        self.assertEqual(json.loads(output.getvalue()), {"ok": True})

    def test_live_analysis_rejects_old_results_and_expires(self):
        service = ApplianceService(socket_path=self.root / "sock", status_path=self.root / "status")
        device = CaptureDevice("/dev/video4", CaptureMode(1920, 1080, 30), "2b89", "5854")
        now = datetime.now(timezone.utc)
        service.frames.put(CapturedFrame(JPEG_A, now, 10, device))
        result = {"sequence": 10, "captured_utc": now.isoformat(), "state": "candidate", "candidate": "10 ohm"}
        service._receive_analysis(result)
        self.assertIn("CANDIDATE | 10 ohm", service._analysis_text())
        with patch("anastylos_mic_node.appliance.time.monotonic", return_value=time.monotonic() + 2):
            self.assertIn("OFFLINE", service._analysis_text())
        with self.assertRaises(ValueError):
            service._receive_analysis({**result, "sequence": 11})
        with self.assertRaises(ValueError):
            service._receive_analysis({**result, "candidate": "<b>unsafe</b>"})

    def test_live_scene_change_invalidates_on_visible_change(self):
        import numpy as np
        from anastylos_mic_node.live_analysis import scene_changed

        first = np.zeros((120, 160, 3), dtype=np.uint8)
        second = first.copy()
        second[20:100, 30:130] = 255
        self.assertFalse(scene_changed(first, first.copy()))
        self.assertTrue(scene_changed(first, second))
        small_change = first.copy()
        small_change[40:46, 40:46] = 255
        self.assertTrue(scene_changed(first, small_change))

    def test_live_reader_does_not_promote_unknown_candidate(self):
        core_source = str(NODE_SOURCE.parents[1] / "src")
        with patch.object(sys, "path", [core_source, *sys.path]):
            from anastylos_microscopy.adapters import REGISTRY
            from anastylos_mic_node.live_analysis import analyze_resistor
            adapter = REGISTRY["resistor"]
            native = {"status": "unknown"}
            normalized = {"candidates": [{"value": "10 ohm"}]}
            with patch.object(adapter, "run", return_value=native), patch.object(adapter, "normalize", return_value=normalized):
                self.assertEqual(analyze_resistor(None), ("unknown", "candidate 10 ohm"))
            normalized = {"candidates": [{"value": "4.7 µΩ"}]}
            with patch.object(adapter, "run", return_value=native), patch.object(adapter, "normalize", return_value=normalized):
                self.assertEqual(analyze_resistor(None), ("unknown", "candidate 4.7 uohm"))
            normalized = {"candidates": [{"value": "4,7 Ω"}]}
            with patch.object(adapter, "run", return_value=native), patch.object(adapter, "normalize", return_value=normalized):
                self.assertEqual(analyze_resistor(None), ("unknown", ""))

    def test_live_loop_reconnects_and_ignores_duplicate_frames(self):
        stop = threading.Event()
        received = []
        statuses = []

        class Client:
            def __init__(self):
                self.connections = 0
                self.reads = 0

            def connect(self):
                self.connections += 1
                if self.connections == 1:
                    raise LiveStreamError("disconnected")

            def frame(self):
                self.reads += 1
                sequence = 1 if self.reads < 3 else 2
                return JPEG_A, {"sequence": sequence}

            def close(self):
                pass

        client = Client()

        def on_frame(_payload, metadata):
            received.append(metadata["sequence"])
            if len(received) == 2:
                stop.set()

        run_live(client, stop, on_frame, statuses.append)
        self.assertEqual(received, [1, 2])
        self.assertEqual(client.connections, 2)
        self.assertTrue(any(status.startswith("offline:") for status in statuses))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def package(self, payload=JPEG_A, *, session_id="session-1"):
        return session.create_session(
            output_root=self.root,
            camera_id="test-camera",
            validation_class="synthetic",
            frames=[(payload, None)],
            session_id=session_id,
        )

    def test_single_frame_schema_hash_and_exact_bytes(self):
        manifest_path = self.package()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frame = manifest["frames"][0]
        stored = (manifest_path.parent / frame["file"]).read_bytes()
        self.assertEqual(stored, JPEG_A)
        self.assertEqual(frame["sha256"], hashlib.sha256(JPEG_A).hexdigest())
        self.assertEqual(frame["seq"], 1)
        from anastylos_microscopy.schema import validate_named
        self.assertEqual(validate_named(manifest, "capture_manifest"), [])
        report = session.verify_manifest(manifest_path)
        self.assertEqual(report["frames"], 1)

    def test_multiple_frames_have_stable_unique_sequences(self):
        manifest_path = session.create_session(
            output_root=self.root,
            camera_id="test-camera",
            validation_class="synthetic",
            frames=[(JPEG_A, None), (JPEG_B, None), (JPEG_A, None)],
            session_id="session-many",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual([entry["seq"] for entry in manifest["frames"]], [1, 2, 3])
        self.assertEqual(len({entry["file"] for entry in manifest["frames"]}), 3)
        self.assertEqual([entry["seq"] for entry in manifest["frames"]],
                         [entry["seq"] for entry in json.loads(manifest_path.read_text())["frames"]])

    def test_core_capture_loader_imports_generated_session(self):
        manifest_path = self.package()
        from anastylos_microscopy import capture
        loaded = capture.load(manifest_path)
        self.assertEqual(loaded["validation_class"], "synthetic")
        self.assertEqual(loaded["source"]["camera_id"], "test-camera")
        self.assertEqual(capture.verify(manifest_path), [])

    def test_missing_frame_is_rejected(self):
        manifest_path = self.package()
        frame = json.loads(manifest_path.read_text(encoding="utf-8"))["frames"][0]
        (manifest_path.parent / frame["file"]).unlink()
        with self.assertRaises(session.SessionError):
            session.verify_manifest(manifest_path)

    def test_corrupt_frame_and_hash_mismatch_are_rejected(self):
        manifest_path = self.package()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        (manifest_path.parent / manifest["frames"][0]["file"]).write_bytes(JPEG_B)
        with self.assertRaisesRegex(session.SessionError, "sha256 mismatch"):
            session.verify_manifest(manifest_path)

    def test_manifest_traversal_is_rejected(self):
        manifest_path = self.package()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["frames"][0]["file"] = "../outside.jpg"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(session.SessionError):
            session.verify_manifest(manifest_path)

    def test_unsafe_session_name_and_corrupt_jpeg_are_rejected(self):
        with self.assertRaises(session.SessionError):
            self.package(session_id="../escape")
        with self.assertRaises(session.SessionError):
            session.create_session(
                output_root=self.root,
                camera_id="test-camera",
                validation_class="synthetic",
                frames=[(b"not-a-jpeg", None)],
                session_id="bad-frame",
            )

    def test_session_collision_does_not_overwrite(self):
        manifest_path = self.package()
        before = manifest_path.read_bytes()
        with self.assertRaises(session.SessionError):
            self.package(JPEG_B)
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_package_files_copies_input_payloads_and_uses_generated_names(self):
        first = self.root / "source one.jpg"
        second = self.root / "frame-two.jpg"
        first.write_bytes(JPEG_A)
        second.write_bytes(JPEG_B)
        manifest_path = session.package_files(
            [first, second], output_root=self.root / "packages", camera_id="test-camera",
            validation_class="synthetic", session_id="packaged",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual([entry["seq"] for entry in manifest["frames"]], [1, 2])
        self.assertEqual((manifest_path.parent / manifest["frames"][0]["file"]).read_bytes(), JPEG_A)
        self.assertEqual((manifest_path.parent / manifest["frames"][1]["file"]).read_bytes(), JPEG_B)
        self.assertTrue(all(".." not in entry["file"] for entry in manifest["frames"]))

    def test_interrupted_frame_write_does_not_publish_manifest(self):
        with patch("anastylos_mic_node.session._atomic_write", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(session.SessionError, "without a manifest") as caught:
                self.package()
        retained = Path(str(caught.exception).split("incomplete session retained at ", 1)[1]
                        .split(", without a manifest", 1)[0])
        self.assertTrue(retained.is_dir())
        self.assertFalse((retained / "capture-manifest.json").exists())

    def test_capture_preserves_mjpeg_payload_and_does_not_set_device_format(self):
        fake = FakeV4L2()
        payload, active, timestamp = v4l2.capture_mjpeg(
            "/dev/video4", runner=fake,
            tempdir_factory=lambda **kwargs: tempfile.TemporaryDirectory(dir=self.root, **kwargs),
        )
        self.assertEqual(payload, JPEG_A)
        self.assertEqual((active.width, active.height, active.pixel_format), (1920, 1080, "MJPG"))
        self.assertEqual(timestamp.tzinfo, timezone.utc)
        self.assertFalse(any("set-fmt" in " ".join(command) for command in fake.commands))

    def test_unsupported_format_fails_before_streaming(self):
        fake = FakeV4L2(active=ACTIVE_YUYV)
        with self.assertRaisesRegex(v4l2.V4L2Error, "refusing conversion"):
            v4l2.capture_mjpeg("/dev/video4", runner=fake)
        self.assertFalse(any(any(arg.startswith("--stream-to=") for arg in command)
                             for command in fake.commands))

    def test_device_format_change_during_capture_is_rejected(self):
        fake = FakeV4L2()
        format_queries = 0

        def changing(command, **kwargs):
            nonlocal format_queries
            if "--get-fmt-video" in command:
                format_queries += 1
                active = ACTIVE_MJPEG if format_queries == 1 else ACTIVE_YUYV
                return subprocess.CompletedProcess(command, 0, stdout=active, stderr="")
            return fake(command, **kwargs)

        with self.assertRaisesRegex(v4l2.V4L2Error, "changed during acquisition"):
            v4l2.capture_mjpeg(
                "/dev/video4", runner=changing,
                tempdir_factory=lambda **kwargs: tempfile.TemporaryDirectory(dir=self.root, **kwargs),
            )

    def test_device_argument_must_be_normalized_linux_device_path(self):
        with self.assertRaisesRegex(v4l2.V4L2Error, "normalized /dev"):
            v4l2.capture_mjpeg("--stream-to=evil.jpg", runner=FakeV4L2())

    def test_probe_lists_device_and_supported_formats_without_setting_state(self):
        fake = FakeV4L2()
        result = v4l2.probe(runner=fake)
        self.assertEqual(result[0]["device"], "/dev/video4")
        self.assertIn("MJPG", result[0]["supported_formats"])
        self.assertEqual(result[0]["active_format"]["pixel_format"], "MJPG")
        self.assertFalse(any("set-fmt" in " ".join(command) for command in fake.commands))

    def test_missing_v4l2_utility_fails_clearly(self):
        def missing(*_args, **_kwargs):
            raise FileNotFoundError("v4l2-ctl")
        with self.assertRaisesRegex(v4l2.V4L2Error, "not installed"):
            v4l2.probe(runner=missing)

    def test_ugreen_discovery_uses_sysfs_identity_and_advertised_mjpeg_mode(self):
        sysfs = self.root / "sys" / "class" / "video4linux"
        dev_root = self.root / "dev"
        for name in ("video0", "video1"):
            entry = sysfs / name
            (entry / "device").mkdir(parents=True)
            dev_root.mkdir(parents=True, exist_ok=True)
            (dev_root / name).touch()
            (entry / "device" / "idVendor").write_text("2b89\n", encoding="ascii")
            (entry / "device" / "idProduct").write_text("5854\n", encoding="ascii")
        selected = v4l2.discover_ugreen(
            sysfs_root=sysfs,
            dev_root=dev_root,
            runner=FakeDiscoveryV4L2(),
        )
        self.assertEqual(selected.path, str(dev_root / "video0"))
        self.assertEqual(selected.mode, CaptureMode(1920, 1080, 30))
        self.assertEqual((selected.usb_vendor_id, selected.usb_product_id), ("2b89", "5854"))

    def test_ugreen_discovery_rejects_multiple_mjpeg_adapters(self):
        sysfs = self.root / "sys" / "class" / "video4linux"
        dev_root = self.root / "dev"
        for name in ("video0", "video2"):
            entry = sysfs / name
            (entry / "device").mkdir(parents=True)
            (entry / "device" / "idVendor").write_text("2b89", encoding="ascii")
            (entry / "device" / "idProduct").write_text("5854", encoding="ascii")
            dev_root.mkdir(parents=True, exist_ok=True)
            (dev_root / name).touch()
        with self.assertRaisesRegex(v4l2.V4L2Error, "multiple UGREEN"):
            v4l2.discover_ugreen(sysfs_root=sysfs, dev_root=dev_root, runner=FakeDiscoveryV4L2())

    def test_shared_pipeline_keeps_jpeg_appsink_before_display_decode_branch(self):
        device = CaptureDevice("/dev/video4", CaptureMode(1920, 1080, 30), "2b89", "5854")
        description = build_pipeline_description(device)
        self.assertIn("tee name=frames", description)
        self.assertIn("appsink name=evidence", description)
        self.assertIn("caps=image/jpeg", description)
        self.assertIn("jpegdec ! videoconvert", description)
        self.assertIn("video/x-raw,width=1920,height=1080", description)
        self.assertIn("textoverlay name=brand", description)
        self.assertIn("kmssink sync=false force-modesetting=true", description)
        evidence_branch = description.split("frames. ! queue", 2)[1].split("frames. ! queue", 1)[0]
        self.assertNotIn("jpegdec", evidence_branch)
        self.assertNotIn("videoconvert", evidence_branch)

    def test_capture_only_pipeline_does_not_claim_the_display(self):
        device = CaptureDevice("/dev/video4", CaptureMode(1920, 1080, 30), "2b89", "5854")
        description = build_pipeline_description(device, preview=False)
        self.assertIn("appsink name=evidence", description)
        self.assertNotIn("kmssink", description)
        self.assertNotIn("jpegdec", description)

    def test_latest_frame_slot_retains_only_a_bounded_fresh_frame(self):
        device = CaptureDevice("/dev/video4", CaptureMode(640, 480, 30), "2b89", "5854")
        slot = LatestFrameSlot()
        frame = CapturedFrame(JPEG_A, datetime.now(timezone.utc), 1, device)
        slot.put(frame)
        self.assertIs(slot.get(max_age_seconds=1), frame)
        self.assertIs(slot.wait(0.01, max_age_seconds=1), frame)
        slot.clear()
        self.assertIsNone(slot.get(max_age_seconds=1))

    def test_appsink_handoff_preserves_original_mjpeg_payload(self):
        device = CaptureDevice("/dev/video4", CaptureMode(640, 480, 30), "2b89", "5854")
        slot = LatestFrameSlot()
        engine = GStreamerAcquisition(device, slot)

        class FakeGst:
            class FlowReturn:
                OK = 0

        self.assertEqual(engine._on_new_sample(FakeAppSink(JPEG_A), FakeGst), FakeGst.FlowReturn.OK)
        frame = slot.get(max_age_seconds=1)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, JPEG_A)
        self.assertEqual(frame.device, device)
        self.assertEqual(frame.sequence, 1)

    def test_preview_stats_read_the_display_sink_counters(self):
        device = CaptureDevice("/dev/video4", CaptureMode(640, 480, 30), "2b89", "5854")
        engine = GStreamerAcquisition(device)

        class Stats:
            def get_value(self, name):
                return {"rendered": 125, "dropped": 2}[name]

        class Sink:
            def get_property(self, name):
                if name != "stats":
                    raise ValueError(name)
                return Stats()

        engine.preview_sink = Sink()
        self.assertEqual(engine.preview_stats(), {"rendered": 125, "dropped": 2})

    def test_display_probe_uses_connected_edid_mode_and_splash_has_brand(self):
        sysfs = self.root / "sys" / "class" / "drm"
        connector = sysfs / "card0-HDMI-A-1"
        connector.mkdir(parents=True)
        (connector / "status").write_text("connected\n", encoding="ascii")
        (connector / "modes").write_text("1360x768\n1920x1080\n", encoding="ascii")
        target = probe_display(sysfs)
        self.assertEqual(target, DisplayTarget("card0-HDMI-A-1", 1360, 768))
        logo = Path(__file__).resolve().parents[1] / "node" / "assets" / "anastylos_logo.png"
        description = build_status_pipeline(DisplayTarget(target.connector, 1920, 1080), logo)
        self.assertIn("width=1920,height=1080", description)
        self.assertIn("gdkpixbufoverlay", description)
        self.assertIn("A workbench that remembers what you took apart.", description)
        self.assertIn("overlay-width=437 overlay-height=378", description)
        self.assertIn("offset-x=741 offset-y=180", description)
        self.assertIn("valignment=top ypad=601", description)
        (connector / "modes").write_text("1360x768\n", encoding="ascii")
        self.assertEqual(probe_display(sysfs), DisplayTarget("card0-HDMI-A-1", 1360, 768))
        device = CaptureDevice("/dev/video4", CaptureMode(1920, 1080, 30), "2b89", "5854")
        preview = build_pipeline_description(device, display_size=(1360, 768))
        self.assertIn("videoscale add-borders=true", preview)
        self.assertIn("width=1360,height=768,pixel-aspect-ratio=1/1", preview)
        self.assertIn("image/jpeg,width=1920,height=1080", preview)
        (connector / "status").write_text("disconnected\n", encoding="ascii")
        self.assertIsNone(probe_display(sysfs))

    def test_control_socket_status_and_frame_capture_protocol(self):
        if not hasattr(socket, "AF_UNIX"):
            self.skipTest("Unix domain sockets are unavailable on this platform")
        socket_path = self.root / "control.sock"
        device = CaptureDevice("/dev/video4", CaptureMode(640, 480, 30), "2b89", "5854")
        frame = CapturedFrame(JPEG_A, datetime.now(timezone.utc), 4, device)
        server = ControlServer(socket_path, lambda: {"state": "ready"}, lambda _timeout: frame)
        server.start()
        try:
            self.assertEqual(request_status(socket_path)["state"], "ready")
            payload, metadata = request_capture(socket_path)
            self.assertEqual(payload, JPEG_A)
            self.assertEqual(metadata["sequence"], 4)
            self.assertEqual(metadata["device"], "/dev/video4")
            self.assertEqual(metadata["capture_resolution"], [640, 480])
        finally:
            server.stop()
        self.assertFalse(socket_path.exists())

    def test_control_socket_refuses_to_replace_regular_file(self):
        socket_path = self.root / "control.sock"
        socket_path.write_text("preserve", encoding="utf-8")
        server = ControlServer(socket_path, lambda: {}, lambda _timeout: None)
        with self.assertRaisesRegex(ControlError, "non-socket"):
            server.start()
        self.assertEqual(socket_path.read_text(encoding="utf-8"), "preserve")

    def test_appliance_capture_uses_service_owned_frame_slot(self):
        if not hasattr(socket, "AF_UNIX"):
            self.skipTest("Unix domain sockets are unavailable on this platform")
        socket_path = self.root / "runtime" / "control.sock"
        status_path = self.root / "runtime" / "status.json"
        device = CaptureDevice("/dev/video9", CaptureMode(1280, 720, 30), "2b89", "5854")

        class FakeDisplay:
            def __init__(self, _logo):
                self.statuses = []

            def show(self, status, _target):
                self.statuses.append(status)
                return False

            def stop(self):
                return None

        class FakeEngine:
            def __init__(self, selected, slot):
                self.selected = selected
                self.slot = slot

            def set_status_text(self, _text):
                return None

            def run(self, stop_event, *, preview):
                self.assert_preview = preview
                self.slot.put(CapturedFrame(JPEG_B, datetime.now(timezone.utc), 7, self.selected))
                stop_event.wait(5)

        service = ApplianceService(
            socket_path=socket_path,
            status_path=status_path,
            logo_path=self.root / "logo.png",
            device_discovery=lambda: device,
            display_probe=lambda: None,
            display_factory=FakeDisplay,
            engine_factory=FakeEngine,
            recovery_start=0.01,
            recovery_max=0.02,
            preview_retry=0.02,
        )
        service_thread = threading.Thread(target=service.run, daemon=True)
        service_thread.start()
        try:
            deadline = time.monotonic() + 3
            status = None
            while time.monotonic() < deadline:
                try:
                    status = request_status(socket_path)
                    if status["state"] == "ready":
                        break
                except (ControlError, FileNotFoundError):
                    time.sleep(0.02)
            self.assertIsNotNone(status)
            self.assertEqual(status["state"], "ready")
            payload, metadata = request_capture(socket_path)
            self.assertEqual(payload, JPEG_B)
            self.assertEqual(metadata["device"], "/dev/video9")
            self.assertEqual(json.loads(status_path.read_text(encoding="utf-8"))["state"], "ready")
        finally:
            service.request_stop()
            service_thread.join(timeout=3)
        self.assertFalse(service_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
