"""Copied-session sampling must not write caches into the original workspace."""

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import frames


class CacheTest(unittest.TestCase):
    def test_configured_cache_reuses_samples_without_changing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "input.mp4"
            video.write_bytes(b"source fixture; decoder is mocked")
            before = video.read_bytes()
            cache = root / "copied-session-cache"
            candidate = frames.Candidate(10, 0.5, 1.0, 0.0, 0.0, 7)
            with mock.patch.dict(os.environ, {"ANASTYLOS_DRAWING_CACHE": str(cache)}), \
                    mock.patch.object(frames, "sample", return_value=[candidate]) as sample:
                first, miss = frames.cached_sample(video, 0.5, (0, 1))
                second, hit = frames.cached_sample(video, 0.5, (0, 1))
            self.assertEqual(first, second)
            self.assertEqual(sample.call_count, 1)
            self.assertFalse(miss["hit"])
            self.assertTrue(hit["hit"])
            self.assertTrue(Path(hit["path"]).is_relative_to(cache))
            self.assertEqual(Path(hit["path"]).parent.name, hashlib.sha256(before).hexdigest())
            self.assertTrue((cache / "video-hashes.json").is_file())
            self.assertEqual(video.read_bytes(), before)
