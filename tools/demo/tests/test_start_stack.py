"""start_stack: planning, refusals, backup and launch record, all against a fake machine (nothing is started)."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import start_stack  # noqa: E402

SECRET = "sk-test-secret-value-123"
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


class FakeSystem(start_stack.System):
    def __init__(self, changes=(), busy=(), ips=("192.168.0.10", "127.0.0.1"), key=SECRET, listening=True):
        self.changes, self.busy, self.ips, self.key, self.listening = list(changes), set(busy), set(ips), key, listening
        self.spawned: list[tuple[list[str], Path, dict, Path]] = []

    def git(self, tree, *args):
        if args[0] == "status":
            return "".join(f" M {c}\n" for c in self.changes)
        return SHA + "\n" if args[-1] == "HEAD" and "--abbrev-ref" not in args else "claude/test\n"

    def port_in_use(self, host, port):
        return port in self.busy

    def ip_present(self, ip):
        return ip in self.ips

    def user_env(self, name):
        return self.key if name == start_stack.KEY_ENV else None

    def now(self):
        return datetime(2026, 11, 2, 9, 30, 15)

    def spawn(self, argv, cwd, env, log):
        self.spawned.append((argv, cwd, env, log))
        return 4000 + len(self.spawned)

    def wait_listening(self, host, port, timeout_s):
        return self.listening


class StartStackBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tree = root / "tree"
        for rel in ("tools/session-ui/server.py", "capture-companion/receiver/anastylos_capture_receiver.py",
                    "tools/live/providers/transcribe.py"):
            (self.tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / rel).write_text("# stub", encoding="utf-8")
        self.workspace = root / "workspace"
        (self.workspace / "s1" / "memory").mkdir(parents=True)
        (self.workspace / "s1" / "memory" / "memory.jsonl").write_text('{"event": 1}\n', encoding="utf-8")
        (self.workspace / "s1" / "drawings" / "jobs" / "j1").mkdir(parents=True)
        (self.workspace / "s1" / "drawings" / "jobs" / "j1" / "progress.json").write_text('{"state": "done"}', encoding="utf-8")
        (self.workspace / "s1" / "other.txt").write_text("not backed up", encoding="utf-8")
        data = root / "receiver-data"
        data.mkdir()
        (data / "cert.pem").write_text("c", encoding="utf-8")
        (data / "key.pem").write_text("k", encoding="utf-8")
        self.config_path = root / "stack.json"
        self.config_doc = {"workspace_root": str(self.workspace), "receiver_data_dir": str(data), "backup_root": str(root / "backups"),
                           "host": "192.168.0.10", "session_ui_port": 8079, "receiver_port": 8443,
                           "cert": str(data / "cert.pem"), "key": str(data / "key.pem"), "python": "py.exe"}
        self.config_path.write_text(json.dumps(self.config_doc), encoding="utf-8")
        self.config = start_stack.load_config(self.config_path)

    def run_main(self, system, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = start_stack.main(["--config", str(self.config_path), *args], system=system, tree=self.tree)
        return code, out.getvalue(), err.getvalue()


class PlanTest(StartStackBase):
    def levels(self, plan):
        return {c.name: c.level for c in plan.checks}

    def test_clean_tree_with_everything_present_is_ok(self):
        plan = start_stack.make_plan(self.config, self.tree, FakeSystem())
        self.assertTrue(plan.ok, [c for c in plan.checks if c.level == start_stack.FAIL])
        self.assertEqual(plan.tree.sha, SHA)
        self.assertEqual(plan.backup_dir.name, "20261102-093015-a1b2c3d4")

    def test_commands_use_absolute_paths_of_this_tree_and_never_contain_the_key(self):
        plan = start_stack.make_plan(self.config, self.tree, FakeSystem())
        ui, receiver = plan.commands["session_ui"], plan.commands["receiver"]
        self.assertEqual(ui[2], str(self.tree / "tools" / "session-ui" / "server.py"))
        self.assertEqual(receiver[2], str(self.tree / "capture-companion" / "receiver" / "anastylos_capture_receiver.py"))
        self.assertEqual(receiver[receiver.index("--voice-transcriber") + 1], str(self.tree / "tools" / "live" / "providers" / "transcribe.py"))
        self.assertEqual(receiver[receiver.index("--session-ui-port") + 1], "8079")
        self.assertEqual(ui[ui.index("--host") + 1], "127.0.0.1")
        self.assertEqual(receiver[receiver.index("--host") + 1], "192.168.0.10")
        for argv in plan.commands.values():
            self.assertTrue(all(Path(a).is_absolute() for a in argv if a.endswith((".py", ".pem")) and a != "py.exe"))
            self.assertNotIn(SECRET, " ".join(argv))

    def test_dirty_tree_fails_unless_allowed(self):
        dirty = FakeSystem(changes=["tools/live/x.py"])
        self.assertEqual(self.levels(start_stack.make_plan(self.config, self.tree, dirty))["tree"], start_stack.FAIL)
        allowed = start_stack.make_plan(self.config, self.tree, dirty, allow_dirty=True)
        self.assertEqual(self.levels(allowed)["tree"], start_stack.WARN)
        self.assertTrue(allowed.ok)
        self.assertTrue(allowed.tree.dirty)

    def test_busy_ports_missing_ip_and_missing_key_each_fail(self):
        for system, check in ((FakeSystem(busy={8079}), "port 8079"), (FakeSystem(busy={8443}), "port 8443"),
                              (FakeSystem(ips=()), "ip"), (FakeSystem(key=None), "model key")):
            with self.subTest(check=check):
                plan = start_stack.make_plan(self.config, self.tree, system)
                self.assertEqual(self.levels(plan)[check], start_stack.FAIL)
                self.assertFalse(plan.ok)

    def test_key_value_is_not_in_any_check_text(self):
        plan = start_stack.make_plan(self.config, self.tree, FakeSystem())
        self.assertNotIn(SECRET, json.dumps([c.detail for c in plan.checks]))

    def test_script_missing_from_this_tree_fails(self):
        (self.tree / "tools" / "live" / "providers" / "transcribe.py").unlink()
        self.assertEqual(self.levels(start_stack.make_plan(self.config, self.tree, FakeSystem()))["scripts"], start_stack.FAIL)

    def test_backup_sources_are_memories_and_drawing_files_only(self):
        names = [p.relative_to(self.workspace).as_posix() for p in start_stack.backup_sources(self.workspace)]
        self.assertEqual(names, ["s1/drawings/jobs/j1/progress.json", "s1/memory/memory.jsonl"])

    def test_backup_folder_inside_workspace_is_refused(self):
        inside = start_stack.Config(**{**self.config.__dict__, "backup_root": self.workspace / "backups"})
        self.assertEqual(self.levels(start_stack.make_plan(inside, self.tree, FakeSystem()))["backup folder"], start_stack.FAIL)


class ConfigTest(StartStackBase):
    def test_missing_key_and_bad_values_are_rejected(self):
        for change in ({"host": "not-an-ip"}, {"session_ui_port": 0}, {"receiver_port": 8079}, {"session_ui_port": "8079"}):
            with self.subTest(change=change):
                self.config_path.write_text(json.dumps({**self.config_doc, **change}), encoding="utf-8")
                with self.assertRaises(start_stack.ConfigError):
                    start_stack.load_config(self.config_path)
        del self.config_doc["cert"]
        self.config_path.write_text(json.dumps(self.config_doc), encoding="utf-8")
        with self.assertRaisesRegex(start_stack.ConfigError, "cert"):
            start_stack.load_config(self.config_path)

    def test_example_config_has_every_required_key(self):
        example = json.loads((Path(__file__).resolve().parents[1] / "stack-config.example.json").read_text(encoding="utf-8"))
        self.assertTrue(set(start_stack.CONFIG_KEYS) <= set(example))


class MainTest(StartStackBase):
    def test_dry_run_prints_the_plan_and_changes_nothing(self):
        system = FakeSystem()
        code, out, _ = self.run_main(system, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertIn(SHA[:12], out)
        self.assertNotIn(SECRET, out)
        self.assertEqual(system.spawned, [])
        self.assertFalse((self.config.backup_root).exists())

    def test_dirty_tree_blocks_start_and_backup(self):
        system = FakeSystem(changes=["a.py"])
        code, _, err = self.run_main(system)
        self.assertEqual(code, 2)
        self.assertIn("Refusing", err)
        self.assertEqual(system.spawned, [])
        self.assertFalse(self.config.backup_root.exists())

    def test_start_backs_up_copies_then_spawns_both_with_key_only_in_child_env(self):
        system = FakeSystem()
        code, out, err = self.run_main(system)
        self.assertEqual((code, err), (0, ""), out)
        backup = self.config.backup_root / "20261102-093015-a1b2c3d4"
        self.assertEqual((backup / "workspace" / "s1" / "memory" / "memory.jsonl").read_text(), '{"event": 1}\n')
        self.assertTrue((backup / "workspace" / "s1" / "drawings" / "jobs" / "j1" / "progress.json").is_file())
        self.assertTrue((self.workspace / "s1" / "memory" / "memory.jsonl").is_file())  # copied, not moved
        self.assertFalse((backup / "workspace" / "s1" / "other.txt").exists())
        self.assertEqual([argv[2].split("\\")[-1].split("/")[-1] for argv, *_ in system.spawned],
                         ["server.py", "anastylos_capture_receiver.py"])
        for argv, cwd, env, log in system.spawned:
            self.assertEqual((env[start_stack.KEY_ENV], cwd, log.parent), (SECRET, self.tree, backup))
            self.assertNotIn(SECRET, " ".join(argv))
        self.assertNotIn(SECRET, out)

    def test_launch_record_has_sha_paths_pids_start_time_and_no_secret(self):
        system = FakeSystem()
        self.run_main(system)
        record_path = self.config.backup_root / "20261102-093015-a1b2c3d4" / "launch-record.json"
        text = record_path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET, text)
        record = json.loads(text)
        self.assertEqual((record["schema"], record["tree"]["sha"], record["tree"]["dirty"], record["started_at"]),
                         (start_stack.RECORD_SCHEMA, SHA, False, "2026-11-02T09:30:15"))
        self.assertEqual({k: v["pid"] for k, v in record["processes"].items()}, {"session_ui": 4001, "receiver": 4002})
        self.assertEqual(record["tree"]["root"], str(self.tree))
        self.assertEqual({f["path"] for f in record["backup"]["files"]}, {"s1/memory/memory.jsonl", "s1/drawings/jobs/j1/progress.json"})
        self.assertTrue(all(len(f["sha256"]) == 64 for f in record["backup"]["files"]))
        self.assertEqual(record["listening"], {"session_ui": True, "receiver": True})

    def test_allow_dirty_is_recorded(self):
        system = FakeSystem(changes=["x.py"])
        self.assertEqual(self.run_main(system, "--allow-dirty")[0], 0)
        record = json.loads((self.config.backup_root / "20261102-093015-a1b2c3d4" / "launch-record.json").read_text())
        self.assertEqual((record["tree"]["dirty"], record["tree"]["changes"]), (True, [" M x.py"]))

    def test_a_process_that_never_listens_is_reported_and_not_stopped(self):
        system = FakeSystem(listening=False)
        code, _, err = self.run_main(system)
        self.assertEqual(code, 1)
        self.assertIn("Nothing was stopped", err)
        self.assertEqual(len(system.spawned), 2)

    def test_existing_backup_folder_is_never_overwritten(self):
        (self.config.backup_root / "20261102-093015-a1b2c3d4").mkdir(parents=True)
        system = FakeSystem()
        self.assertEqual(self.run_main(system)[0], 2)
        self.assertEqual(system.spawned, [])


if __name__ == "__main__":
    unittest.main()
