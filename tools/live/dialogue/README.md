# dialogue — catalog-driven agent

Layer above the coordinator; it never writes memory. Contract with the coordinator: an understood answer is returned
as `forward` text, the coordinator reads it back, and only its read-back plus "yes" writes (`CONTRACT.md`).

- `../catalog/phrases.json` + `selector.py`: eight question types (plus an internal `ack`) x three personas
  (`serious`, `playful`, `unhinged`), 4-6 phrasings each. Variant = hash(event id, type, persona), skipping the last
  three used. Slots come from verified data only; unknown cell -> "the sheet". `read_back` wraps the coordinator's
  own read-back sentence unchanged; `unclear_answer` / `defer_to_desktop` keep their fixed sentences in all personas.
- `settings.py`: persona and agent name per workspace (`dialogue-settings.json`); session UI `GET/POST /api/dialogue/settings`
  (also what the phone reads: `wake_words`, `asr_keywords`, `mishearings`, `persona`). `wake.py`: name -> keywords,
  mishearings, wake-word stripping. No name: "Muse" and "Meta".
- `executor.py`: Muse (`choose_question`, `extract_fields`, effort `minimal`/`low` (`none` is rejected by the API), strict JSON). Muse writes no text;
  chosen type must be allowed, extracted words must be words the operator said (`ground`). Any failure -> rules.
- `agent.py`: catalog questions need no model call (Muse is asked only when a question was asked < 8 s ago or several
  types are allowed). One re-ask, then the defer line plus an open item (`open_items.jsonl`), no write, event closed.
  `on_answer` takes an `ack` callback (called before any model call) and returns per-stage timing.
- `eval/`: pre-registered live batch (`PREREG.md`, `cases.json`, `run_batch.py`, `RESULTS.md`). Result: `low` is accurate
  but ~3.7 s per call, so the default wiring is rules only (`Executor(None)`); Muse stays advisory until a faster effort proves out.

Tests: `python -m unittest discover -s tests` from `tools/live` (synthetic, fake model). Not validated on hardware.

## Voice path (session UI + receiver)

`tools/session-ui/dialogue_flow.py` runs the spoken question round: `POST /api/dialogue/turn {session, command: "next_question" | text}`
-> `{handled, speak:{text,kind,basis_ref}}`. The receiver (`anastylos_voice_routes.py`, `VoiceConfig.dialogue`) relays input there and falls
back to the legacy question/guide routes when the turn is not handled. Small parts of one cell are bundled into one question
(`bundle.py`); every part stays its own coordinator question, gets its own read-back state, and one combined read-back + "yes" confirms them
individually. "No" or a refused bundled answer returns to one-by-one questions; two misunderstandings leave the parts for the desktop
(`LEFT_UNRESOLVED`, `dialogue-open-items.jsonl` in the session). When a video job finishes, `videos._bind_voice` writes the receiver's
`guided-voice-bindings.json` (uploading guided session -> new session) and a `dialogue-pending-open` marker: the first voice utterance opens the dialogue.
