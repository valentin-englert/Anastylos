# ANASTYLOS handoff

Written 2026-11-02 evening by the Technical Lead agent (Claude), for any coding agent (Muse, Codex, Claude) that continues
without prior context. Read this first, then `AGENTS.md`, then `ARCHITECTURE.md`. Where this file and the code disagree,
believe the code and fix this file. `docs/submission/CLAUDE-HANDOFF.md` is older and historical.

## 1. Project and deadline

ANASTYLOS is an evidence-backed disassembly and reassembly assistant for the Meta AI hackathon: the operator films a
disassembly with the phone, the system finds what was removed, Muse (the voice agent) asks what it could not know, an
append-only Assembly Memory records the answers, and the product helps with reassembly from that record. Design rule:
**deterministic core, agentic edge**.

- **Submission:** Wednesday 2026-11-04, 9:00 PM ET (Thursday 03:00 German time). Demo video is filmed in English.
- **Owner:** the repository owner directs and decides. Reply to the owner in German; write code and docs in English.
- **Owner plan:** agents rest until Tuesday or Wednesday unless the owner calls earlier. Wednesday: make the repo ready for submission.

### North star (owner's words)

"Demontage dokumentieren → fehlende Informationen interaktiv beschaffen → Explosionszeichnung aufbauen → beim Zusammenbau helfen."

1. **Capture loop:** missing view → phone request → photo assigned → drawing or concrete follow-up. No manual file or JSON handling.
2. **First usable explosion view:** three assemblies, selectable, with source photo or overlay. No invented heights or paths.
3. **Assembly help** from a selected drawing part: "Where does this part belong?" from stored origin; origin and storage stay separate.
4. **Later:** drawing depth, latency, LED pad.

## 2. Status

| Area | State | Evidence |
|---|---|---|
| Phone films and uploads video | Works | 3:25 min, 514 MB controller video, hash OK, flash did no harm |
| Video pipeline to questions | Works, overcounts | 5:49 min, 21 questions; per-cell results in section 7 |
| Voice dialogue, one open question per cell | **Worked end to end once** with the owner's voice | `dialogue-turns.jsonl`, 42 turns; owner: mostly correct, but tedious |
| Confirmed answers into Assembly Memory | Works | `memory.jsonl` of the session in section 7 |
| Pairing from the session UI (Phone page, operator approves) | Pairing was used on the device, approve path worked | |
| Name as wake word, three personas | Name tested ("Herbert"); only persona `serious` was tried by voice | |
| Capture loop with a real photo into a follow-up job | **Not accepted end to end** | Unit and loopback tests only |
| Explosion view with three assemblies | **Not done** | Only one real drawing exists; back shell and PCB drawings are missing |
| Assembly help through the part link | Implemented, **not accepted end to end** | Unit tests |
| Fidget toy hard test | **Not run** | See section 6 |
| E3-B and second object | Not run | Pre-registered earlier; files frozen |

Honest gaps: session resume is only unit-tested; evidence is from a single object; empty transcripts in the first voice tests
have no proven cause (a peak/RMS log now exists, tag `AnastylosVoice`).

## 3. Repository and branches

- `main` holds everything from `integration/northstar` at the time of this handoff. Continue on a new branch from `main`.
- **Never push unless told to.** Nothing here has been pushed. Merging to `main` needs the owner's word each time.
- Do not merge `wave2`-named work: the audit found it is a duplicate of `main`.
- Lane branches `claude/*` listed in section 5 are fully merged.

### Hard limits

- **Frozen for E3-B, do not edit:** `custody-ledger/*`, `tools/session/{session,parts,answers}.py`,
  `assembly-memory/anastylos_memory/store.py`, their configs, `tools/proto-01/score_e3b.py`, `e3b_grid_frame.py`.
- **Data:** originals under `C:\ANASTYLOS\data` are untouched; work on copies. Videos and photos stay out of git. `ui-inspo/` stays out of commits.
- **`MODEL_API_KEY`:** Windows user scope only, injected per child process. Never print, log, store, or put it on the phone.
- **Memory is append-only.** Corrections are new events with `supersedes`. Only operator confirmation confirms anything.
- **Spoken output** is catalog or verified text only. A memory write needs a read-back and "yes".
- **API tests:** bounded, logged batches with bounded retries; write the plan first; no silent retuning. A deviation from a plan is the owner's decision, and is recorded.
- **Operator actions stay with the operator:** approving a phone pairing is a UI decision, never done by an agent. The owner also stops processes himself; the agent's permission classifier refused both.

## 4. How the owner wants to work

- "Working product, keine Raketenwissenschaft." Quality is expressed as labels, not blockers. Keep strictness only where it protects truth.
- "Ziel ist es, dass nicht WIR das machen, sondern Muse... Damit steht und fällt das Projekt."
- **Be frugal.** Usage is limited. Do not burn it on fine calibration. Prefer focused parallel lanes on a cheaper model.
- **Do not use the owner as the tester for things you can replay.** Replay real transcripts first; ask for a voice test only after that passes. Three voice tests were spent on bugs a replay would have found.
- Commit only when the suites are green: chain test and commit. An earlier commit slipped through red.
- Tell the owner plainly what was not verified. Never fix a failing test by retrying: two "flaky" tests here were real Windows races.

