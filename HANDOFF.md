# ANASTYLOS handoff

## Current continuation — Codex, 2026-11-02 late evening

This section supersedes older status statements below; keep the historical evidence.
The owner corrected the working source explicitly: **use `video-20261102-173652-6b0b2e13`**.
That real session successfully ran through video processing and the voice/dialogue-to-memory
pipeline. Do not confuse that existing success with the still-pending drawing/capture-loop acceptance.
Do not fall back to old FULL-01 geometry or transfer IDs/bindings from its different memories.

### Checkout and integration

- Branch: `codex/mvp-lead`; worktree:
  `C:\Users\HP\.codex\visualizations\2026\11\02\01a19de2-0a65-7dc2-80c8-8697aa87eff9\anastylos-codex-lead`.
- Main was checked locally and with `git ls-remote`: both `f9c37dd94d68c332bdea37ce88f8cf5e958b372e`.
  Therefore the old "nothing pushed" statement was already false at takeover.
- Lanes started at that same base, using available `gpt-6-luna`, reasoning `xhigh`:
  dialogue `f519557e` + `2cb6192d`; capture `4bbca0e2`; explosion/help `61309ea3`.
  Each lane diff was reviewed and integrated sequentially with checks before its local merge commit.
- Verified product-code checkpoint: `2086a5fdec0eb9e541cd79ef30843543565b3168`.
  Use `git rev-parse codex/mvp-lead` for the final commit, including this handoff.
- No push, main merge, branch/worktree cleanup, or original stack restart. The original root's
  untracked logo and Northstar's `ui-inspo/` remain untouched.

### Completed software changes

- Unambiguous compound read-back affirmatives such as the observed right/yes response now work;
  mixed/hedged answers do not authorize memory writes. Closing negatives finish correctly in
  both `missed` and `missed_detail` states.
- Deferred cells return once at the tail, with a durable offer record and a fresh coordinator
  revision. Existing final-session deferrals are cells 2/3; surplus detector qids in already
  described cells 8/9 are excluded, preventing duplicate parts.
- Completely missed parts can be added at closing with count, cell and origin, after a read-back
  and explicit yes. Origin is stored in `declared_removal.source`, storage in its own event.
  Crash/recovery coverage checks these fields and exactly-once recording.
- A last confirmed origin can be explicitly proposed in the next combined read-back, saving
  a separate origin turn. Rejection asks for the actual origin. Repeated-fragment ASR noise
  asks for clarification before model extraction; a model may not silently drop the noisy token.
- Capture checks the guided session, enrolled source, request revision, unchanged source spec,
  receipt and image hash. The follow-up has a durable request-bound ID. Uncertain starts become
  `follow_up_pending_recovery`; the UI explains the pause instead of launching duplicate work.
  Existing receiver loopback now covers actual request/package receipt/asset-fetch bytes through
  follow-up initiation. It uses a generated image package and a fake worker seam, not a phone.
- The part selector starts blank and offers no-match. Stale replacement requires an explicit
  choice. Late answers cannot switch to another selected part. Origin/storage provenance stays
  separate, and a different memory cannot inherit links merely by reusing an ID.
- Explosion layout/selection survives reload; technical capture/review gates exclude a drawing
  from both proposal and composition, including when a stale saved binding exists. The page
  no longer calls an unconfirmed layer order operator-confirmed.
- `ANASTYLOS_DRAWING_CACHE` isolates copied-session derived caches. Real browser review also
  found and fixed review jobs mislabeled "Needs capture", an empty enabled start button and
  a misleading empty-spec message despite existing jobs.

### Verification and evidence

All local evidence is outside Git:
`C:\Users\HP\.codex\visualizations\2026\11\02\01a19de2-0a65-7dc2-80c8-8697aa87eff9\evidence`.

`SUMMARY.json` summarizes the result. `evidence-manifest.json` hashes 631 local artifacts;
manifest SHA-256: `c405d7c637885e47ca466ebfd138613e2a37db926d04d2ff813464b62948a9b3`.

- `original-session-copy/` + `session-copy-manifest.json`: untouched source snapshot.
- `replay_real_session.py`, `real-replay-before/replay.json`, `real-replay-final-full/replay.json`:
  six state-anchored cases with the actual coordinator and copied real memory/journal prefixes,
  recorded Muse proposals, no network; before 0/6 desired outcomes, after 6/6. The missed RGBY
  description is a labelled synthetic extension from owner truth, not a fabricated historical reply.
  All 42 recorded inputs were additionally replayed diagnostically, reconstructing the historical
  process restart: no write without an unambiguous affirmative. Changed prompts and legacy bundle
  states prevent treating that diagnostic as full semantic acceptance. No new live voice/model
  semantics or physical dialogue run is claimed.
- Final suites: session UI 165/165, live 87/87, agent 119/119, live integration 12/12,
  drawing proof 47/47, explosion 26/26. Receiver lane: 209 run, 206 pass, 3 environment skips.
  Compliance: 0 errors, 1 worktree-environment warning, 18 existing open reviews.
- Sandbox failures were diagnosed: temporary AppData access, loopback socket permission,
  and `taskkill` denial (isolated repro returned credential/access failure). Suites requiring
  local sockets/own worker termination passed with scoped escalation; no timing failure was
  declared green merely by retrying unchanged conditions.
- `frozen-check.json`: all 20 E3-B Git blobs match their frozen SHA-256; checkout bytes also
  match exactly in this worktree (0 CRLF differences). Frozen files were not normalized.
