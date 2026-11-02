"""Capture loop for part drawings: a NEEDS_CAPTURE job asks the phone for one view, the still comes back hash-checked
and a follow-up drawing job runs on it. Stdlib only; talks to the capture receiver through the G0 control client.

Workspace files (written by this module; the operator never edits them in the normal flow):
    <root>/capture-settings.json   one-time workstation setup: receiver URL + pin, receiver data dir, phone source id,
                                   board/calibration ids and board frame the phone accepts (see README "Capture loop")
    <root>/capture-link.json       the guided session this workspace drives and its adapter token (local secret,
                                   like the receiver's own token store; never sent to the browser)
    <session>/drawings/jobs/<job>/capture-request.json   request record: what was asked, request id/revision, follow-up
    <session>/drawings/captures/<image_id>.jpg + .json   delivered still and its evidence record (sha256, receipt)

Three records stay separate: the request (what the agent asked for), the evidence (what arrived, with hashes) and the
part binding (the operator's later decision on the follow-up drawing). A delivered still never proves part identity.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapter import AdapterError, resolve_session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capture-control"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drawing-proof"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "capture-companion" / "receiver"))
import anastylos_qr  # noqa: E402  (stdlib only)
import job_control  # noqa: E402
from control_client import ControlClient, ControlClientError  # noqa: E402

SETTINGS = "capture-settings.json"
LINK = "capture-link.json"
REQUEST = "capture-request.json"
PRINCIPAL = "anastylos-session-ui"
REQUESTABLE = {"NEEDS_CAPTURE"}
DELIVERED = {"package_only", "qualified"}
TERMINAL_REQUEST = {"CANCELLED", "EXPIRED", "SUPERSEDED", "UNREACHABLE"}
# Roughly top-down, about 450 mm above the part's region. The workstation rectifies every still on the sheet markers,
# so the camera pose only has to keep the sheet and the part in view: generous tolerances, no millimetre aiming.
CAMERA_HEIGHT_MM = -450.0
TOLERANCE_MM = [100, 100, 150]
TOLERANCE_DEG = 20
RECEIVER = Path(__file__).resolve().parents[2] / "capture-companion" / "receiver" / "anastylos_capture_receiver.py"


def phone_errors(func):
    """Receiver/phone failures reach the browser as a clear 502, never as an unhandled exception."""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ControlClientError as exc:
            raise AdapterError(f"Capture receiver: {exc}", 502) from exc
    return wrapper


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, doc: dict) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(doc, indent=1))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def settings(root: Path) -> dict:
    doc = _read(root / SETTINGS)
    if doc is None:
        raise AdapterError("Phone capture is not set up on this workstation (capture-settings.json missing).", 409)
    missing = [k for k in ("receiver_url", "receiver_data_dir", "source_id", "board_id", "calibration_id", "board_frame")
               if not doc.get(k)]
    if missing:
        raise AdapterError(f"Phone capture setup is incomplete: {', '.join(missing)}.", 409)
    return doc


def _client(conf: dict) -> ControlClient:
    return ControlClient(conf["receiver_url"], timeout=15, ca_file=conf.get("ca_file"), spki_pin_sha256=conf.get("spki_pin_sha256"))


def _mint_bootstrap(conf: dict) -> str:
    """The receiver CLI mints the adapter bootstrap token; it is read from stdout and kept only in capture-link.json."""
    done = subprocess.run([sys.executable, "-B", str(RECEIVER), "--data-dir", conf["receiver_data_dir"], "guided-bootstrap",
                           "--principal", PRINCIPAL, "--ttl-hours", "24"], capture_output=True, text=True, timeout=60)
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    marker = next((i for i, line in enumerate(lines) if line.startswith("adapter bootstrap token")), None)
    if done.returncode or marker is None or marker + 1 >= len(lines):
        raise AdapterError("The capture receiver did not issue an adapter token.", 502)
    return lines[marker + 1]


def _find_grant(data_dir: str, session_id: str) -> str | None:
    """Grant created when the operator armed the session on the phone. Read-only look into the receiver's journal on
    this same workstation, because the G0 contract gives a bootstrap token no read route for it (see README)."""
    db = Path(data_dir) / "control.db"
    if not db.is_file():
        return None
    with contextlib.closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as con:  # closes the file handle
        row = con.execute("SELECT grant_id FROM grants WHERE session_id = ? AND adapter_principal = ? AND revoked = 0 "
                          "AND claimed_by IS NULL ORDER BY created_at DESC LIMIT 1", (session_id, PRINCIPAL)).fetchone()
    return row[0] if row else None


@phone_errors
def link_status(root: Path, client: ControlClient | None = None, find_grant=_find_grant) -> dict:
    """Current phone link; claims the grant as soon as the operator has armed the session on the phone."""
    link = _read(root / LINK)
    if link is None:
        return {"state": "not_connected"}
    if link["state"] == "waiting_for_phone":
        conf = settings(root)
        grant = find_grant(conf["receiver_data_dir"], link["guided_session_id"])
        if grant:
            claimed = (client or _client(conf)).claim(link["bootstrap_token"], grant, str(uuid.uuid4()))
            link = {**link, "state": "connected", "grant_id": grant, "adapter_token": claimed["token"],
                    "token_expires_at": claimed["expires_at"], "bootstrap_token": None}
            _write_atomic(root / LINK, link)
    return {k: link.get(k) for k in ("state", "guided_session_id", "created_at", "token_expires_at")}


def session_qr_png(root: Path) -> bytes:
    """QR with the guided session id; the phone scans it to attach (it carries no token)."""
    link = _read(root / LINK)
    if link is None:
        raise AdapterError("Connect the phone first.", 409)
    return anastylos_qr.qr_png_bytes(anastylos_qr.encode_qr(link["guided_session_id"].encode("ascii")), scale=8)


@phone_errors
def connect_phone(root: Path, client: ControlClient | None = None, mint=_mint_bootstrap) -> dict:
    """Open one guided session for the configured phone; the operator then arms it on the phone."""
    current = _read(root / LINK)
    if current and current["state"] in ("waiting_for_phone", "connected"):
        return link_status(root, client)
    conf = settings(root)
    bootstrap = mint(conf)
    session = (client or _client(conf)).open_session(bootstrap, {
        "operation_id": str(uuid.uuid4()), "source_id": conf["source_id"], "max_images": 20,
        "expires_at": _iso(_now() + timedelta(hours=8))})
    _write_atomic(root / LINK, {"schema": "anastylos-capture-link@1", "state": "waiting_for_phone",
                                "guided_session_id": session["session_id"], "bootstrap_token": bootstrap,
                                "created_at": _iso(_now())})
    return link_status(root, client)


def _connected(root: Path) -> dict:
    link = _read(root / LINK)
    if not link or link.get("state") != "connected":
        raise AdapterError("Connect the phone first (open the session on the phone and arm it).", 409)
    return link


def _job(root: Path, session_id: str, job: str) -> Path:
    if not job or job in (".", "..") or "/" in job or "\\" in job:
        raise AdapterError(f"Unknown drawing job {job!r}.", 404)
    session_dir = resolve_session(root, session_id)
    jobs_dir = (session_dir / "drawings" / "jobs").resolve()
    if session_dir.resolve() not in jobs_dir.parents:
        raise AdapterError("Drawing jobs are outside the selected session.", 409)
    path = (jobs_dir / job).resolve()
    if path.parent != jobs_dir:
        raise AdapterError(f"Unknown drawing job {job!r}.", 404)
    if not (path / "progress.json").is_file():
        raise AdapterError(f"Unknown drawing job {job!r}.", 404)
    return path


def _job_spec(job_dir: Path) -> tuple[bytes, dict]:
    path = job_dir / "spec.json"
    if path.is_symlink() or path.resolve().parent != job_dir.resolve():
        raise AdapterError("The drawing job spec is not a regular file in its job directory.", 409)
    try:
        raw = path.read_bytes()
        spec = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise AdapterError("The drawing job spec cannot be read.", 409) from exc
    if not isinstance(spec, dict):
        raise AdapterError("The drawing job spec is not an object.", 409)
    return raw, spec


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def _follow_up_name(job_dir: Path, request_id: str) -> str:
    if not isinstance(request_id, str) or not request_id:
        raise AdapterError("The capture request has no stable request id.", 409)
    suffix = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24]
    return f"{job_dir.name}-capture-{suffix}"


def _follow_up_spec(job_dir: Path, still: dict, record: dict) -> dict:
    raw, spec = _job_spec(job_dir)
    expected = record.get("origin_spec_sha256")
    if not _valid_sha256(expected) or hashlib.sha256(raw).hexdigest() != expected:
        raise AdapterError("The drawing job changed after this capture was requested; request a fresh view.", 409)
    spec["still"] = still
    return spec


def view_label(spec: dict) -> str:
    """What the phone shows: the part, the requested side and the one rule that makes the still usable."""
    side = spec.get("view_side") or "requested side"
    return f"{spec['part']}, {side} up, on the sheet, hands off"[:120]


def request_body(conf: dict, link: dict, spec: dict, reasons: list[str], revision: int) -> dict:
    x, y, w, h = spec["region_mm"]
    cx, cy, cz = x + w / 2, y + h / 2, CAMERA_HEIGHT_MM
    return {"operation_id": str(uuid.uuid4()), "session_id": link["guided_session_id"], "expected_session_revision": revision,
            "source_id": conf["source_id"], "target_label": view_label(spec), "board_id": conf["board_id"],
            "calibration_id": conf["calibration_id"],
            "target": {"kind": "pose",
                       "T_B_C": {"from": conf.get("camera_frame", "camera:0"), "to": conf["board_frame"], "units": "mm",
                                 "matrix": [1, 0, 0, cx, 0, 1, 0, cy, 0, 0, 1, cz, 0, 0, 0, 1]},
                       "translation_tolerance_mm": TOLERANCE_MM, "rotation_tolerance_deg": TOLERANCE_DEG,
                       "roi": {"min_B_mm": [x, y, -80], "max_B_mm": [x + w, y + h, 0], "basis": "operator_declared",
                               "evidence_ids": []}},
            "quality_profile_id": conf.get("quality_profile_id", "guidance-hypothesis-1"),
            "expires_at": _iso(_now() + timedelta(minutes=30)),
            "reason": ("Drawing needs a new view: " + "; ".join(reasons))[:500] if reasons else "Drawing needs a new view.",
            "board_registration_epoch_id": str(uuid.uuid4()), "object_pose_epoch_id": str(uuid.uuid4()), "cad_context": None}


@phone_errors
def request_capture(root: Path, session_id: str, job: str, client: ControlClient | None = None) -> dict:
    """Ask the phone for the view a NEEDS_CAPTURE job lacks. One open request per job; a repeat click returns it."""
    job_dir = _job(root, session_id, job)
    existing = _read(job_dir / REQUEST)
    if existing and existing["state"] not in ("failed", "closed"):
        return capture_status(root, session_id, job, client)
    result = (job_control.status(job_dir).get("result") or {})
    if result.get("status") not in REQUESTABLE:
        raise AdapterError(f"Only a job that needs a capture can request one (status {result.get('status')}).", 409)
    conf, link = settings(root), _connected(root)
    client = client or _client(conf)
    snapshot = client.get_session(link["adapter_token"], link["guided_session_id"])
    session = snapshot.get("session") or {}
    if session.get("session_id") != link["guided_session_id"] or session.get("source_id") != conf["source_id"]:
        raise AdapterError("The connected phone session does not match this workstation's enrolled phone.", 409)
    spec_bytes, spec = _job_spec(job_dir)
    created = client.request_view(link["adapter_token"], link["guided_session_id"],
                                  request_body(conf, link, spec, result.get("reasons", []), session["revision"]))
    request = created.get("request") or {}
    if (request.get("session_id") != link["guided_session_id"] or request.get("source_id") != conf["source_id"]
            or request.get("revision") != 1 or not isinstance(request.get("request_id"), str)):
        raise AdapterError("The receiver returned a request with a different session binding.", 502)
    record = {"schema": "anastylos-capture-request@1", "state": "requested", "origin_job": job,
              "request_id": request["request_id"], "request_revision": request["revision"],
              "guided_session_id": link["guided_session_id"], "source_id": conf["source_id"],
              "origin_spec_sha256": hashlib.sha256(spec_bytes).hexdigest(), "label": request["target_label"],
              "reasons": result.get("reasons", []), "requested_at": _iso(_now()), "follow_up_job": None,
              "identity_proven": False}
    _write_atomic(job_dir / REQUEST, record)
    return capture_status(root, session_id, job, client)


def _store_still(session_dir: Path, receipt: dict, data: bytes, digest: str, record: dict) -> dict:
    image_id = receipt.get("image_id")
    if (not isinstance(image_id, str) or not image_id or image_id in (".", "..") or len(image_id) > 128
            or "/" in image_id or "\\" in image_id or "\x00" in image_id):
        raise AdapterError("The delivered receipt has an invalid image id.", 502)
    if not isinstance(data, bytes) or not data or not _valid_sha256(digest):
        raise AdapterError("The delivered image bytes are empty or have an invalid hash.", 502)
    if hashlib.sha256(data).hexdigest() != digest:
        raise AdapterError("The delivered image bytes fail their SHA-256 re-check.", 502)

    captures = session_dir / "drawings" / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    session_path, captures_path = session_dir.resolve(), captures.resolve()
    if session_path not in captures_path.parents:
        raise AdapterError("Capture evidence is outside the selected session.", 409)
    still = captures / f"{image_id}.jpg"
    if still.is_symlink() or still.resolve().parent != captures_path:
        raise AdapterError("The delivered image path is not inside the capture evidence folder.", 409)
    if still.is_file():
        existing_digest = hashlib.sha256(still.read_bytes()).hexdigest()
        if existing_digest != digest:
            raise AdapterError("An existing image with this id has different bytes.", 409)
    else:
        tmp = captures / f".{image_id}.{uuid.uuid4().hex}.tmp"
        try:
            with tmp.open("xb") as handle:
                handle.write(data)
            if tmp.resolve().parent != captures_path:
                raise AdapterError("The temporary image path is outside the capture evidence folder.", 409)
            os.replace(tmp, still)
        finally:
            tmp.unlink(missing_ok=True)

    evidence_path = captures / f"{image_id}.json"
    if evidence_path.is_symlink() or evidence_path.resolve().parent != captures_path:
        raise AdapterError("The delivered evidence path is not inside the capture evidence folder.", 409)
    evidence = {"schema": "anastylos-capture-evidence@1", "image_id": image_id, "sha256": digest,
                "receipt_status": receipt["status"], "receipt_id": receipt["receipt_id"],
                "request_id": record["request_id"], "request_revision": record["request_revision"],
                "guided_session_id": record["guided_session_id"], "source_id": record["source_id"],
                "received_at": _iso(_now()), "requested_for_job": record["origin_job"],
                "origin_spec_sha256": record["origin_spec_sha256"], "identity_proven": False}
    if evidence_path.exists():
        try:
            existing = _read(evidence_path)
        except (OSError, ValueError) as exc:
            raise AdapterError("An existing capture evidence record is unreadable.", 409) from exc
        binding = ("schema", "image_id", "sha256", "receipt_status", "receipt_id", "request_id", "request_revision",
                   "guided_session_id", "source_id", "requested_for_job", "origin_spec_sha256", "identity_proven")
        if not existing or any(existing.get(key) != evidence[key] for key in binding):
            raise AdapterError("This image id is already bound to different capture evidence.", 409)
        evidence = existing
    else:
        _write_atomic(evidence_path, evidence)
    return {"path": str(still), "sha256": digest, "request_id": record["request_id"], "request_revision": record["request_revision"],
            "guided_session_id": record["guided_session_id"], "source_id": record["source_id"], "requested_for_job": record["origin_job"],
            "origin_spec_sha256": record["origin_spec_sha256"], "receipt_id": receipt["receipt_id"], "image_id": image_id}


def _start_follow_up(job_dir: Path, still: dict) -> str:
    follow_name = _follow_up_name(job_dir, still["request_id"])
    follow = job_dir.parent / follow_name
    if follow.is_symlink() or follow.resolve().parent != job_dir.parent.resolve():
        raise AdapterError("The follow-up job path is not inside the drawing jobs folder.", 409)
    spec = _follow_up_spec(job_dir, still, {"origin_spec_sha256": still.get("origin_spec_sha256")})
    spec_path = follow / "requested-spec.json"
    follow.mkdir(parents=True, exist_ok=True)
    if spec_path.is_symlink() or spec_path.resolve().parent != follow.resolve():
        raise AdapterError("The follow-up spec path is not inside its job folder.", 409)
    if spec_path.exists():
        try:
            if json.loads(spec_path.read_text(encoding="utf-8")) != spec:
                raise AdapterError("The follow-up job already has a different spec.", 409)
        except (OSError, ValueError) as exc:
            raise AdapterError("The existing follow-up spec cannot be read.", 409) from exc
    else:
        _write_atomic(spec_path, spec)
    kept_spec = follow / "spec.json"
    if kept_spec.exists():
        try:
            if json.loads(kept_spec.read_text(encoding="utf-8")) != spec:
                raise AdapterError("The follow-up job already has a different saved spec.", 409)
        except (OSError, ValueError) as exc:
            raise AdapterError("The saved follow-up spec cannot be read.", 409) from exc
    if (follow / "progress.json").exists():
        raise AdapterError("The follow-up job already has progress; automatic restart is paused.", 409)
    started = job_control.start(spec_path, follow)
    if started.get("state") not in ("started", "already_running"):
        raise AdapterError("The follow-up job did not confirm a start.", 502)
    return follow_name


def _existing_follow_up(job_dir: Path, record: dict) -> tuple[str, str | None] | None:
    follow_name = record.get("follow_up_job")
    if not isinstance(follow_name, str) or not follow_name or "/" in follow_name or "\\" in follow_name:
        return "follow_up_pending_recovery", "The follow-up job id is missing or invalid; inspect this capture before continuing."
    follow = job_dir.parent / follow_name
    if follow.is_symlink() or follow.resolve().parent != job_dir.parent.resolve():
        return "follow_up_pending_recovery", "The follow-up job path is not inside the drawing jobs folder; inspect it before continuing."
    progress_path = follow / "progress.json"
    if not progress_path.exists():
        return None
    try:
        progress = job_control.status(follow)
        spec_path = follow / "spec.json"
        expected = _follow_up_spec(job_dir, record["still"], record)
        if progress is None or not spec_path.is_file() or json.loads(spec_path.read_text(encoding="utf-8")) != expected:
            return "follow_up_pending_recovery", "The existing follow-up job does not match this request; automatic restart is paused."
    except (OSError, ValueError, AdapterError, KeyError, TypeError):
        return "follow_up_pending_recovery", "The existing follow-up job cannot be verified; automatic restart is paused."
    if progress.get("state") == "done":
        return "follow_up_started", None
    if progress.get("state") == "running" and progress.get("pid") is not None:
        return "follow_up_started", None
    return "follow_up_pending_recovery", (
        "A follow-up job exists, but its worker state does not prove a live run or completed job. "
        "Automatic restart is paused to avoid duplicate work; inspect the follow-up job before continuing.")


def _status_view(record: dict) -> dict:
    return {key: record.get(key) for key in ("state", "phone_status", "label", "request_id", "requested_at", "delivered_at",
                                              "follow_up_job", "failure", "identity_proven")}


def _failed(job_dir: Path, record: dict, message: str) -> dict:
    record.update(state="failed", failure=message)
    _write_atomic(job_dir / REQUEST, record)
    return _status_view(record)


def _matching_request(snapshot: dict, record: dict) -> dict:
    session = snapshot.get("session") or {}
    if (session.get("session_id") != record.get("guided_session_id")
            or session.get("source_id") != record.get("source_id")):
        raise AdapterError("The phone snapshot is for a different guided session or enrolled phone.", 409)
    matches = [item for item in snapshot.get("requests", []) if item.get("request_id") == record.get("request_id")]
    if len(matches) != 1:
        raise AdapterError("The phone no longer has exactly one matching capture request.", 409)
    request = matches[0]
    if (request.get("session_id") != record.get("guided_session_id") or request.get("source_id") != record.get("source_id")
            or request.get("revision") != record.get("request_revision")):
        raise AdapterError("The phone capture request session or revision changed; request a fresh view.", 409)
    return request


def _matching_receipt(snapshot: dict, record: dict) -> dict | None:
    candidates = [item for item in snapshot.get("receipts", [])
                  if item.get("request_id") == record.get("request_id")
                  and item.get("session_id") == record.get("guided_session_id")
                  and item.get("source_id") == record.get("source_id")
                  and item.get("request_revision") == record.get("request_revision")
                  and item.get("status") in DELIVERED]
    return min(candidates, key=lambda item: (str(item.get("accepted_at", "")), str(item.get("receipt_id", "")))) if candidates else None


def _has_mismatched_receipt(snapshot: dict, record: dict) -> bool:
    return any(item.get("request_id") == record.get("request_id") and item.get("status") in DELIVERED
               and (item.get("session_id") != record.get("guided_session_id")
                    or item.get("source_id") != record.get("source_id")
                    or item.get("request_revision") != record.get("request_revision"))
               for item in snapshot.get("receipts", []))


def _start_or_resume_follow_up(job_dir: Path, record: dict, start) -> dict:
    expected_name = _follow_up_name(job_dir, record.get("request_id"))
    if not record.get("follow_up_job"):
        record.update(state="follow_up_pending_recovery",
                      failure="This delivered capture has no durable follow-up job id; inspect it before continuing.")
        _write_atomic(job_dir / REQUEST, record)
        return _status_view(record)
    if record.get("follow_up_job") != expected_name:
        record.update(state="follow_up_pending_recovery", failure="The saved follow-up job id does not match this request; automatic restart is paused.")
        _write_atomic(job_dir / REQUEST, record)
        return _status_view(record)
    record["follow_up_job"] = expected_name
    try:
        _follow_up_spec(job_dir, record.get("still") or {}, record)
    except AdapterError as exc:
        return _failed(job_dir, record, str(exc))
    if record.get("state") == "delivered":
        record["state"] = "follow_up_starting"
        _write_atomic(job_dir / REQUEST, record)

    existing = _existing_follow_up(job_dir, record)
    if existing is not None:
        record.update(state=existing[0], failure=existing[1])
        _write_atomic(job_dir / REQUEST, record)
        return _status_view(record)
    try:
        started_name = start(job_dir, record["still"])
    except Exception as exc:
        record.update(state="follow_up_pending_recovery",
                      failure=f"The follow-up start could not be confirmed ({type(exc).__name__}); automatic retry is paused.")
        _write_atomic(job_dir / REQUEST, record)
        return _status_view(record)
    if started_name != expected_name:
        record.update(state="follow_up_pending_recovery", failure="The follow-up start returned a different job id; automatic restart is paused.")
    else:
        record.update(state="follow_up_started", failure=None)
    _write_atomic(job_dir / REQUEST, record)
    return _status_view(record)


@phone_errors
def capture_status(root: Path, session_id: str, job: str, client: ControlClient | None = None, start=_start_follow_up) -> dict:
    """Request state from the phone; on delivery: fetch the still (re-hashed), store it, start the follow-up job once."""
    job_dir = _job(root, session_id, job)
    record = _read(job_dir / REQUEST)
    if record is None:
        return {"state": "none"}
    if record.get("origin_job") != job:
        return _failed(job_dir, record, "The capture request belongs to a different drawing job; request a fresh view.")
    if record["state"] == "requested":
        link = _connected(root)
        if link.get("guided_session_id") != record.get("guided_session_id"):
            return _failed(job_dir, record, "The connected phone session changed; request a fresh view for this drawing job.")
        conf = settings(root)
        if not record.get("source_id") or not record.get("origin_spec_sha256"):
            return _failed(job_dir, record, "This capture request lacks a phone or drawing-spec binding; request a fresh view.")
        client = client or _client(conf)
        try:
            snapshot = client.get_session(link["adapter_token"], record["guided_session_id"])
            request = _matching_request(snapshot, record)
        except AdapterError as exc:
            return _failed(job_dir, record, str(exc))
        record["phone_status"] = request.get("status")
        if request.get("status") in TERMINAL_REQUEST:
            return _failed(job_dir, record, f"The phone request ended as {request['status']}; request a fresh view.")
        receipt = _matching_receipt(snapshot, record)
        if receipt is None and _has_mismatched_receipt(snapshot, record):
            return _failed(job_dir, record, "A delivered receipt does not match this phone session and request revision; request a fresh view.")
        if receipt is None:
            _write_atomic(job_dir / REQUEST, record)
            return _status_view(record)
        if receipt:
            if (not isinstance(receipt.get("receipt_id"), str) or not receipt["receipt_id"]
                    or not _valid_sha256(receipt.get("image_sha256"))):
                return _failed(job_dir, record, "The delivered receipt is missing its id or SHA-256; request a fresh view.")
            try:
                spec_bytes, _spec = _job_spec(job_dir)
            except AdapterError as exc:
                return _failed(job_dir, record, str(exc))
            if hashlib.sha256(spec_bytes).hexdigest() != record["origin_spec_sha256"]:
                return _failed(job_dir, record, "The drawing job changed after this capture was requested; request a fresh view.")
            data, digest = client.asset_bytes(link["adapter_token"], record["guided_session_id"], receipt["image_id"])
            if receipt["image_sha256"].lower() != digest:
                return _failed(job_dir, record, "The delivered image bytes do not match the receipt SHA-256; request a fresh view.")
            try:
                still = _store_still(resolve_session(root, session_id), receipt, data, digest, record)
            except AdapterError as exc:
                return _failed(job_dir, record, str(exc))
            follow_up_job = _follow_up_name(job_dir, record["request_id"])
            record.update(state="delivered", still=still, delivered_at=_iso(_now()), follow_up_job=follow_up_job)
            _write_atomic(job_dir / REQUEST, record)
    if record.get("state") in ("delivered", "follow_up_starting"):
        return _start_or_resume_follow_up(job_dir, record, start)
    return _status_view(record)


__all__ = ["connect_phone", "link_status", "request_capture", "capture_status", "view_label", "ControlClientError"]