### Muse design decisions

- Muse is the executing agent with a fixed universal question catalog (`tools/live/catalog/`); no free part recognition.
- Personas: `serious`, `playful`, `unhinged` (humor-focused and friendly, like a buddy; never actually unhinged).
- A user-set name becomes the wake word; with no name, Muse and Meta. Test name: Herbert, English pronunciation.
- Misunderstood: never guess; at most one re-ask, then "Why don't we resolve this later on the desktop? I'll make a note of the uncertainty."
- Live interaction during recording is **paused, not discarded**: for the MVP the dialogue runs after upload. The owner wants to brainstorm this again later.
- Offline `faster-whisper` is an idea to keep in mind only.

## 5. Team

The owner directs. A Technical Lead agent (Opus) reviews and integrates; it reads diffs, merges lanes into the integration
branch, runs the suites, and talks to the owner. Each lane is one session with one branch and one worktree under
`.claude/worktrees/`; agents never touch each other's files. A new agent does not need these sessions: their code is in `main`.

| Lane | Branch | Owned |
|---|---|---|
| `muse-agent` | `claude/muse-agent-a1577c` | Catalog, personas, wake name, dialogue flow, operator notes into memory |
| `app-ui` | `claude/app-ui-7fde3c` | Android app, conversation window, scanner, record control; session UI Phone page |
| `desktop-ui` | `claude/desktop-ui-d8d8ea` | Session UI look and flow |
| `explosion` | `claude/explosion-view-5ad7ef` | Explosion view from part drawings |
| `assembly-help` | `claude/assembly-help-f60dda` | Part intent and "where does this belong" |
| `stabilize` | `claude/stabilize-41a2e2` | Job races, controlled start, voice wiring |
| `consistency` | `claude/muse-consistency-55feda` | CONS-1 measurement of model consistency |

Claude Desktop session IDs, usable only from that app: muse-agent `local_4bfc0616-4ae5-4d33-81a2-a370e48afc35`, app-ui
`local_dbaa7b20-7386-4345-8685-9d960a6ba483`, desktop-ui `local_fa3d6ce7-0873-4d4a-aa83-21659345dd98`, explosion
`local_71b42971-e71d-4b64-a06e-c7f767bbdf92`, assembly-help `local_4859f987-6426-4596-a4f8-8dc9331baa70`, stabilize
`local_d8597bd8-a488-4c11-8f98-47a2ce3dcb2b`, consistency `local_260703f1-a9f0-4920-ac84-b14f6ac0c4c6`.

Working pattern that served well: one short brief per lane naming evidence, scope and what done means; a report of at most 8 lines; the lead
checks the diff, merges each branch with `--no-commit`, runs suites, and commits only when green.

## 6. Run it

Paths on the owner's workstation (outside git):

- Python: `C:/Users/HP/Documents/Anastylos/.venv/Scripts/python.exe`
- Stack config: `C:\ANASTYLOS\stack-config.json`
- Workspace: `C:\ANASTYLOS\data\session-ui-voice` (with `capture-settings.json`, `capture-link.json`, `dialogue-settings.json`)
- Receiver data: `C:\Users\HP\AppData\Local\Anastylos\receiver` (includes `guided-voice-bindings.json`, `voice-journal.jsonl`)
- Audit report: `C:\tmp\anastylos-audit\AUDIT-2026-11-01.md`
- Devices: Xiaomi phone over adb; workstation LAN `192.168.0.110`; ports 8079 (session UI, localhost) and 8443 (receiver)

The running stack currently starts from the worktree `C:\Users\HP\Documents\Anastylos-northstar`. `start_stack.py` runs from the tree it lives in.

```powershell
& "C:/Users/HP/Documents/Anastylos/.venv/Scripts/python.exe" -B "C:/Users/HP/Documents/Anastylos/tools/demo/start_stack.py" --config "C:/ANASTYLOS/stack-config.json" --allow-dirty
```

A busy port means the old stack still runs; the owner stops it:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "session-ui.server\.py|anastylos_capture_receiver\.py" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Everyday flow: open `http://127.0.0.1:8079/`, Phone page. Pair if needed (new pairing means a new `source_id`, updated automatically,
and a new session). Scan the session code in the app, arm, record, upload. Process the video in the UI. Say the wake word; Muse asks by cell.

Details that cost time:

- `capture-settings.json` uses `receiver_cert` for the pairing QR. **Do not set `ca_file` there:** it turns off the SPKI pin path and breaks every connection.
- The app needs a voice binding from guided session to session folder. Processing writes it for the uploading session; a session opened later needs an entry added, plus the marker file `dialogue-pending-open` in the folder.
- Python code changes need a stack restart; the app changes need an APK install.

### Tests