- Browser: actual selection/help/no-match/reload tested on clearly synthetic internal fixtures;
  that visible synthetic preview confused the owner and was closed. It is **not product geometry**.
  The real source-session Drawings page was then actually opened/reloaded and status controls checked.
  No real drawing binding, part link or layer order was confirmed by an agent.
  Screenshot: `evidence/browser-real-source.jpg`; exact changed files: `evidence/changed-files.txt`.

Run from the integration tree with the existing Python; each suite runs from its own directory:

```powershell
$py = 'C:/Users/HP/Documents/Anastylos/.venv/Scripts/python.exe'
$env:TEMP = 'C:/Users/HP/.codex/visualizations/2026/11/02/01a19de2-0a65-7dc2-80c8-8697aa87eff9/evidence/lead-temp'
$env:TMP = $env:TEMP
# In tools/session-ui, tools/live, tools/agent, tools/live/integration,
# tools/drawing-proof, tools/explosion, or capture-companion/receiver:
& $py -B -m unittest discover -s tests
# From repository root:
& $py -B tools/compliance/check.py
& $py -B 'C:/Users/HP/.codex/visualizations/2026/11/02/01a19de2-0a65-7dc2-80c8-8697aa87eff9/evidence/check_provenance.py' .
```

### Real drawing result — current unresolved product issue

- Source copy: `evidence/video-source/video.mp4`, SHA-256
  `6b0b2e1362f0ed66ffe7295b771b2938db2d86a20e35d71458a91aceb01ecb52`.
  1080×1920, about 29.956 fps, 205.468 seconds. Candidate sheets/manifests are in
  `evidence/new-session-geometry/`. Unlike the old FULL-01 material, this video DOES show
  the separated shells and board. At f4316 / 144.078 s they are visible together.
- Working copy: `evidence/product-workspace/video-20261102-173652-6b0b2e13/`.
  Session video/run paths are rebased to this copy; all original session bytes were rechecked unchanged.
- `REAL-SOURCE-BATCH.md` and frozen-input manifests precede inference. Batch 1: 6 Muse + 3 SAM;
  Batch 2 context check: 2 Muse + 1 SAM, then stopped because the back-shell outline was still bad.
  No retries/resumes or further two planned targets in Batch 2. Separate usage logs preserve all attempts.
  Aggregate returned Muse usage: 12,433 input + 18,130 output tokens. Four SAM ledgers estimate
  USD 0.01 at their stored rate; this is not a provider invoice or a new price verification.
- Existing pipeline, prompts, parser and gates were used. Offline preflight corrected an old
  `[58,50,254,177]` crop which clipped the new source: the full calibrated sheet includes the parts.
  Initial preflight specs/sheets are preserved. Frame choice selected f4373 / 145.981 s.
- **Do not confirm these four masks.** Full-page back-shell mask includes background; PCB mask
  selects cable; front-shell mask is sparse; context back-shell mask is incomplete. PCB/front-shell
  are `NEEDS_REVIEW`. Back-shell jobs pass limited plausibility checks and remain `NEEDS_CONFIRMATION`,
  illustrating that those checks do not prove outline correctness. No decoder/placement bug was
  demonstrated; masks were checked at their native returned raster/box coordinates.
- No real three-assembly explosion was composed or accepted. Next engineering work is to diagnose
  mask/box reliability on these preserved responses and source pixels, using existing tools. Do not
  use synthetic geometry, invent missing rims, weaken gates, or ask the owner to repeat a capture
  that is already adequate merely to avoid this software issue. A new API budget needs a separate plan.

### Runtime and next owner action

At takeover, original UI listeners were PID 22076 on 127.0.0.1:8079 and receiver PID 15204 on
192.168.0.110:8443, from `Anastylos-northstar` @ `713b9b45` (clean tracked files, untracked `ui-inspo/`).
Command-line/source evidence is in `existing-runtime-processes.json`; a live Python process's loaded
module bytes cannot be proved solely from current files. Those original processes were not stopped.

The isolated review page used port 8097 and only the copied real workspace. Its agent-started
process and browser tab were closed after verification. Original 8079/8443 listeners stayed in place.
Start the copied review explicitly when needed:

```powershell
$env:ANASTYLOS_DRAWING_CACHE = 'C:/Users/HP/.codex/visualizations/2026/11/02/01a19de2-0a65-7dc2-80c8-8697aa87eff9/evidence/new-source-drawing-cache'
& 'C:/Users/HP/Documents/Anastylos/.venv/Scripts/python.exe' -B tools/session-ui/server.py `
  --workspace-root 'C:/Users/HP/.codex/visualizations/2026/11/02/01a19de2-0a65-7dc2-80c8-8697aa87eff9/evidence/product-workspace' `
  --host 127.0.0.1 --port 8097
# http://127.0.0.1:8097/?session=video-20261102-173652-6b0b2e13#/drawings
```

The owner need not repeat the known dialogue bugs now. Next physical acceptance remains a real phone
request → capture → checked receipt → one actual drawing worker → overlay/result, on a copied session
with a newly armed guided session; loopback does not prove that. No real picture proves part identity.
First resolve the preserved segmentation issue; then ask for specific view/layer/part confirmations on
usable real output. Full MVP acceptance and submission preparation remain open. Android was unchanged
and not rerun in this continuation. No new dependencies or licensing conclusions.

---

## Historical handoff — retained below

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
- **Never push unless told to.** Historical wording "nothing pushed" is superseded by the verified remote
  `main @ f9c37dd9` above. Merging to `main` still needs the owner's word each time.
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
- **Operator actions stay with the operator:** approving a phone pairing is a UI decision, never done by an agent. The owner also stops processes; the agent's permission classifier refused both.

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
