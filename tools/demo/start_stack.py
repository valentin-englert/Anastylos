"""Controlled start of the demo stack (session UI + capture receiver) from THIS git tree, with a work-data backup first.

    python tools/demo/start_stack.py --config <stack-config.json> [--dry-run] [--allow-dirty]

The script never stops anything. It refuses to start when a check fails (dirty tree, busy port, IP not on this machine,
missing key, missing files). The model API key is read from the user environment, never printed, never written to the launch
record, and handed to the two child processes only through their own environment. See README.md.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TREE = Path(__file__).resolve().parents[2]
KEY_ENV = "MODEL_API_KEY"
SESSION_UI_HOST = "127.0.0.1"  # the session UI is localhost only by design
RECORD_SCHEMA = "anastylos-launch-record@1"
CONFIG_KEYS = ("workspace_root", "receiver_data_dir", "backup_root", "host", "session_ui_port", "receiver_port", "cert", "key")
OK, WARN, FAIL = "ok", "warn", "fail"


# ---------------------------------------------------------------- configuration

@dataclass(frozen=True)
class Config:
    workspace_root: Path
    receiver_data_dir: Path
    backup_root: Path
    host: str
    session_ui_port: int
    receiver_port: int
    cert: Path
    key: Path
    python: str


class ConfigError(Exception):
    pass


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    missing = [k for k in CONFIG_KEYS if k not in raw]
    if missing:
        raise ConfigError(f"config {path} lacks: {', '.join(missing)}")
    try:
        ipaddress.IPv4Address(raw["host"])
    except ValueError as exc:
        raise ConfigError(f"host must be an IPv4 address: {exc}") from exc
    ports = {name: raw[name] for name in ("session_ui_port", "receiver_port")}
    for name, port in ports.items():
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ConfigError(f"{name} must be a port number")
    if ports["session_ui_port"] == ports["receiver_port"]:
        raise ConfigError("session_ui_port and receiver_port must differ")
    return Config(workspace_root=Path(raw["workspace_root"]), receiver_data_dir=Path(raw["receiver_data_dir"]),
                  backup_root=Path(raw["backup_root"]), host=raw["host"], session_ui_port=ports["session_ui_port"],
                  receiver_port=ports["receiver_port"], cert=Path(raw["cert"]), key=Path(raw["key"]),
                  python=raw.get("python") or sys.executable)


# ---------------------------------------------------------------- the machine (replaced by a fake in tests)

class System:
    """Everything that touches git, the network, the user environment and processes."""

    def git(self, tree: Path, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()[:200]}")
        return done.stdout

    def port_in_use(self, host: str, port: int) -> bool:
        for target in {host, "127.0.0.1"}:
            with socket.socket() as probe:
                probe.settimeout(0.5)
                if probe.connect_ex((target, port)) == 0:
                    return True
        with socket.socket() as probe:
            try:
                probe.bind((host, port))
            except OSError:
                return True
        return False

    def ip_present(self, ip: str) -> bool:
        with socket.socket() as probe:
            try:
                probe.bind((ip, 0))  # only an address assigned to an interface of this machine can be bound
            except OSError:
                return False
        return True

    def user_env(self, name: str) -> str | None:
        """The persistent user-scope value (Windows registry), else this process's environment. Never printed."""
        if os.name == "nt":
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                    value, _ = winreg.QueryValueEx(key, name)
                    if value and str(value).strip():
                        return str(value).strip()
            except OSError:
                pass
        return (os.environ.get(name) or "").strip() or None

    def now(self) -> datetime:
        return datetime.now()

    def spawn(self, argv: list[str], cwd: Path, env: dict[str, str], log: Path) -> int:
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        with open(log, "ab") as stream:
            process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=stream,
                                       stderr=subprocess.STDOUT, creationflags=flags, start_new_session=os.name != "nt")
        return process.pid

    def wait_listening(self, host: str, port: int, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                probe.settimeout(0.5)
                if probe.connect_ex((host, port)) == 0:
                    return True
            time.sleep(0.25)
        return False


# ---------------------------------------------------------------- planning (pure given a System)

@dataclass(frozen=True)
class Check:
    name: str
    level: str
    detail: str


@dataclass(frozen=True)
class TreeState:
    root: Path
    sha: str
    branch: str
    changes: tuple[str, ...]

    @property
    def dirty(self) -> bool:
        return bool(self.changes)


@dataclass
class Plan:
    tree: TreeState
    checks: list[Check]
    backup_dir: Path
    backup_sources: list[Path]
    commands: dict[str, list[str]]

    @property
    def ok(self) -> bool:
        return all(c.level != FAIL for c in self.checks)


def inspect_tree(tree: Path, system: System) -> TreeState:
    sha = system.git(tree, "rev-parse", "HEAD").strip()
    branch = system.git(tree, "rev-parse", "--abbrev-ref", "HEAD").strip()
    changes = tuple(line for line in system.git(tree, "status", "--porcelain").splitlines() if line.strip())
    return TreeState(tree, sha, branch, changes)


def script_paths(tree: Path) -> dict[str, Path]:
    return {"session_ui": tree / "tools" / "session-ui" / "server.py",
            "receiver": tree / "capture-companion" / "receiver" / "anastylos_capture_receiver.py",
            "transcriber": tree / "tools" / "live" / "providers" / "transcribe.py"}


def build_commands(config: Config, tree: Path) -> dict[str, list[str]]:
    """Absolute paths of THIS tree only. The key is not part of any command line."""
    scripts = script_paths(tree)
    return {
        "session_ui": [config.python, "-B", str(scripts["session_ui"]), "--workspace-root", str(config.workspace_root),
                       "--host", SESSION_UI_HOST, "--port", str(config.session_ui_port)],
        "receiver": [config.python, "-B", str(scripts["receiver"]), "--data-dir", str(config.receiver_data_dir), "serve",
                     "--host", config.host, "--port", str(config.receiver_port), "--cert", str(config.cert), "--key", str(config.key),
                     "--voice-transcriber", str(scripts["transcriber"]), "--session-ui-port", str(config.session_ui_port)],
    }


def backup_sources(workspace_root: Path) -> list[Path]:
    """Every session's memory.jsonl and every file below its drawings folder (jobs and specs)."""
    if not workspace_root.is_dir():
        return []
    files = list(workspace_root.glob("*/memory/memory.jsonl"))
    for drawings in workspace_root.glob("*/drawings"):
        files.extend(p for p in drawings.rglob("*") if p.is_file())
    return sorted(files)


def _inside(child: Path, parent: Path) -> bool:
    child, parent = child.resolve(), parent.resolve()
    return child == parent or parent in child.parents


def make_plan(config: Config, tree: Path, system: System, allow_dirty: bool = False) -> Plan:
    checks: list[Check] = []
    state = inspect_tree(tree, system)
    if state.dirty:
        checks.append(Check("tree", WARN if allow_dirty else FAIL,
                            f"{state.root} @ {state.sha[:12]} ({state.branch}) is DIRTY: {len(state.changes)} change(s)"
                            + ("" if allow_dirty else "; commit them or pass --allow-dirty")))
    else:
        checks.append(Check("tree", OK, f"{state.root} @ {state.sha[:12]} ({state.branch}) is clean"))
    scripts = script_paths(tree)
    missing = [str(p) for p in scripts.values() if not p.is_file()]
    checks.append(Check("scripts", FAIL if missing else OK, f"missing in this tree: {', '.join(missing)}" if missing else "session UI, receiver and transcriber found in this tree"))
    for label, host, port in (("session UI", SESSION_UI_HOST, config.session_ui_port), ("receiver", config.host, config.receiver_port)):
        busy = system.port_in_use(host, port)
        checks.append(Check(f"port {port}", FAIL if busy else OK,
                            f"{label} port {host}:{port} is already in use; stop the old process first" if busy else f"{label} port {host}:{port} is free"))
    present = system.ip_present(config.host)
    checks.append(Check("ip", OK if present else FAIL,
                        f"{config.host} is on a local interface" if present else f"{config.host} is not assigned to any interface of this machine"))
    checks.append(Check("model key", OK if system.user_env(KEY_ENV) else FAIL,
                        f"{KEY_ENV} is available in the user environment (value not shown)" if system.user_env(KEY_ENV)
                        else f"{KEY_ENV} is not set in the user environment"))
    for name, path, kind in (("workspace", config.workspace_root, "dir"), ("receiver data dir", config.receiver_data_dir, "dir"),
                             ("certificate", config.cert, "file"), ("certificate key", config.key, "file")):
        exists = path.is_dir() if kind == "dir" else path.is_file()
        checks.append(Check(name, OK if exists else FAIL, f"{path}" if exists else f"{path} does not exist"))
    stamp = system.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = config.backup_root / f"{stamp}-{state.sha[:8]}"
    if _inside(backup_dir, config.workspace_root) or _inside(config.workspace_root, config.backup_root):
        checks.append(Check("backup folder", FAIL, "backup_root and workspace_root must not contain each other"))
    elif backup_dir.exists():
        checks.append(Check("backup folder", FAIL, f"{backup_dir} already exists; not overwriting"))
    sources = backup_sources(config.workspace_root)
    memories = sum(p.name == "memory.jsonl" for p in sources)
    checks.append(Check("backup", WARN if not memories else OK, f"{memories} memory.jsonl and {len(sources) - memories} drawing file(s) to copy to {backup_dir}"
                        if memories else f"no */memory/memory.jsonl found under {config.workspace_root}; nothing to protect (drawing files: {len(sources)})"))
    return Plan(state, checks, backup_dir, sources, build_commands(config, tree))


# ---------------------------------------------------------------- execution

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def backup_work_data(plan: Plan, workspace_root: Path) -> list[dict]:
    """Copy (never move) the planned files below ``<backup_dir>/workspace`` and verify every copy by SHA-256."""
    manifest = []
    target_root = plan.backup_dir / "workspace"
    for source in plan.backup_sources:
        relative = source.relative_to(workspace_root)
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_sha, copy_sha = _sha256(source), _sha256(target)
        if source_sha != copy_sha:
            raise RuntimeError(f"backup copy of {relative} differs from its source; not starting")
        manifest.append({"path": relative.as_posix(), "bytes": target.stat().st_size, "sha256": copy_sha})
    return manifest


def _write_json(path: Path, doc: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def launch(plan: Plan, config: Config, system: System, config_path: Path, startup_timeout_s: float = 20.0) -> tuple[dict, bool]:
    """Back up, start both processes with the key only in their own environment, write the launch record."""
    plan.backup_dir.mkdir(parents=True)
    manifest = backup_work_data(plan, config.workspace_root)
    key = system.user_env(KEY_ENV)
    if not key:
        raise RuntimeError(f"{KEY_ENV} vanished from the user environment; not starting")
    env = {**os.environ, KEY_ENV: key}
    record = {"schema": RECORD_SCHEMA, "started_at": system.now().isoformat(timespec="seconds"),
              "tree": {"root": str(plan.tree.root), "sha": plan.tree.sha, "branch": plan.tree.branch, "dirty": plan.tree.dirty,
                       "changes": list(plan.tree.changes[:50])},
              "config_file": str(config_path), "workspace_root": str(config.workspace_root),
              "receiver_data_dir": str(config.receiver_data_dir), "host": config.host, "ports": {"session_ui": config.session_ui_port, "receiver": config.receiver_port},
              "backup": {"dir": str(plan.backup_dir), "files": manifest}, "processes": {}, "listening": {}}
    record_path = plan.backup_dir / "launch-record.json"
    _write_json(record_path, record)
    endpoints = {"session_ui": (SESSION_UI_HOST, config.session_ui_port), "receiver": (config.host, config.receiver_port)}
    for name, argv in plan.commands.items():
        log = plan.backup_dir / f"{name}.log"
        pid = system.spawn(argv, plan.tree.root, env, log)
        record["processes"][name] = {"pid": pid, "argv": argv, "log": str(log)}
        _write_json(record_path, record)
    for name, (host, port) in endpoints.items():
        record["listening"][name] = system.wait_listening(host, port, startup_timeout_s)
    _write_json(record_path, record)
    return record, all(record["listening"].values())


# ---------------------------------------------------------------- command line

def _print_plan(plan: Plan) -> None:
    for check in plan.checks:
        print(f"[{check.level.upper():4}] {check.name}: {check.detail}")
    print("commands (key injected into the child environment only):")
    for name, argv in plan.commands.items():
        print(f"  {name}: {subprocess.list2cmdline(argv)}")
    print(f"backup folder: {plan.backup_dir}")


def main(argv: list[str] | None = None, system: System | None = None, tree: Path = TREE) -> int:
    parser = argparse.ArgumentParser(description="Controlled start of the ANASTYLOS demo stack from this tree.")
    parser.add_argument("--config", required=True, type=Path, help="stack config JSON (see stack-config.example.json)")
    parser.add_argument("--dry-run", action="store_true", help="run the checks and print the plan; copy and start nothing")
    parser.add_argument("--allow-dirty", action="store_true", help="start although the git tree has uncommitted changes")
    parser.add_argument("--startup-timeout", type=float, default=20.0, help="seconds to wait for each port to listen")
    args = parser.parse_args(argv)
    system = system or System()
    try:
        config = load_config(args.config)
        plan = make_plan(config, tree, system, args.allow_dirty)
    except (ConfigError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_plan(plan)
    if not plan.ok:
        print("Refusing to start: fix the FAIL lines above.", file=sys.stderr)
        return 2
    if args.dry_run:
        print("dry run: nothing copied, nothing started.")
        return 0
    try:
        record, listening = launch(plan, config, system, args.config.resolve(), args.startup_timeout)
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for name, info in record["processes"].items():
        print(f"started {name}: pid {info['pid']}, listening {record['listening'][name]}, log {info['log']}")
    print(f"launch record: {plan.backup_dir / 'launch-record.json'}")
    if not listening:
        print("A process did not start listening; read its log and the launch record. Nothing was stopped.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