```powershell
python -m unittest discover -s tests   # from each of tools/live, tools/session-ui, capture-companion/receiver
python tools/compliance/check.py       # must stay at 0 errors
.\gradlew.bat :app:testDebugUnitTest :app:lintDebug :app:assembleDebug   # from capture-companion/android
```

Last results: live 85, session UI 136, receiver 209 pass; compliance 0 errors; Android 531 pass with lint clean, on the app branch. Android was not re-run after the merge, since the later merges did not touch it.

## 7. The controller test session

Session `video-20261102-173652-6b0b2e13`, video id `6b0b2e1362f0ed66ffe7295b771b2938`, in the workspace above. Turn log with
transcripts: `dialogue-turns.jsonl` in that folder. Sheet numbering is the owner's and it is correct: do not reinterpret it.

| Cell | Owner truth | Detected | Memory now |
|---|---|---|---|
| 1 | 5 identical screws | 4 | 5 screws, back shell |
| 2 | L button | 2 | **Open**, deferred |
| 3 | R button | 2 | **Open**, deferred |
| 4 | 2 white rubber membranes, L/R button, back of controller | 2 | Corrected: `dec-0019`, `dec-0020` supersede the wrong "why" entries |
| 5 | D-pad membrane | 1 | "rubber membrane", origin misheard as "front shell Dpadpad" (uncorrected) |
| 6 | RGBY membrane | 0 | **Missing**: the detector missed it and the closing question did not catch it |
| 7 | start/select membrane | 2 | "start and select button", front shell |
| 8 | D-pad | 2 | Dpad, front shell |
| 9 to 12 | green, yellow, blue, red button | 3, 1, 1, 1 | Correct, front shell |

Also in memory: work area holds back shell, front shell, PCB (with soldered cable). The note of the cell 4 correction says 2026-11-03, but it was written 2026-11-02; the record is append-only, so it stays.

## 8. Next steps, in order

1. **Fix the known dialogue defects below; replay `dialogue-turns.jsonl` as the test.** Keep it small.
2. **Fidget toy hard test once**, documented honestly (pistol fidget toy; untouched data, lanes must never see it before the test).
3. **Explosion with three assemblies:** obtain back shell and PCB drawings from video or stills, with the operator confirming view side.
4. **Capture loop acceptance** with a real photo through to a follow-up job. Then **assembly help** through the part link.
5. **Wednesday:** acceptance, video, submission docs, `ARCHITECTURE.md` status update (its status index predates this work and must be checked), push only when the owner says.
6. Optional if time remains: E3-B and second object; drawing depth and latency; key guide screenshots; OFL text with the APK.

### Known dialogue defects

- The owner finds answering every cell tedious. Reduce turns: asking for origin per part is the biggest cost. For example, remember the last origin ("Also front shell?").
- `closing/missed`: "now i don't think so" and "no" were not understood and fell into the deferral line. A "no" here must end the round.
- `cell/confirm`: "right, yes," was not accepted as yes.
- Origin text keeps ASR garbage ("Dpadpad"); the read-back is the only guard.
- A deferred cell does not come back in the same round. It should return once at the end.
- Latency: about 3.5 s median model time at effort `minimal` plus 2 s transcription when the model runs. p95 misses the pre-registered 6 s; the owner accepted `minimal` anyway (`tools/live/dialogue/eval/RESULTS-items.md`).
- The cause of the earlier "model never ran" symptom was not proven. In the final test the model did run (9 turns); check its share.

## 9. Key technical facts

- **Guided control G0:** `open_session` (bootstrap) → phone consent/arm creates a grant → claim → adapter token → ViewRequest → receipts → asset bytes. The grant is found via a read-only look into `control.db` (route R11 was requested from the contract owner).
- **Capture profile H2** (`guidance-hypothesis-2`): the phone shoots when the sheet is tracked and still for about 0.5 s; images stay package only. Pose samples (R13) are deferred.
- **Phone values:** board `5a3a3a3a-0000-4000-8000-00000000a3a3`, calibration `e92c8ea1-36a8-449f-8df9-50f0f3481548`, frame `board:surface_a3`, camera `camera:0`.
- **Drawing models:** box at effort high (r4.2, 19.4 s vs 28.0 s, IoU 0.914 vs 0.926); frame choice stays at max. CONS-1: frame choice needs operator confirmation (L2); box is a vote of 3 (L1). Prompt cache gave no benefit.
- **Dialogue:** `tools/session-ui/dialogue_flow.py` drives; `tools/live/dialogue/items.py` extracts and checks that names occur in the transcript; `Coordinator.write_operator_notes` records parts the detector did not see, marked operator-stated.
- **Phone:** `capture-companion/android`, voice in `voice/VoiceController.kt` and `voice/WakeWord.kt`. Receiver voice routes: `capture-companion/receiver/anastylos_voice_routes.py`.
- **UI:** `tools/session-ui` (`server.py`, `captures.py`, `phone.py`, `videos.py`, `web/`), one-click launch in `launch/`.
