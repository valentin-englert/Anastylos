"""Tests for make_manifest.py helpers (no ffmpeg, no real inputs)."""

import hashlib
import json
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_manifest


def synthetic_jpeg(width, height):
    """Minimal JPEG: SOI + APP0 + DQT + SOF0 + EOI."""
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    dqt = b"\xff\xdb\x00\x43\x00" + bytes(64)
    sof0 = (
        b"\xff\xc0\x00\x0b\x08"
        + struct.pack(">HH", height, width)
        + b"\x01\x01\x11\x00"
    )
    return b"\xff\xd8" + app0 + dqt + sof0 + b"\xff\xd9"


class JpegDimensionsCase(unittest.TestCase):
    def test_parses_sof0(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(synthetic_jpeg(2160, 3840))
            path = tmp.name
        self.addCleanup(os.remove, path)
        self.assertEqual(make_manifest.jpeg_dimensions(path), (2160, 3840))

    def test_rejects_non_jpeg(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(b"not a jpeg at all....")
            path = tmp.name
        self.addCleanup(os.remove, path)
        with self.assertRaises(ValueError):
            make_manifest.jpeg_dimensions(path)


class MappingChecksCase(unittest.TestCase):
    def test_parses_checks(self):
        parsed = make_manifest.parse_mapping_checks(
            "t=0:frame_0001, t=84:frame_0085"
        )
        self.assertEqual(
            parsed,
            [
                {"video_time_s": "0", "frame": "frame_0001"},
                {"video_time_s": "84", "frame": "frame_0085"},
            ],
        )

    def test_rejects_malformed(self):
        with self.assertRaises(ValueError):
            make_manifest.parse_mapping_checks("nonsense")


class BuildManifestCase(unittest.TestCase):
    def test_manifest_structure_with_stubbed_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "clip.mp4")
            with open(video, "wb") as handle:
                handle.write(b"fake-video-bytes")
            frames_dir = os.path.join(tmp, "frames")
            os.mkdir(frames_dir)
            for name in ("frame_0001.jpg", "frame_0002.jpg"):
                with open(os.path.join(frames_dir, name), "wb") as handle:
                    handle.write(synthetic_jpeg(2160, 3840))
            probe = {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 3840,
                        "height": 2160,
                        "avg_frame_rate": "30/1",
                        "nb_frames": "6384",
                        "duration": "213.18",
                        "side_data_list": [
                            {
                                "side_data_type": "Display Matrix",
                                "rotation": -90,
                            }
                        ],
                    }
                ],
                "format": {
                    "format_name": "mov,mp4",
                    "duration": "213.18",
                    "tags": {"creation_time": "2026-10-27T16:28:00.000000Z"},
                },
            }
            args = mock.Mock(
                video=video,
                frames_dir=frames_dir,
                sibling=[],
                mapping_checks="t=0:frame_0001",
                ffprobe="ffprobe",
                ffmpeg="ffmpeg",
            )
            with (
                mock.patch.object(
                    make_manifest, "run_ffprobe", return_value=probe
                ),
                mock.patch.object(
                    make_manifest, "tool_version", return_value="stub"
                ),
            ):
                doc = make_manifest.build_manifest(args)
        self.assertEqual(doc["schema"], make_manifest.SCHEMA)
        self.assertEqual(
            doc["source_video"]["sha256"],
            hashlib.sha256(b"fake-video-bytes").hexdigest(),
        )
        self.assertEqual(
            doc["source_video"]["video_stream"]["display_rotation_deg"], -90
        )
        self.assertEqual(doc["frames"]["count"], 2)
        self.assertEqual(
            [f["name"] for f in doc["frames"]["files"]],
            ["frame_0001.jpg", "frame_0002.jpg"],
        )
        for entry in doc["frames"]["files"]:
            self.assertEqual((entry["width"], entry["height"]), (2160, 3840))
            self.assertEqual(
                entry["sha256"],
                hashlib.sha256(synthetic_jpeg(2160, 3840)).hexdigest(),
            )
        self.assertEqual(
            doc["frames"]["mapping_checks"],
            [{"video_time_s": "0", "frame": "frame_0001"}],
        )


if __name__ == "__main__":
    unittest.main()
