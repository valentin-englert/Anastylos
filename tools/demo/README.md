# tools/demo — controlled start of the demo stack

`start_stack.py` starts the session UI and the capture receiver **from the git tree it lives in**, after a
work-data backup, and leaves a launch record that proves which code was running. Stdlib only, offline.
Status and contracts: [`ARCHITECTURE.md`](../../ARCHITECTURE.md).

## Use

```powershell
copy tools\demo\stack-config.example.json C:\ANASTYLOS\stack-config.json   # edit paths, host IP, ports
python tools\demo\start_stack.py --config C:\ANASTYLOS\stack-config.json --dry-run
python tools\demo\start_stack.py --config C:\ANASTYLOS\stack-config.json
```

Run it with the interpreter you want the stack to use (it is recorded; `"python"` in the config overrides it).
Options: `--dry-run` (checks and plan only: nothing copied, nothing started), `--allow-dirty`,
`--startup-timeout <s>` (how long to wait for each port to listen, default 20).

## What it does, in order

1. Prints the commit SHA, branch and dirty state of **this** tree. A dirty tree (any `git status --porcelain` line,
   untracked files included) is refused unless `--allow-dirty`; the launch record then says `dirty: true`.
2. Checks, and refuses on any FAIL: ports free (session UI `127.0.0.1:<session_ui_port>`, receiver
   `<host>:<receiver_port>`), the configured `host` is assigned to a local interface, `MODEL_API_KEY` is available in the
   user environment (Windows: `HKCU\Environment`, else the process environment; the value is never printed), the three
   scripts exist in this tree, workspace / receiver data dir / cert / key exist, and the backup folder is not inside the
   workspace. A busy port means the old process is still running: stop it yourself, the script never stops anything.
3. Copies (never moves) every `<workspace>/*/memory/memory.jsonl` and every file under `<workspace>/*/drawings/`
   (jobs and specs) to `<backup_root>/<YYYYmmdd-HHMMSS>-<sha8>/workspace/...`, verifying each copy by SHA-256.
   An existing backup folder is never overwritten.
4. Starts, detached, with absolute paths of this tree and the working directory set to the tree root:
   - `tools/session-ui/server.py --workspace-root <workspace> --host 127.0.0.1 --port <session_ui_port>`
   - `capture-companion/receiver/anastylos_capture_receiver.py --data-dir <receiver_data_dir> serve --host <host>
     --port <receiver_port> --cert … --key … --voice-transcriber <tree>/tools/live/providers/transcribe.py
     --session-ui-port <session_ui_port>`

   `MODEL_API_KEY` is put into the two children's own environment only (drawing workers and the transcriber inherit it
   from them). It is on no command line, in no log of this script and not in the launch record.
5. Waits until both ports accept connections and writes `launch-record.json` next to the backup: schema
   `anastylos-launch-record@1`, start time, tree root / SHA / branch / dirty list, config file, ports, backup manifest
   (path, bytes, SHA-256), and per process the pid, argv and log path (`session_ui.log`, `receiver.log` in the same
   folder). On Windows with a venv the pid is the venv launcher's; the real interpreter is its child.

Exit codes: 0 started, 1 a process did not start listening (nothing is stopped; read its log), 2 refused or bad config.

## Config

JSON, no paths in code. Keys (all required except `python`): `workspace_root`, `receiver_data_dir` (must already be
initialised with `receiver … init`), `backup_root`, `host` (IPv4 the phone reaches), `session_ui_port`, `receiver_port`,
`cert`, `key`, `python`. A real config holds machine paths: keep it outside the repository.

## Tests and limits

`python -m unittest discover -s tests` (fake git / network / environment / spawner; nothing is started, nothing real is
copied outside temporary folders). Not covered by tests: the real `System` class (git, sockets, registry, `Popen`) and
the real startup of both servers; use `--dry-run` to exercise the checks on the actual machine.
