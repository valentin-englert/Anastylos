"""Make the sealed LIVE-01 die (``live01-die@1``) and its secret seed (docs/LIVE-01.md §4).

The die assigns every question slot to ``live`` or ``offline`` and every live question (by ordinal) to wording
``L1`` or ``L0``. Each assignment is a balanced complete randomisation: a list with equal halves, shuffled by a
Fisher–Yates pass whose draws are HMAC-SHA256(seed, label:i). Anyone holding the seed can re-derive the die;
the die records only the seed's SHA-256.

    python make_die.py --out DIR --slots 16 --live-questions 16 --delivery-delay-s 12 \
        --delay-basis traces.json --offline-delay-s 600

Writes ``DIR/live01-die.json`` and ``DIR/live01-die-seed.SEALED.txt`` and prints only their hashes. The operator
must not open either file before the ground truth is hashed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "live01-die@1"
SLOT_ARMS = ("live", "offline")
WORDINGS = ("L1", "L0")
DIE_NAME = "live01-die.json"
SEED_NAME = "live01-die-seed.SEALED.txt"


def _draw(seed: bytes, label: str, i: int) -> int:
    return int.from_bytes(hmac.new(seed, f"{label}:{i}".encode("ascii"), hashlib.sha256).digest(), "big")


def balanced_assignment(seed: bytes, label: str, n: int, arms: tuple[str, str]) -> dict[str, str]:
    """1-based ordinal -> arm; halves equal (the first arm gets the extra one when n is odd)."""
    order = [arms[0]] * ((n + 1) // 2) + [arms[1]] * (n // 2)
    for i in range(n - 1, 0, -1):
        j = _draw(seed, label, i) % (i + 1)
        order[i], order[j] = order[j], order[i]
    return {str(k + 1): arm for k, arm in enumerate(order)}


def derive(seed: bytes, slots: int, live_questions: int) -> dict:
    return {"slots": balanced_assignment(seed, "slot", slots, SLOT_ARMS),
            "live_questions": balanced_assignment(seed, "wording", live_questions, WORDINGS)}


def seed_sha256(seed: bytes) -> str:
    return hashlib.sha256(seed).hexdigest()


def build_die(seed: bytes, slots: int, live_questions: int, delivery_delay_s: float, delay_basis_sha256: str | None,
              offline_delay_s: float, created_at: str) -> dict:
    if slots < 1 or live_questions < 1:
        raise ValueError("slots and live questions must be at least 1")
    if delivery_delay_s <= 0 or offline_delay_s <= 0:
        raise ValueError("delivery and offline delays must be positive seconds")
    return {"schema": SCHEMA, "generator": "tools/live/eval/make_die.py", "created_at": created_at,
            "seed_sha256": seed_sha256(seed), "randomisation": "balanced complete, HMAC-SHA256 Fisher-Yates",
            **derive(seed, slots, live_questions),
            "delivery_delay_s": delivery_delay_s, "delay_basis_sha256": delay_basis_sha256,
            "offline_delay_s": offline_delay_s}


def verify(die: dict, seed: bytes) -> list[str]:
    """Problems found when re-deriving ``die`` from ``seed`` (empty list: the die is the seed's die)."""
    problems = []
    if die.get("seed_sha256") != seed_sha256(seed):
        problems.append("seed does not match the die's seed_sha256")
    expected = derive(seed, len(die.get("slots") or {}), len(die.get("live_questions") or {}))
    for key in ("slots", "live_questions"):
        if die.get(key) != expected[key]:
            problems.append(f"{key} differ from the assignment derived from the seed")
    return problems


def read_seed(path: Path) -> bytes:
    return bytes.fromhex(Path(path).read_text(encoding="ascii").strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--slots", required=True, type=int, help="question slots to assign (live/offline)")
    parser.add_argument("--live-questions", required=True, type=int, help="live question ordinals to assign (L1/L0)")
    parser.add_argument("--delivery-delay-s", required=True, type=float, help="D, fixed before recording (§4.3)")
    parser.add_argument("--delay-basis", type=Path, help="Lane C trace file D was derived from (hashed, not read)")
    parser.add_argument("--offline-delay-s", required=True, type=float, help="start of the offline round after the take")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    die_path, seed_path = args.out / DIE_NAME, args.out / SEED_NAME
    if die_path.exists() or seed_path.exists():
        raise SystemExit(f"{args.out} already holds a die; a die is made once (docs/LIVE-01.md §4)")
    basis = hashlib.sha256(args.delay_basis.read_bytes()).hexdigest() if args.delay_basis else None
    seed = secrets.token_bytes(32)
    die = build_die(seed, args.slots, args.live_questions, args.delivery_delay_s, basis, args.offline_delay_s,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"))
    seed_path.write_text(seed.hex() + "\n", encoding="ascii")
    die_bytes = (json.dumps(die, indent=2, sort_keys=True) + "\n").encode("utf-8")
    die_path.write_bytes(die_bytes)
    print(json.dumps({"die": str(die_path), "die_sha256": hashlib.sha256(die_bytes).hexdigest(),
                      "seed_sha256": die["seed_sha256"], "delivery_delay_s": args.delivery_delay_s,
                      "delay_basis_sha256": basis}, indent=1))


if __name__ == "__main__":
    main()
