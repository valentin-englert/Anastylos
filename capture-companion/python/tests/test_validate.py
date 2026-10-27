"""Tests for the capture package validator. Standard library only.

Run from the repository root:

    py -3.12 -m unittest discover -s capture-companion/python/tests -v
"""

import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anastylos_capture_validate as validator


STAMP = "2026-01-15T10:30:00.000Z"


def fresh_id():
    return str(uuid.uuid4())


def make_session(**overrides):
    session = {
        "schema_version": 1,
        "session_id": fresh_id(),
        "name": "Day 1 teardown",
        "created_at": STAMP,
        "updated_at": STAMP,
        "assembly_memory_session": None,
        "tray_default": None,
    }
    session.update(overrides)
    return session


def make_observation(session_id, **overrides):
    obs = {
        "schema_version": 1,
        "observation_id": fresh_id(),
        "session_id": session_id,
        "component_reference": None,
        "tray_reference": None,
        "captured_at": STAMP,
        "capture_source": "camera",
        "review_state": "unreviewed",
        "operator_notes": None,
        "markings": [],
        "category": {"value": None, "state": "unknown"},
        "measurements": [],
        "quantity": {"value": None, "state": "unknown"},
        "candidates": [],
        "provenance": {
            "app": "anastylos-capture",
            "app_version": "0.1.0",
            "user_agent": None,
        },
        "export_history": [],
    }
    obs.update(overrides)
    return obs


def make_image(observation_id, payload, filename="front.jpg", role="front", **overrides):
    image = {
        "schema_version": 1,
        "image_id": fresh_id(),
        "observation_id": observation_id,
        "role": role,
        "filename": filename,
        "mime": "image/jpeg",
        "byte_length": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "captured_at": STAMP,
        "crop": None,
    }
    image.update(overrides)
    return image


def make_manifest(sessions, observations, files, **overrides):
    manifest = {
        "schema_version": 1,
        "package_id": fresh_id(),
        "exported_at": STAMP,
        "generator": {"app": "anastylos-capture", "app_version": "0.1.0"},
        "sessions": sessions,
        "observations": observations,
        "files": files,
    }
    manifest.update(overrides)
    return manifest


