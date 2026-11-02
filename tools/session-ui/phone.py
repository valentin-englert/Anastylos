"""Phone pairing from the session page: no terminal needed.

The receiver's own CLI does the work, run as a subprocess against the receiver's data dir (the same state the
running receiver reads): ``pair-code --qr`` makes the pairing QR, ``pair-status --json`` lists requests,
``pair-approve`` / ``pair-reject`` decide, ``guided-enroll`` enrols an approved phone for guided capture.

Approval is the operator's decision: it happens only through ``decide`` after a click on the page, never on its own.
Approving also enrols the phone and writes its new ``source_id`` into ``<root>/capture-settings.json``; a session
opened for the previous phone is dropped so the next connect opens one for the new phone.

Settings read from ``<root>/capture-settings.json``: ``receiver_data_dir``, ``receiver_url``, ``ca_file`` (the
receiver certificate PEM, for the QR pin) and optionally ``phone_host`` (the LAN address the phone reaches; found
automatically when absent).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from adapter import AdapterError

RECEIVER = Path(__file__).resolve().parents[2] / "capture-companion" / "receiver" / "anastylos_capture_receiver.py"
SETTINGS = "capture-settings.json"
LINK = "capture-link.json"
PAIR_QR = "pair-qr.png"
REQUEST_ID = re.compile(r"^[0-9a-f-]{6,64}$")
Run = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def subprocess_run(command: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=60,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _settings(root: Path) -> dict:
    try:
        conf = json.loads((root / SETTINGS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AdapterError("Phone setup is missing: capture-settings.json is not in the workspace.", 409) from None
    if not conf.get("receiver_data_dir"):
        raise AdapterError("capture-settings.json needs receiver_data_dir.", 409)
    return conf


def _write_atomic(path: Path, document: dict) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    os.replace(partial, path)


def _cli(conf: dict, args: list[str], run: Run) -> "subprocess.CompletedProcess[str]":
    return run([sys.executable, "-B", str(RECEIVER), "--data-dir", str(conf["receiver_data_dir"]), *args])


def _fail(done: "subprocess.CompletedProcess[str]", what: str) -> AdapterError:
    reason = (done.stderr or done.stdout or "").strip().splitlines()
    return AdapterError(f"{what}: {reason[-1] if reason else 'the receiver reported an error'}", 502)


def _lan_host() -> str:
    """The address this workstation uses towards the local network (no packet is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def certificate_fingerprint(pem: Path) -> str:
    """Colon-separated SHA-256 of the first certificate in a PEM file."""
    der = ssl.PEM_cert_to_DER_cert(pem.read_text(encoding="ascii").split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n")
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def start_pairing(root: Path, run: Run = subprocess_run) -> dict:
    """Mint a fresh single-use pairing code and its QR picture."""
    conf = _settings(root)
    cert = conf.get("ca_file")
    if not cert or not Path(cert).is_file():
        raise AdapterError("capture-settings.json needs ca_file: the receiver certificate (PEM) for the pairing QR.", 409)
    split = urlsplit(conf.get("receiver_url") or "")
    host = conf.get("phone_host") or _lan_host()
    port = str(split.port or 8443)
    done = _cli(conf, ["pair-code", "--qr", "--host", host, "--port", port, "--cert", str(cert),
                       "--qr-out", str(root / PAIR_QR)], run)
    if done.returncode != 0:
        raise _fail(done, "Could not make the pairing code")
    return {"state": "waiting_for_scan", "host": host, "port": int(port), "ttl_minutes": 5,
            "certificate_sha256": certificate_fingerprint(Path(cert)), "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def pairing_qr_png(root: Path) -> bytes:
    path = root / PAIR_QR
    if not path.is_file():
        raise AdapterError("Show the pairing code first.", 409)
    return path.read_bytes()


def pending_requests(root: Path, run: Run = subprocess_run) -> dict:
    """Phones that scanned the code and wait for the operator's decision."""
    conf = _settings(root)
    done = _cli(conf, ["pair-status", "--json"], run)
    if done.returncode != 0:
        raise _fail(done, "Could not read the pairing requests")
    try:
        listing = json.loads(done.stdout)
    except ValueError:
        raise AdapterError("The receiver's pairing list was not readable.", 502) from None
    now = time.time()
    requests = [
        {"id": request_id, "short_id": request_id[:12], "device": entry.get("device_label"), "status": entry.get("status"),
         "created_at": entry.get("created_at"), "expires_in_s": max(0, int(float(entry.get("expires_at", 0)) - now))}
        for request_id, entry in sorted(listing.get("requests", {}).items())
        if entry.get("status") == "pending" and float(entry.get("expires_at", 0)) > now
    ]
    cert = conf.get("ca_file")
    return {"requests": requests, "certificate_sha256": certificate_fingerprint(Path(cert)) if cert and Path(cert).is_file() else None,
            "source_id": conf.get("source_id")}


def decide(root: Path, request_id: str, decision: str, run: Run = subprocess_run) -> dict:
    """The operator's click: approve (and enrol for guided capture) or reject one pending request."""
    if decision not in ("approve", "reject"):
        raise AdapterError("Decision must be approve or reject.", 400)
    if not REQUEST_ID.match(request_id or ""):
        raise AdapterError("Unknown pairing request.", 404)
    conf = _settings(root)
    if request_id not in {r["id"] for r in pending_requests(root, run)["requests"]}:
        raise AdapterError("That pairing request is gone or no longer pending.", 404)
    if decision == "reject":
        done = _cli(conf, ["pair-reject", request_id], run)
        if done.returncode != 0:
            raise _fail(done, "Could not reject the request")
        return {"decision": "rejected", "request": request_id}
    done = _cli(conf, ["pair-approve", request_id], run)
    if done.returncode != 0:
        raise _fail(done, "Could not approve the request")
    enrolled = _cli(conf, ["guided-enroll", request_id], run)
    match = re.search(r"source_id:\s*(\S+)", enrolled.stdout or "")
    if enrolled.returncode != 0 or not match:
        raise _fail(enrolled, "Approved, but the guided enrolment failed")
    conf["source_id"] = match.group(1)
    _write_atomic(root / SETTINGS, conf)
    (root / LINK).unlink(missing_ok=True)  # a session opened for the previous phone no longer fits
    return {"decision": "approved", "request": request_id, "source_id": conf["source_id"]}
