"""CLI for the Core Capture Evidence Index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import SCHEMA
from .discover import list_staged_packages
from .ingest import ingest_receiver
from .paths import is_uuid
from .store import EvidenceStore


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_ingest(args: argparse.Namespace) -> int:
    if args.session_id and not is_uuid(args.session_id):
        print("error: --session-id must be a UUID", file=sys.stderr)
        return 2
    if args.package_id and not is_uuid(args.package_id):
        print("error: --package-id must be a UUID", file=sys.stderr)
        return 2
    if args.associate_session and not is_uuid(args.associate_session):
        print("error: --associate-session must be a UUID", file=sys.stderr)
        return 2

    with EvidenceStore(Path(args.core_dir)) as store:
        results = ingest_receiver(
            store,
            Path(args.receiver_data_dir),
            session_id=args.session_id,
            package_id=args.package_id,
            associate_session=args.associate_session,
        )
    summary = {
        "schema": SCHEMA,
        "ingested": sum(1 for r in results if r.result == "complete"),
        "duplicate": sum(1 for r in results if r.result == "duplicate"),
        "rejected": sum(1 for r in results if r.result == "rejected"),
        "conflict": sum(1 for r in results if r.result == "conflict"),
        "results": [
            {
                "package_id": r.package_id,
                "result": r.result,
                "detail": r.detail,
                "observation_ids": r.observation_ids,
                "image_sha256s": r.image_sha256s,
            }
            for r in results
        ],
    }
    _print(summary)
    if any(r.result in ("rejected", "conflict") for r in results):
        return 1
    return 0


def cmd_lookup(args: argparse.Namespace) -> int:
    if bool(args.session_id) == bool(args.observation_id):
        print(
            "error: provide exactly one of --session-id or --observation-id",
            file=sys.stderr,
        )
        return 2
    with EvidenceStore(Path(args.core_dir)) as store:
        if args.session_id:
            if not is_uuid(args.session_id):
                print("error: --session-id must be a UUID", file=sys.stderr)
                return 2
            rows = store.lookup_session(args.session_id)
            _print(
                {
                    "schema": SCHEMA,
                    "session_id": args.session_id,
                    "count": len(rows),
                    "observations": rows,
                }
            )
            return 0
        if not is_uuid(args.observation_id):
            print("error: --observation-id must be a UUID", file=sys.stderr)
            return 2
        row = store.get_observation(args.observation_id)
        if row is None:
            print(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "observation_id": args.observation_id,
                        "found": False,
                    }
                )
            )
            return 1
        _print({"schema": SCHEMA, "found": True, **row})
        return 0


def cmd_status(args: argparse.Namespace) -> int:
    with EvidenceStore(Path(args.core_dir)) as store:
        payload = store.status()
        payload["ledger_tail"] = store.ledger_tail(args.ledger_limit)
        if args.receiver_data_dir:
            staged = list_staged_packages(
                Path(args.receiver_data_dir), session_id=args.session_id
            )
            payload["receiver_staged_visible"] = len(staged)
        _print(payload)
    return 0


def cmd_verify_blob(args: argparse.Namespace) -> int:
    with EvidenceStore(Path(args.core_dir)) as store:
        data = store.blob_bytes(args.sha256)
    digest = __import__("hashlib").sha256(data).hexdigest()
    ok = digest == args.sha256 and (
        args.expect_bytes is None or len(data) == args.expect_bytes
    )
    _print(
        {
            "sha256": args.sha256,
            "byte_length": len(data),
            "matches": ok,
            "computed_sha256": digest,
        }
    )
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anastylos-core-ingest",
        description=(
            "Ingest Receiver-staged capture packages into the Core Capture "
            "Evidence Index. Does not write Assembly Memory."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="discover and ingest staged packages")
    p_ingest.add_argument(
        "--receiver-data-dir",
        required=True,
        help="Receiver --data-dir (read-only; outside the Git repo)",
    )
    p_ingest.add_argument(
        "--core-dir",
        required=True,
        help="Core evidence directory (operator-owned; outside the Git repo)",
    )
    p_ingest.add_argument("--session-id", help="only packages listing this capture session")
    p_ingest.add_argument("--package-id", help="ingest a single package id")
    p_ingest.add_argument(
        "--associate-session",
        help=(
            "explicit Core session UUID for association; never inferred from a "
            "missing remote request id"
        ),
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_lookup = sub.add_parser("lookup", help="retrieve ingested evidence")
    p_lookup.add_argument("--core-dir", required=True)
    p_lookup.add_argument("--session-id")
    p_lookup.add_argument("--observation-id")
    p_lookup.set_defaults(func=cmd_lookup)

    p_status = sub.add_parser("status", help="summarize the Core evidence store")
    p_status.add_argument("--core-dir", required=True)
    p_status.add_argument("--receiver-data-dir")
    p_status.add_argument("--session-id")
    p_status.add_argument("--ledger-limit", type=int, default=20)
    p_status.set_defaults(func=cmd_status)

    p_verify = sub.add_parser(
        "verify-blob", help="re-hash a stored original image by sha256"
    )
    p_verify.add_argument("--core-dir", required=True)
    p_verify.add_argument("--sha256", required=True)
    p_verify.add_argument("--expect-bytes", type=int)
    p_verify.set_defaults(func=cmd_verify_blob)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
