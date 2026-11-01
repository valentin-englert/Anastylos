"""Local session UI server: stdlib only, localhost only, explicit workspace only.

Serves the static page in ``web/`` and a narrow JSON API over ``adapter.py``.
Mutating calls are serialized per session (a double click becomes two sequential
requests; the second finds the new state and performs no duplicate operation)
and refuse foreign browser origins. The browser never sends file paths: sessions,
selection bindings and stored claims are plain names resolved inside the
configured workspace root.

    python tools/session-ui/server.py --workspace-root C:\\ANASTYLOS\\data\\session-ui-demo
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

import adapter
import drawings
from adapter import AdapterError

LOG = logging.getLogger("session-ui")
WEB = Path(__file__).resolve().parent / "web"
STATIC = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
          "styles.css": "text/css; charset=utf-8"}
MAX_BODY = 64 * 1024


def is_loopback_host(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost")


class Handler(BaseHTTPRequestHandler):
    workspace_root: Path
    locks: dict[str, threading.Lock]
    locks_guard = threading.Lock()

    # ------------------------------------------------------------------ plumbing

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802 (http.server name)
        LOG.info("%s %s", self.address_string(), fmt % args)

    def _session_lock(self, session_id: str) -> threading.Lock:
        with Handler.locks_guard:
            return Handler.locks.setdefault(session_id, threading.Lock())

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, name: str) -> None:
        body = (WEB / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", STATIC[name])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str, download_name: str | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query, keep_blank_values=True))

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise AdapterError("Request body is too large.", 400)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise AdapterError("Request body is not valid JSON.", 400) from exc
        if not isinstance(body, dict):
            raise AdapterError("Request body must be a JSON object.", 400)
        return body

    def _discard_body(self) -> None:
        # Closing a socket with unread request data makes Windows send a TCP reset that can reach
        # the client before the refusal does; drain the body first. A body over MAX_BODY is not
        # drained, so that connection is closed after the refusal and may still be reset.
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.close_connection = True
        elif length:
            self.rfile.read(length)

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            host = urllib.parse.urlsplit(origin).hostname or ""
        except ValueError:
            return False
        return is_loopback_host(host.lower())

    # ------------------------------------------------------------------ routes

    def do_GET(self) -> None:  # noqa: N802 (http.server name)
        routes: dict[str, Callable[[], None]] = {
            "/": self._page, "/index.html": self._page, "/app.js": lambda: self._send_static("app.js"),
            "/styles.css": lambda: self._send_static("styles.css"),
            "/api/sessions": self._api_sessions, "/api/status": self._api_status,
            "/api/questions": self._api_questions, "/api/guide": self._api_guide_view,
            "/api/selections": self._api_selections, "/api/assembly": self._api_assembly_view,
            "/api/report": self._api_report, "/api/gaps": self._api_gaps,
            "/api/gaps/evidence": self._api_evidence, "/api/drawings": self._api_drawings,
            "/api/drawing/status": self._api_drawing_status, "/api/drawing/file": self._api_drawing_file,
        }
        route = routes.get(urllib.parse.urlsplit(self.path).path)
        if route is None:
            self._send_json({"error": "Unknown path."}, 404)
            return
        try:
            route()
        except AdapterError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except (OSError, ValueError) as exc:
            LOG.exception("GET %s failed", self.path)
            self._send_json({"error": f"Request failed ({type(exc).__name__})."}, 500)

    def do_POST(self) -> None:  # noqa: N802 (http.server name)
        if not self._origin_ok():
            self._discard_body()
            self._send_json({"error": "Foreign browser origin refused."}, 403)
            return
        routes = {"/api/questions/next": self._api_question_next, "/api/questions/answer": self._api_answer,
                  "/api/questions/confirm": self._api_confirm, "/api/guide/build": self._api_guide_build,
                  "/api/guide/ask": self._api_guide_ask, "/api/select": self._api_select,
                  "/api/selected-ask": self._api_selected_ask, "/api/assembly/done": self._api_assembly_done,
                  "/api/report/export": self._api_report_export, "/api/gaps/evaluate": self._api_gaps_evaluate,
                  "/api/drawing/start": self._api_drawing_start, "/api/drawing/bind": self._api_drawing_bind}
        route = routes.get(urllib.parse.urlsplit(self.path).path)
        if route is None:
            self._discard_body()
            self._send_json({"error": "Unknown path."}, 404)
            return
        try:
            route()
        except AdapterError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except (OSError, ValueError) as exc:
            LOG.exception("POST %s failed", self.path)
            self._send_json({"error": f"Request failed ({type(exc).__name__})."}, 500)

    # ------------------------------------------------------------------ handlers

    def _page(self) -> None:
        self._send_static("index.html")

    def _api_sessions(self) -> None:
        self._send_json({"sessions": adapter.list_sessions(Handler.workspace_root)})

    def _session_param(self, query: dict[str, str]) -> str:
        session_id = query.get("session", "")
        if not session_id:
            raise AdapterError("The session parameter is missing.", 400)
        return session_id

    def _mutate(self, session_id: str, func: Callable[[], dict]) -> None:
        with self._session_lock(session_id):
            self._send_json(func())

    def _api_status(self) -> None:
        session_id = self._session_param(self._query())
        self._send_json(adapter.status(Handler.workspace_root, session_id))

    def _api_questions(self) -> None:
        session_id = self._session_param(self._query())
        self._send_json(adapter.question_overview(Handler.workspace_root, session_id))

    def _api_question_next(self) -> None:
        body = self._read_json()
        session_id = body.get("session", "")
        if not session_id:
            raise AdapterError("The session parameter is missing.", 400)
        self._mutate(session_id, lambda: adapter.question_next(Handler.workspace_root, session_id))

    def _api_answer(self) -> None:
        body = self._read_json()
        session_id, qid = body.get("session", ""), body.get("question_id", "")
        if not session_id or not qid:
            raise AdapterError("The session and question_id parameters are required.", 400)
        self._mutate(session_id, lambda: adapter.question_answer(
            Handler.workspace_root, session_id, qid, body.get("text"), body.get("choice")))

    def _api_confirm(self) -> None:
        body = self._read_json()
        session_id, qid = body.get("session", ""), body.get("question_id", "")
        if not session_id or not qid:
            raise AdapterError("The session and question_id parameters are required.", 400)
        self._mutate(session_id,
                      lambda: adapter.question_confirm(Handler.workspace_root, session_id, qid, body.get("text", "")))

    def _api_guide_view(self) -> None:
        session_id = self._session_param(self._query())
        self._send_json(adapter.guide_view(Handler.workspace_root, session_id))

    def _api_guide_build(self) -> None:
        body = self._read_json()
        session_id = body.get("session", "")
        if not session_id:
            raise AdapterError("The session parameter is missing.", 400)
        self._mutate(session_id, lambda: adapter.guide_build(Handler.workspace_root, session_id))

    def _api_guide_ask(self) -> None:
        body = self._read_json()
        session_id = body.get("session", "")
        if not session_id:
            raise AdapterError("The session parameter is missing.", 400)
        self._mutate(session_id, lambda: adapter.guide_ask(Handler.workspace_root, session_id, body.get("question", "")))

    def _api_selections(self) -> None:
        session_id = self._session_param(self._query())
        self._send_json(adapter.list_selections(Handler.workspace_root, session_id))

    def _api_select(self) -> None:
        body = self._read_json()
        session_id, binding = body.get("session", ""), body.get("binding", "")
        if not session_id or not binding:
            raise AdapterError("The session and binding parameters are required.", 400)
        self._mutate(session_id, lambda: adapter.select(Handler.workspace_root, session_id, binding))

    def _api_selected_ask(self) -> None:
        body = self._read_json()
        session_id, binding, intent = body.get("session", ""), body.get("binding", ""), body.get("intent", "")
        if not session_id or not binding or not intent:
            raise AdapterError("The session, binding and intent parameters are required.", 400)
        self._mutate(session_id, lambda: adapter.selected_ask(
            Handler.workspace_root, session_id, binding, intent, body.get("claims")))

    def _api_assembly_view(self) -> None:
        session_id = self._session_param(self._query())
        self._send_json(adapter.assembly_view(Handler.workspace_root, session_id))

    def _api_assembly_done(self) -> None:
        body = self._read_json()
        session_id = body.get("session", "")
        if not session_id or body.get("step") is None:
            raise AdapterError("The session and step parameters are required.", 400)
        self._mutate(session_id, lambda: adapter.assembly_done(Handler.workspace_root, session_id, body["step"]))

    def _api_report_export(self) -> None:
        body = self._read_json()
        session_id = body.get("session", "")
        if not session_id:
            raise AdapterError("The session parameter is missing.", 400)
        self._mutate(session_id, lambda: adapter.report_export(Handler.workspace_root, session_id))

    def _api_report(self) -> None:
        query = self._query()
        session_id = self._session_param(query)
        download = query.get("download") == "1"
        self._send_bytes(adapter.report_bytes(Handler.workspace_root, session_id),
                         "text/html; charset=utf-8", f"{session_id}-report.html" if download else None)

    def _api_gaps(self) -> None:
        session_id = self._session_param(self._query())
        self._send_json(adapter.gaps_view(Handler.workspace_root, session_id))

    def _api_gaps_evaluate(self) -> None:
        body = self._read_json()
        session_id = body.get("session", "")
        if not session_id:
            raise AdapterError("The session parameter is missing.", 400)
        self._mutate(session_id, lambda: adapter.gaps_evaluate(
            Handler.workspace_root, session_id, body.get("gap", ""), body.get("candidate")))

    def _api_evidence(self) -> None:
        query = self._query()
        session_id, name = self._session_param(query), query.get("file", "")
        if not name:
            raise AdapterError("The file parameter is missing.", 400)
        body, content_type = adapter.evidence_bytes(Handler.workspace_root, session_id, name)
        self._send_bytes(body, content_type)

    def _api_drawings(self) -> None:
        self._send_json(drawings.list_drawings(Handler.workspace_root, self._session_param(self._query())))

    def _api_drawing_status(self) -> None:
        query = self._query()
        self._send_json(drawings.drawing_status(Handler.workspace_root, self._session_param(query), query.get("job", "")))

    def _api_drawing_file(self) -> None:
        query = self._query()
        body, content_type = drawings.drawing_bytes(Handler.workspace_root, self._session_param(query), query.get("job", ""),
                                                    query.get("file", ""))
        self._send_bytes(body, content_type)

    def _api_drawing_start(self) -> None:
        body = self._read_json()
        session_id = body.get("session", "")
        if not session_id:
            raise AdapterError("The session parameter is missing.", 400)
        self._mutate(session_id, lambda: drawings.start_drawing(Handler.workspace_root, session_id, body.get("spec", "")))

    def _api_drawing_bind(self) -> None:
        body = self._read_json()
        session_id = body.get("session", "")
        if not session_id:
            raise AdapterError("The session parameter is missing.", 400)
        self._mutate(session_id, lambda: drawings.bind(Handler.workspace_root, session_id, body.get("job", ""),
                                                       body.get("decision", ""), str(body.get("note", ""))))


def serve(workspace_root: Path, host: str = "127.0.0.1", port: int = 8071) -> ThreadingHTTPServer:
    if not is_loopback_host(host):
        raise AdapterError(f"Refusing to bind {host}: the session UI serves localhost only.", 500)
    Handler.workspace_root = adapter.check_workspace_root(workspace_root)
    Handler.locks = {}
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ANASTYLOS local session UI (localhost only).")
    parser.add_argument("--workspace-root", required=True, type=Path,
                        help="folder with session folders (each holding agent-session.json)")
    parser.add_argument("--host", default="127.0.0.1", help="loopback interface only (default 127.0.0.1)")
    parser.add_argument("--port", default=8071, type=int)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        server = serve(args.workspace_root, args.host, args.port)
    except AdapterError as exc:
        parser.error(str(exc))
    LOG.info("Serving %s on http://%s:%s (localhost only, offline demo).",
             Handler.workspace_root, args.host, server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
