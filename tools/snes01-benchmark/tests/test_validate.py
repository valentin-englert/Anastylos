"""Tests for validate.py using synthetic in-memory bundles."""

import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import validate


def manifest_doc():
    return {
        "schema": "snes01-benchmark/manifest@1",
        "source_video": {"sha256": "ab" * 32},
        "frames": {
            "count": 2,
            "files": [
                {
                    "name": "frame_0001.jpg",
                    "sha256": "cd" * 32,
                    "width": 2160,
                    "height": 3840,
                },
                {
                    "name": "frame_0002.jpg",
                    "sha256": "ef" * 32,
                    "width": 2160,
                    "height": 3840,
                },
            ],
        },
    }


def stable_doc():
    return {
        "schema": "snes01-benchmark/stable-states@1",
        "stable_states": [
            {
                "id": "S0",
                "label": "assembled",
                "frame_start": "frame_0001",
                "frame_end": "frame_0002",
                "time_s": [0, 1],
            }
        ],
        "occluded_intervals": [
            {
                "id": "O1",
                "frame_start": "frame_0001",
                "frame_end": "frame_0002",
            }
        ],
    }


def event_doc(eid="E1", **overrides):
    doc = {
        "id": eid,
        "status": "candidate",
        "uncertainty": "a sufficiently long uncertainty note here",
        "evidence_before": "frame_0001",
        "evidence_after": "frame_0002",
        "frame_interval": ["frame_0001", "frame_0002"],
        "time_interval_s": [0, 1],
        "contact_tile": {
            "before": {
                "frame": "frame_0001",
                "crop": [0, 0, 2160, 3840],
                "region": "full",
            },
            "after": {
                "frame": "frame_0002",
                "crop": [0, 0, 100, 100],
                "region": "tray",
            },
        },
    }
    doc.update(overrides)
    return doc


def events_doc(events=None):
    return {
        "schema": "snes01-benchmark/event-candidates@1",
        "events": events if events is not None else [event_doc()],
    }


def ground_doc(manifest_text, event_ids=("E1",)):
    return {
        "schema": "snes01-benchmark/ground-truth@1",
        "status": "pending",
        "source_manifest_sha256": hashlib.sha256(
            manifest_text.encode("utf-8")
        ).hexdigest(),
        "events": [
            {
                "id": eid,
                "status": "pending",
                "verdict": None,
                "confirmed_by": None,
            }
            for eid in event_ids
        ],
    }


class BundleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manifest_path = os.path.join(self.tmp.name, "manifest.json")
        self.stable_path = os.path.join(self.tmp.name, "stable.json")
        self.events_path = os.path.join(self.tmp.name, "events.json")
        self.ground_path = os.path.join(self.tmp.name, "ground.json")
        for name in ("frame_0001.jpg", "frame_0002.jpg", "sheet.jpg"):
            with open(os.path.join(self.tmp.name, name), "wb") as handle:
                handle.write(b"x")

    def read_text(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def read_json(self, path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def write_bundle(self, manifest, stable, events, ground):
        for path, doc in (
            (self.manifest_path, manifest),
            (self.stable_path, stable),
            (self.events_path, events),
        ):
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(doc, handle)
        manifest_text = self.read_text(self.manifest_path)
        if ground == "auto":
            ground = ground_doc(manifest_text)
        with open(self.ground_path, "w", encoding="utf-8") as handle:
            json.dump(ground, handle)

    def run_main(self, extra=None):
        argv = [
            "--manifest",
            self.manifest_path,
            "--stable",
            self.stable_path,
            "--events",
            self.events_path,
            "--ground",
            self.ground_path,
            "--frames-dir",
            self.tmp.name,
            "--contact-sheet",
            os.path.join(self.tmp.name, "sheet.jpg"),
        ] + (extra or [])
        return validate.main(argv)

    def test_valid_bundle_passes(self):
        manifest = manifest_doc()
        self.write_bundle(manifest, stable_doc(), events_doc(), "auto")
        self.assertEqual(self.run_main(), 0)

    def test_nine_events_fail(self):
        events = events_doc(
            [event_doc(f"E{i}", id=f"E{i}") for i in range(1, 10)]
        )
        self.write_bundle(manifest_doc(), stable_doc(), events, "auto")
        # fix ground to cover the ids so only the count check fails
        manifest_text = self.read_text(self.manifest_path)
        ground = ground_doc(manifest_text, [f"E{i}" for i in range(1, 10)])
        with open(self.ground_path, "w", encoding="utf-8") as handle:
            json.dump(ground, handle)
        self.assertEqual(self.run_main(), 1)

    def test_non_candidate_status_fails(self):
        events = events_doc([event_doc(status="confirmed")])
        self.write_bundle(manifest_doc(), stable_doc(), events, "auto")
        self.assertEqual(self.run_main(), 1)

    def test_duplicate_ids_fail(self):
        events = events_doc([event_doc(), event_doc()])
        manifest_text_holder = manifest_doc()
        self.write_bundle(manifest_text_holder, stable_doc(), events, "auto")
        self.assertEqual(self.run_main(), 1)

    def test_time_mapping_mismatch_fails(self):
        events = events_doc([event_doc(time_interval_s=[5, 6])])
        self.write_bundle(manifest_doc(), stable_doc(), events, "auto")
        self.assertEqual(self.run_main(), 1)

    def test_crop_out_of_bounds_fails(self):
        bad = event_doc()
        bad["contact_tile"]["after"]["crop"] = [2000, 3700, 500, 500]
        self.write_bundle(manifest_doc(), stable_doc(), events_doc([bad]), "auto")
        self.assertEqual(self.run_main(), 1)

    def test_ground_verdict_set_fails(self):
        manifest = manifest_doc()
        self.write_bundle(manifest, stable_doc(), events_doc(), "auto")
        ground = self.read_json(self.ground_path)
        ground["events"][0]["verdict"] = "confirmed"
        with open(self.ground_path, "w", encoding="utf-8") as handle:
            json.dump(ground, handle)
        self.assertEqual(self.run_main(), 1)

    def test_ground_status_set_fails(self):
        manifest = manifest_doc()
        self.write_bundle(manifest, stable_doc(), events_doc(), "auto")
        ground = self.read_json(self.ground_path)
        ground["status"] = "confirmed"
        with open(self.ground_path, "w", encoding="utf-8") as handle:
            json.dump(ground, handle)
        self.assertEqual(self.run_main(), 1)

    def test_manifest_hash_mismatch_fails(self):
        manifest = manifest_doc()
        self.write_bundle(manifest, stable_doc(), events_doc(), "auto")
        with open(self.manifest_path, "a", encoding="utf-8") as handle:
            handle.write(" ")  # tamper after ground was linked
        self.assertEqual(self.run_main(), 1)

    def test_manifest_count_mismatch_fails(self):
        manifest = manifest_doc()
        manifest["frames"]["count"] = 99
        self.write_bundle(manifest, stable_doc(), events_doc(), "auto")
        self.assertEqual(self.run_main(), 1)

    def test_missing_frame_file_fails(self):
        self.write_bundle(manifest_doc(), stable_doc(), events_doc(), "auto")
        os.remove(os.path.join(self.tmp.name, "frame_0002.jpg"))
        self.assertEqual(self.run_main(), 1)

    def test_stable_time_mapping_mismatch_fails(self):
        stable = stable_doc()
        stable["stable_states"][0]["time_s"] = [3, 4]
        self.write_bundle(manifest_doc(), stable, events_doc(), "auto")
        self.assertEqual(self.run_main(), 1)

    def test_ground_id_coverage_mismatch_fails(self):
        manifest = manifest_doc()
        self.write_bundle(manifest, stable_doc(), events_doc(), "auto")
        manifest_text = self.read_text(self.manifest_path)
        ground = ground_doc(manifest_text, ("E1", "E2"))
        with open(self.ground_path, "w", encoding="utf-8") as handle:
            json.dump(ground, handle)
        self.assertEqual(self.run_main(), 1)


if __name__ == "__main__":
    unittest.main()
