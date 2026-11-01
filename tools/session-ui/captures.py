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
import job_control  # noqa: E402
from control_client import ControlClient, ControlClientError  # noqa: E402

SETTINGS = "capture-settings.json"
LINK = "capture-link.json"
REQUEST = "capture-request.json"
PRINCIPAL = "anastylos-session-ui"
REQUESTABLE = {"NEEDS_CAPTURE"}
DELIVERED = {"package_only", "qualified"}
TERMINAL_REQUEST = {"CANCELLED", "EXPIRED", "SUPERSEDED", "UNREACHABLE"}
# Top-down view over the A3 sheet centre, the phone's own "a3-top-center" viewpoint (camera 450 mm above the board).
TOP_DOWN_CENTER_MM = (198.0, 136.5, -450.0)
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
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, path)


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
    path = resolve_session(root, session_id) / "drawings" / "jobs" / job
    if not (path / "progress.json").is_file():
        raise AdapterError(f"Unknown drawing job {job!r}.", 404)
    return path


def view_label(spec: dict) -> str:
    """What the phone shows: the part, the requested side and the one rule that makes the still usable."""
    side = spec.get("view_side") or "requested side"
    return f"{spec['part']}, {side} up, on the sheet, hands off"[:120]


def request_body(conf: dict, link: dict, spec: dict, reasons: list[str], revision: int) -> dict:
    x, y, w, h = spec["region_mm"]
    cx, cy, cz = TOP_DOWN_CENTER_MM
    return {"operation_id": str(uuid.uuid4()), "session_id": link["guided_session_id"], "expected_session_revision": revision,
            "source_id": conf["source_id"], "target_label": view_label(spec), "board_id": conf["board_id"],
            "calibration_id": conf["calibration_id"],
            "target": {"kind": "pose",
                       "T_B_C": {"from": conf.get("camera_frame", "camera:0"), "to": conf["board_frame"], "units": "mm",
                                 "matrix": [1, 0, 0, cx, 0, 1, 0, cy, 0, 0, 1, cz, 0, 0, 0, 1]},
                       "translation_tolerance_mm": [15, 15, 20], "rotation_tolerance_deg": 5,
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
    session = client.get_session(link["adapter_token"], link["guided_session_id"])["session"]
    spec = json.loads((job_dir / "spec.json").read_text(encoding="utf-8"))
    created = client.request_view(link["adapter_token"], link["guided_session_id"],
                                  request_body(conf, link, spec, result.get("reasons", []), session["revision"]))
    record = {"schema": "anastylos-capture-request@1", "state": "requested", "origin_job": job,
              "request_id": created["request"]["request_id"], "request_revision": created["request"]["revision"],
              "guided_session_id": link["guided_session_id"], "label": created["request"]["target_label"],
              "reasons": result.get("reasons", []), "requested_at": _iso(_now()), "follow_up_job": None,
              "identity_proven": False}
    _write_atomic(job_dir / REQUEST, record)
    return capture_status(root, session_id, job, client)


def _store_still(session_dir: Path, receipt: dict, data: bytes, digest: str, record: dict) -> dict:
    captures = session_dir / "drawings" / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    still = captures / f"{receipt['image_id']}.jpg"
    if not still.exists():
        tmp = still.with_name(still.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, still)
    evidence = {"schema": "anastylos-capture-evidence@1", "image_id": receipt["image_id"], "sha256": digest,
                "receipt_status": receipt["status"], "receipt_id": receipt.get("receipt_id"),
                "request_id": record["request_id"], "request_revision": receipt.get("request_revision"),
                "guided_session_id": record["guided_session_id"], "received_at": _iso(_now()),
                "requested_for_job": record["origin_job"], "identity_proven": False}
    _write_atomic(captures / f"{receipt['image_id']}.json", evidence)
    return {"path": str(still), "sha256": digest, "request_id": record["request_id"], "image_id": receipt["image_id"]}


def _start_follow_up(job_dir: Path, still: dict) -> str:
    spec = json.loads((job_dir / "spec.json").read_text(encoding="utf-8"))
    spec["still"] = still
    follow = job_dir.parent / f"{job_dir.name.split('-capture-')[0]}-capture-{time.strftime('%Y%m%d-%H%M%S')}"
    follow.mkdir(parents=True)
    spec_path = follow / "requested-spec.json"
    spec_path.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    job_control.start(spec_path, follow)
    return follow.name


@phone_errors
def capture_status(root: Path, session_id: str, job: str, client: ControlClient | None = None, start=_start_follow_up) -> dict:
    """Request state from the phone; on delivery: fetch the still (re-hashed), store it, start the follow-up job once."""
    job_dir = _job(root, session_id, job)
    record = _read(job_dir / REQUEST)
    if record is None:
        return {"state": "none"}
    if record["state"] == "requested":
        conf, link = settings(root), _connected(root)
        client = client or _client(conf)
        snapshot = client.get_session(link["adapter_token"], record["guided_session_id"])
        request = next((r for r in snapshot["requests"] if r["request_id"] == record["request_id"]), {})
        receipt = next((r for r in snapshot["receipts"] if r["request_id"] == record["request_id"] and r["status"] in DELIVERED), None)
        record["phone_status"] = request.get("status")
        if receipt:
            data, digest = client.asset_bytes(link["adapter_token"], record["guided_session_id"], receipt["image_id"])
            if receipt.get("image_sha256") and receipt["image_sha256"] != digest:
                raise AdapterError("The delivered image does not match its receipt hash.", 502)
            still = _store_still(resolve_session(root, session_id), receipt, data, digest, record)
            record.update(state="delivered", still=still, delivered_at=_iso(_now()))
            _write_atomic(job_dir / REQUEST, record)  # recorded before the job starts: a crash never starts it twice
            record.update(state="follow_up_started", follow_up_job=start(job_dir, still))
        elif request.get("status") in TERMINAL_REQUEST:
            record.update(state="failed", failure=request.get("status"))
        _write_atomic(job_dir / REQUEST, record)
    elif record["state"] == "delivered":  # crashed between delivery and start
        record.update(state="follow_up_started", follow_up_job=start(job_dir, record["still"]))
        _write_atomic(job_dir / REQUEST, record)
    return {k: record.get(k) for k in ("state", "phone_status", "label", "request_id", "requested_at", "delivered_at",
                                        "follow_up_job", "failure", "identity_proven")}


__all__ = ["connect_phone", "link_status", "request_capture", "capture_status", "view_label", "ControlClientError"]
