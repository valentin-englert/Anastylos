"""Disassembly-video routes: auth, chunks, resume, hash mismatch, duplicates, journal."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anastylos_video_routes as video  # noqa: E402
from test_guided_routes import GuidedCase  # noqa: E402

CHUNK = 16  # tiny chunks keep the tests fast; the module reads the size at call time


class VideoCase(GuidedCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(video, "CHUNK_BYTES", CHUNK)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source_id, self.token, self.legacy = self.enroll_source()
        self.session_id = self.open_session(self.bootstrap(), self.source_id)["session_id"]
        self.payload = bytes(range(256)) * 2 + b"tail"  # 516 bytes: 32 full chunks and a short one
        self.sha = hashlib.sha256(self.payload).hexdigest()
        self.base = f"/v1/guided/sessions/{self.session_id}/videos"

    def declare(self, sha=None, size=None, token=None):
        return self.json_call("POST", self.base, token=token or self.token,
                              body={"sha256": sha or self.sha, "size": size or len(self.payload)})

    def put(self, video_id, index, data=None, token=None):
        if data is None:
            data = self.payload[index * CHUNK:(index + 1) * CHUNK]
        status, body, _ = self.call("PUT", f"{self.base}/{video_id}/chunks/{index}", token=token or self.token, raw=data)
        return status, json.loads(body.decode("utf-8"))

    def stored(self, video_id):
        return self.data / video.VIDEO_DIR / self.session_id / video_id

    def upload_all(self, video_id, skip=()):
        count = video.chunk_count(len(self.payload))
        return [self.put(video_id, i) for i in range(count) if i not in skip]


class TestAuth(VideoCase):
    def test_requires_a_guided_source_token(self):
        self.assertEqual(self.call("POST", self.base, body={"sha256": self.sha, "size": 1})[0], 401)
        self.assertEqual(self.declare(token=self.legacy)[0], 401)
        self.assertEqual(self.call("GET", self.base)[0], 401)
        self.assertEqual(self.call("PUT", f"{self.base}/{self.sha[:32]}/chunks/0", raw=b"x")[0], 401)

    def test_other_session_is_not_found(self):
        status, body, _ = self.call("GET", f"/v1/guided/sessions/{'0' * 32}/videos", token=self.token)
        self.assertIn(status, (403, 404), body)


class TestDeclare(VideoCase):
    def test_declare_is_idempotent_and_reports_the_chunk_plan(self):
        status, first, _ = self.declare()
        self.assertEqual(status, 201)
        self.assertEqual(first["video_id"], self.sha[:32])
        self.assertEqual(first["chunk_count"], video.chunk_count(len(self.payload)))
        self.assertEqual(first["received"], [])
        status, again, _ = self.declare()
        self.assertEqual(status, 200)
        self.assertEqual(again["video_id"], first["video_id"])

    def test_rejects_bad_hash_size_and_the_4gb_limit(self):
        self.assertEqual(self.declare(sha="xyz")[0], 400)
        self.assertEqual(self.json_call("POST", self.base, token=self.token, body={"sha256": self.sha, "size": 0})[0], 400)
        self.assertEqual(self.declare(size=video.MAX_VIDEO_BYTES + 1)[0], 413)


class TestChunks(VideoCase):
    def test_full_upload_assembles_the_verified_file_atomically(self):
        video_id = self.declare()[1]["video_id"]
        results = self.upload_all(video_id)
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(results[-1][1]["status"], "complete")
        final = self.stored(video_id) / video.FINAL_NAME
        self.assertEqual(final.read_bytes(), self.payload)
        self.assertEqual([p.name for p in self.stored(video_id).glob("*.part")], [])
        self.assertEqual(list((self.stored(video_id) / "chunks").iterdir()), [])
        status, listed, _ = self.json_call("GET", self.base, token=self.token)
        self.assertEqual([v["status"] for v in listed["videos"]], ["complete"])

    def test_resume_asks_for_what_is_missing(self):
        video_id = self.declare()[1]["video_id"]
        self.upload_all(video_id, skip={3, 7})
        status, view, _ = self.json_call("GET", f"{self.base}/{video_id}", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(view["status"], "receiving")
        missing = sorted(set(range(view["chunk_count"])) - set(view["received"]))
        self.assertEqual(missing, [3, 7])
        for index in missing:
            self.assertEqual(self.put(video_id, index)[0], 200)
        self.assertEqual(self.json_call("GET", f"{self.base}/{video_id}", token=self.token)[1]["status"], "complete")

    def test_duplicate_chunk_is_idempotent_and_a_different_one_conflicts(self):
        video_id = self.declare()[1]["video_id"]
        self.assertEqual(self.put(video_id, 0)[0], 200)
        self.assertEqual(self.put(video_id, 0)[0], 200)
        status, body = self.put(video_id, 0, data=b"\xff" * CHUNK)
        self.assertEqual((status, body["error"]["code"]), (409, "chunk_conflict"))

    def test_resending_after_completion_changes_nothing(self):
        video_id = self.declare()[1]["video_id"]
        self.upload_all(video_id)
        status, body = self.put(video_id, 1)
        self.assertEqual((status, body["status"]), (200, "complete"))
        self.assertEqual((self.stored(video_id) / video.FINAL_NAME).read_bytes(), self.payload)

    def test_hash_mismatch_is_refused_and_can_be_retried(self):
        wrong = hashlib.sha256(b"other").hexdigest()
        video_id = self.declare(sha=wrong)[1]["video_id"]
        results = self.upload_all(video_id)
        status, body = results[-1]
        self.assertEqual((status, body["error"]["code"]), (409, "hash_mismatch"))
        self.assertFalse((self.stored(video_id) / video.FINAL_NAME).exists())
        self.assertEqual(self.json_call("GET", f"{self.base}/{video_id}", token=self.token)[1]["status"], "failed")
        self.assertEqual(self.declare(sha=wrong)[0], 201)  # a failed attempt may be declared again

    def test_wrong_length_unknown_chunk_and_undeclared_video(self):
        video_id = self.declare()[1]["video_id"]
        status, body = self.put(video_id, 0, data=b"short")
        self.assertEqual((status, body["error"]["code"]), (400, "bad_chunk_length"))
        self.assertEqual(self.put(video_id, 999)[0], 404)
        self.assertEqual(self.put("a" * 32, 0, data=b"x")[0], 404)


class TestJournal(VideoCase):
    def test_journal_has_routes_and_counts_but_no_content(self):
        video_id = self.declare()[1]["video_id"]
        self.upload_all(video_id)
        text = (self.data / video.JOURNAL_FILE).read_text(encoding="utf-8")
        lines = [json.loads(line) for line in text.splitlines()]
        self.assertIn("ok", [line["status"] for line in lines if line["route"] == "complete"])
        self.assertTrue(all(set(line) == {"at", "session_id", "video_id", "route", "status", "bytes"} for line in lines))
        self.assertNotIn(self.payload[:16].hex(), text)
