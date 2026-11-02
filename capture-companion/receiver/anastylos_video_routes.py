#!/usr/bin/env python3
"""Disassembly-video upload routes for the capture receiver. Stdlib only.

POST /v1/guided/sessions/{id}/videos                      declare {sha256, size}
GET  /v1/guided/sessions/{id}/videos                      list this session's videos
GET  /v1/guided/sessions/{id}/videos/{video_id}           status and received chunks
PUT  /v1/guided/sessions/{id}/videos/{video_id}/chunks/{n} one chunk (8 MiB, last one shorter)

Same TLS, guided-token and session checks as the other guided routes (role
``source``, session bound to that source in the control journal). The video id
is the first 32 hex characters of the declared SHA-256, so declaring the same
file again is idempotent and a phone can resume after any interruption by
asking which chunks are missing. A video becomes ``complete`` only after the
assembled bytes match the declared SHA-256 and size; the file is written
under a temporary name and moved into place atomically. The journal records
route, status and byte counts only, never content.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from http import HTTPStatus

import anastylos_guided_routes as guided

PREFIX = "/v1/guided/sessions/"
CHUNK_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024
DECLARE_BODY_MAX = 4096
VIDEO_DIR = "videos"
JOURNAL_FILE = "video-journal.jsonl"
FINAL_NAME = "video.mp4"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VIDEO_ID = re.compile(r"^[0-9a-f]{32}$")
# Oversized or malformed chunk bodies up to this size are read and discarded
# so the client receives its status code instead of a connection reset.
DRAIN_MAX_BYTES = CHUNK_BYTES + 1024


def chunk_count(size):
    return -(-size // CHUNK_BYTES)


def expected_chunk_length(size, index):
    return min(CHUNK_BYTES, size - index * CHUNK_BYTES)


def _parts(path):
    return path[len(PREFIX):].split("/")


def is_video_path(path):
    if not path.startswith(PREFIX):
        return False
    parts = _parts(path)
    if len(parts) < 2 or parts[1] != "videos" or not parts[0]:
        return False
    return len(parts) in (2, 3) or (len(parts) == 5 and parts[3] == "chunks")


def get_runtime(state):
    cached = vars(state).get("_video_runtime")
    if cached is None:
        with state.lock:
            cached = vars(state).get("_video_runtime")
            if cached is None:
                cached = {"lock": threading.Lock(), "videos": {}}
                vars(state)["_video_runtime"] = cached
    return cached


def write_json_atomic(path, payload):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class VideoRoutes:
    """Route handlers bound to one live request handler."""

    def __init__(self, handler):
        self.h = handler
        self.state = handler.state
        self.guided = guided.GuidedRoutes(handler)
        self.runtime = get_runtime(self.state)

    def dispatch(self, method, parsed):
        parts = _parts(parsed.path)
        session_id = parts[0]
        record = self.guided._require_guided("source")
        if record is None:
            self._discard()  # an unread body makes Windows reset the connection before the client reads the 401
            return True
        _snapshot, exc = self.guided._journal_errors(
            self.guided.journal.get_session, session_id, record.get("source_id"))
        if exc is not None:
            return True
        source_id = record.get("source_id")
        if len(parts) == 2:
            if method == "POST":
                self._declare(session_id, source_id)
            elif method == "GET":
                self._list(session_id)
            else:
                self._fail(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "Use POST or GET.")
            return True
        video_id = parts[2]
        if not _VIDEO_ID.match(video_id):
            self._fail(HTTPStatus.NOT_FOUND, "not_found", "Unknown video.")
            return True
        if len(parts) == 3:
            if method == "GET":
                self._status(session_id, video_id)
            else:
                self._fail(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "Use GET.")
            return True
        if method != "PUT":
            self._fail(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "Use PUT.")
            return True
        self._chunk(session_id, video_id, parts[4])
        return True

    # -- storage -----------------------------------------------------------------

    def _dir(self, session_id, video_id):
        return self.state.data_dir / VIDEO_DIR / session_id / video_id

    def _meta(self, directory):
        try:
            return json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _received(self, directory):
        try:
            return sorted(int(p.name) for p in (directory / "chunks").iterdir() if p.name.isdigit())
        except OSError:
            return []

    def _view(self, meta, directory):
        view = {k: meta[k] for k in ("video_id", "session_id", "sha256", "size", "chunk_count",
                                      "chunk_bytes", "status", "declared_at", "completed_at")}
        view["received"] = [] if meta["status"] == "complete" else self._received(directory)
        return view

    def _lock(self, video_id):
        with self.runtime["lock"]:
            return self.runtime["videos"].setdefault(video_id, threading.Lock())

    def _journal(self, session_id, video_id, route, status, nbytes):
        line = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session_id": session_id,
                "video_id": video_id, "route": route, "status": status, "bytes": nbytes}
        with self.runtime["lock"]:
            with open(self.state.data_dir / JOURNAL_FILE, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line) + "\n")

    # -- routes ------------------------------------------------------------------

    def _declare(self, session_id, source_id):
        body = self.guided._read_json(DECLARE_BODY_MAX)
        if body is None:
            return
        sha, size = body.get("sha256"), body.get("size")
        if not isinstance(sha, str) or not _SHA256.match(sha):
            return self._fail(HTTPStatus.BAD_REQUEST, "invalid_input", "sha256 must be 64 lowercase hex characters.")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            return self._fail(HTTPStatus.BAD_REQUEST, "invalid_input", "size must be a positive integer.")
        if size > MAX_VIDEO_BYTES:
            return self._fail(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large", "Videos are limited to 4 GB.")
        video_id = sha[:32]
        directory = self._dir(session_id, video_id)
        with self._lock(video_id):
            meta = self._meta(directory)
            if meta is not None and meta["sha256"] == sha and meta["size"] == size and meta["status"] != "failed":
                self._journal(session_id, video_id, "declare", "existing", 0)
                return self.h._send_json(HTTPStatus.OK, self._view(meta, directory))
            (directory / "chunks").mkdir(parents=True, exist_ok=True)
            if meta is not None:  # a failed attempt: start clean
                for leftover in self._received(directory):
                    (directory / "chunks" / str(leftover)).unlink(missing_ok=True)
            meta = {"video_id": video_id, "session_id": session_id, "source_id": source_id, "sha256": sha,
                    "size": size, "chunk_count": chunk_count(size), "chunk_bytes": CHUNK_BYTES,
                    "status": "receiving", "declared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "completed_at": None}
            write_json_atomic(directory / "meta.json", meta)
            self._journal(session_id, video_id, "declare", "created", 0)
            self.h._send_json(HTTPStatus.CREATED, self._view(meta, directory))

    def _list(self, session_id):
        root = self.state.data_dir / VIDEO_DIR / session_id
        videos = []
        try:
            for directory in sorted(root.iterdir()):
                meta = self._meta(directory)
                if meta is not None:
                    videos.append(self._view(meta, directory))
        except OSError:
            pass
        self.h._send_json(HTTPStatus.OK, {"videos": videos})

    def _status(self, session_id, video_id):
        directory = self._dir(session_id, video_id)
        meta = self._meta(directory)
        if meta is None:
            return self._fail(HTTPStatus.NOT_FOUND, "not_found", "Unknown video.")
        self.h._send_json(HTTPStatus.OK, self._view(meta, directory))

    def _chunk(self, session_id, video_id, index_text):
        directory = self._dir(session_id, video_id)
        meta = self._meta(directory)
        if meta is None:
            self._discard()
            return self._fail(HTTPStatus.NOT_FOUND, "not_found", "Declare the video first.")
        if not index_text.isdigit() or int(index_text) >= meta["chunk_count"]:
            self._discard()
            return self._fail(HTTPStatus.NOT_FOUND, "not_found", "No such chunk.")
        index = int(index_text)
        expected = expected_chunk_length(meta["size"], index)
        try:
            length = int(self.h.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length != expected:
            self._discard(length)
            return self._fail(HTTPStatus.BAD_REQUEST, "bad_chunk_length", f"Chunk {index} must be {expected} bytes.")
        try:
            data = self.h.rfile.read(length)
        except OSError:
            return self._fail(HTTPStatus.BAD_REQUEST, "interrupted", "Request body was interrupted.")
        if len(data) != length:
            return self._fail(HTTPStatus.BAD_REQUEST, "interrupted", "Request body was interrupted.")
        with self._lock(video_id):
            meta = self._meta(directory)
            if meta["status"] == "complete":
                self._journal(session_id, video_id, "chunk", "already_complete", 0)
                return self.h._send_json(HTTPStatus.OK, self._view(meta, directory))
            target = directory / "chunks" / str(index)
            if target.exists():
                if target.read_bytes() != data:
                    self._journal(session_id, video_id, "chunk", "conflict", 0)
                    return self._fail(HTTPStatus.CONFLICT, "chunk_conflict", f"Chunk {index} already holds different bytes.")
                self._journal(session_id, video_id, "chunk", "duplicate", 0)
            else:
                tmp = target.with_name(target.name + ".part")
                tmp.write_bytes(data)
                os.replace(tmp, target)
                self._journal(session_id, video_id, "chunk", "stored", len(data))
            if len(self._received(directory)) == meta["chunk_count"]:
                return self._assemble(session_id, video_id, directory, meta)
            self.h._send_json(HTTPStatus.OK, self._view(meta, directory))

    def _assemble(self, session_id, video_id, directory, meta):
        tmp = directory / (FINAL_NAME + ".part")
        digest = hashlib.sha256()
        written = 0
        with open(tmp, "wb") as out:
            for index in range(meta["chunk_count"]):
                data = (directory / "chunks" / str(index)).read_bytes()
                digest.update(data)
                written += len(data)
                out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if digest.hexdigest() != meta["sha256"] or written != meta["size"]:
            tmp.unlink(missing_ok=True)
            for index in range(meta["chunk_count"]):
                (directory / "chunks" / str(index)).unlink(missing_ok=True)
            meta["status"] = "failed"
            write_json_atomic(directory / "meta.json", meta)
            self._journal(session_id, video_id, "complete", "hash_mismatch", written)
            return self._fail(HTTPStatus.CONFLICT, "hash_mismatch",
                              "The assembled video does not match the declared SHA-256. Declare and send it again.")
        os.replace(tmp, directory / FINAL_NAME)
        meta["status"] = "complete"
        meta["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_json_atomic(directory / "meta.json", meta)
        for index in range(meta["chunk_count"]):
            (directory / "chunks" / str(index)).unlink(missing_ok=True)
        self._journal(session_id, video_id, "complete", "ok", written)
        self.h._send_json(HTTPStatus.OK, self._view(meta, directory))

    # -- plumbing ----------------------------------------------------------------

    def _discard(self, length=None):
        try:
            length = int(self.h.headers.get("Content-Length", "0")) if length is None else length
        except ValueError:
            length = -1
        if not 0 <= length <= DRAIN_MAX_BYTES:
            self.h.close_connection = True
            return
        remaining = length
        while remaining > 0:
            chunk = self.h.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)

    def _fail(self, status, code, message, extra=None):
        self.h._send_json(status, {"error": {"code": code, "message": message}}, extra)