def write_package(path, manifest, members):
    """Write a package zip. members maps archive name to bytes."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        for name, payload in members.items():
            zf.writestr(name, payload)


class PackageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def package_path(self, name="package.zip"):
        return self.dir / name

    def build_valid(self, with_annotations=True):
        """A valid two-observation package; returns (manifest, members)."""
        session = make_session()
        first = make_observation(session["session_id"])
        second = make_observation(session["session_id"])
        payload_a = b"\xff\xd8\xff" + bytes(range(256))
        payload_b = b"\xff\xd8\xffSECOND" + bytes(range(100))
        preview = b"\xff\xd8preview"
        image_a = make_image(first["observation_id"], payload_a, "front.jpg", "front")
        image_b = make_image(second["observation_id"], payload_b, "detail.jpg", "detail")
        first["images"] = [image_a]
        second["images"] = [image_b]
        path_a = "assets/%s/front.jpg" % first["observation_id"]
        path_b = "assets/%s/detail.jpg" % second["observation_id"]
        path_p = "previews/%s/%s.jpg" % (first["observation_id"], image_a["image_id"])
        files = [
            {
                "path": path_a,
                "kind": "original",
                "observation_id": first["observation_id"],
                "image_id": image_a["image_id"],
                "sha256": image_a["sha256"],
                "bytes": len(payload_a),
                "mime": "image/jpeg",
            },
            {
                "path": path_b,
                "kind": "original",
                "observation_id": second["observation_id"],
                "image_id": image_b["image_id"],
                "sha256": image_b["sha256"],
                "bytes": len(payload_b),
                "mime": "image/jpeg",
            },
            {
                "path": path_p,
                "kind": "preview",
                "observation_id": first["observation_id"],
                "image_id": image_a["image_id"],
                "sha256": hashlib.sha256(preview).hexdigest(),
                "bytes": len(preview),
                "mime": "image/jpeg",
            },
        ]
        members = {path_a: payload_a, path_b: payload_b, path_p: preview}
        if with_annotations:
            members["annotations.json"] = json.dumps(
                {"crops": {image_a["image_id"]: {"crop": {"x": 0, "y": 0, "width": 0.5, "height": 0.5}}}}
            ).encode("utf-8")
        manifest = make_manifest([session], [first, second], files)
        return manifest, members


class ValidateTests(PackageCase):
    def test_valid_package_accepts_every_observation(self):
        manifest, members = self.build_valid()
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["package_id"], manifest["package_id"])
        self.assertEqual(len(report["accepted_for_review"]), 2)

    def test_report_is_deterministic(self):
        manifest, members = self.build_valid()
        path = self.package_path()
        write_package(path, manifest, members)
        first = validator.validate_package(path)
        second = validator.validate_package(path)
        self.assertEqual(first, second)

    def test_tampered_bytes_reject_only_the_damaged_capture(self):
        manifest, members = self.build_valid()
        first_path = next(name for name in members if name.startswith("assets/"))
        tampered = bytearray(members[first_path])
        tampered[10] ^= 0xFF
        members[first_path] = bytes(tampered)
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("SHA-256" in issue for issue in report["issues"]))
        # The healthy capture stays acceptable; only the damaged one is held.
        self.assertEqual(len(report["accepted_for_review"]), 1)
        rejected = [o for o in report["observations"] if not o["accepted"]]
        self.assertEqual(len(rejected), 1)
        self.assertTrue(any("SHA-256" in issue for issue in rejected[0]["issues"]))

    def test_missing_asset_is_rejected(self):
        manifest, members = self.build_valid()
        removed = next(name for name in members if name.startswith("assets/"))
        del members[removed]
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("missing from the archive" in issue for issue in report["issues"]))

    def test_unsafe_zip_paths_are_rejected(self):
        for unsafe in ("../evil.bin", "/abs.bin", "C:/x.bin", "a\\b.bin", "a/../../b.bin"):
            with self.subTest(path=unsafe):
                manifest, members = self.build_valid()
                members = dict(members)
                members[unsafe] = b"evil"
                path = self.package_path()
                write_package(path, manifest, members)
                report = validator.validate_package(path)
                self.assertEqual(report["status"], "invalid")
                self.assertTrue(
                    any("unsafe ZIP path" in issue for issue in report["issues"]),
                    report["issues"],
                )

    def test_duplicate_zip_entries_are_rejected(self):
        manifest, members = self.build_valid()
        path = self.package_path()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            for name, payload in members.items():
                zf.writestr(name, payload)
            zf.writestr(next(iter(members)), b"second copy")
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("duplicate ZIP entry" in issue for issue in report["issues"]))

    def test_duplicate_observation_ids_are_rejected(self):
        manifest, members = self.build_valid()
        manifest["observations"].append(copy.deepcopy(manifest["observations"][0]))
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("duplicate observation_id" in issue for issue in report["issues"]))

    def test_wrong_schema_version_is_rejected(self):
        manifest, members = self.build_valid()
        manifest["schema_version"] = 99
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("schema_version" in issue for issue in report["issues"]))

    def test_oversized_manifest_is_rejected(self):
        manifest, members = self.build_valid()
        path = self.package_path()
        write_package(path, manifest, members)
        with mock.patch.object(validator, "MAX_MANIFEST_BYTES", 10):
            report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("manifest.json is" in issue for issue in report["issues"]))

    def test_oversized_package_is_rejected(self):
        manifest, members = self.build_valid()
        path = self.package_path()
        write_package(path, manifest, members)
        with mock.patch.object(validator, "MAX_PACKAGE_BYTES", 10):
            report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")

    def test_unlisted_member_is_rejected(self):
        manifest, members = self.build_valid()
        members = dict(members)
        members["stowaway.bin"] = b"not in the manifest"
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("unlisted archive member" in issue for issue in report["issues"]))

    def test_missing_manifest_is_rejected(self):
        _manifest, members = self.build_valid()
        path = self.package_path()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
            for name, payload in members.items():
                zf.writestr(name, payload)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("manifest.json is missing" in issue for issue in report["issues"]))

    def test_corrupt_zip_is_rejected(self):
        path = self.package_path()
        path.write_bytes(b"this is not a zip archive at all" * 10)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")

    def test_confirmed_candidate_is_rejected(self):
        manifest, members = self.build_valid()
        manifest["observations"][0]["candidates"] = [
            {
                "rank": 1,
                "label": "NE555",
                "confidence": 0.9,
                "supporting_observations": [],
                "uncertainty": None,
                "provider": "x",
                "model": "y",
                "state": "confirmed",
            }
        ]
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("must be 'suggested'" in issue for issue in report["issues"]))

    def test_unknown_with_value_is_rejected(self):
        manifest, members = self.build_valid()
        manifest["observations"][0]["quantity"] = {"value": 3, "state": "unknown"}
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("unknown but carries a value" in issue for issue in report["issues"]))

    def test_dangling_session_reference_is_rejected(self):
        manifest, members = self.build_valid()
        manifest["observations"][0]["session_id"] = fresh_id()
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("unknown session" in issue for issue in report["issues"]))

    def test_one_bad_observation_blocks_only_itself(self):
        # A structurally broken observation is rejected while a global-clean
        # package still accepts the healthy one.
        manifest, members = self.build_valid()
        manifest["observations"][1]["quantity"] = {"value": 0, "state": "confirmed"}
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(len(report["accepted_for_review"]), 1)
        self.assertEqual(report["accepted_for_review"][0], manifest["observations"][0]["observation_id"])
        rejected = [o for o in report["observations"] if not o["accepted"]]
        self.assertEqual(len(rejected), 1)
        self.assertTrue(any("positive integer" in issue for issue in rejected[0]["issues"]))


class StageTests(PackageCase):
    def test_stage_valid_package_into_empty_directory(self):
        manifest, members = self.build_valid()
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        target = self.dir / "staged"
        staged = validator.stage_package(path, target, report)
        self.assertIn("manifest.json", staged)
        for name, payload in members.items():
            self.assertEqual((target / name).read_bytes(), payload)
        # Nothing outside the target was touched.
        self.assertEqual(
            {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()},
            set(members) | {"manifest.json"},
        )

    def test_stage_refuses_non_empty_target(self):
        manifest, members = self.build_valid()
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        target = self.dir / "staged"
        target.mkdir()
        (target / "already-here.txt").write_text("do not touch")
        with self.assertRaises(ValueError):
            validator.stage_package(path, target, report)
        self.assertEqual((target / "already-here.txt").read_text(), "do not touch")

    def test_stage_refuses_invalid_package(self):
        manifest, members = self.build_valid()
        manifest["schema_version"] = 99
        path = self.package_path()
        write_package(path, manifest, members)
        report = validator.validate_package(path)
        with self.assertRaises(ValueError):
            validator.stage_package(path, self.dir / "staged", report)
        self.assertFalse((self.dir / "staged").exists())


class CliTests(PackageCase):
    def test_main_exit_codes(self):
        manifest, members = self.build_valid()
        good = self.package_path("good.zip")
        write_package(good, manifest, members)
        bad = self.package_path("bad.zip")
        bad.write_bytes(b"not a zip")
        with mock.patch.object(sys, "argv", ["validate", "validate", str(good)]):
            with mock.patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(validator.main(), 0)
        with mock.patch.object(sys, "argv", ["validate", "validate", str(bad), "--json"]):
            with mock.patch("sys.stdout", new=io.StringIO()) as out:
                self.assertEqual(validator.main(), 1)
                parsed = json.loads(out.getvalue())
                self.assertEqual(parsed["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
