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
- `executor.py`: Muse (`choose_question`, `extract_fields`, `extract_items`, effort `minimal`/`low` (`none` is rejected by the API), strict JSON). Muse writes no text;
  chosen type must be allowed, extracted words must be words the operator said (`ground`). Any failure -> rules.
- `agent.py`: catalog questions need no model call (Muse is asked only when a question was asked < 8 s ago or several
  types are allowed). One re-ask, then the defer line plus an open item (`open_items.jsonl`), no write, event closed.
  `on_answer` takes an `ack` callback (called before any model call) and returns per-stage timing.
- `eval/`: preserved pre-registered batches. The later cell-items batch and owner's recorded decision
  (`RESULTS-items.md`) selected `minimal` with an 8-second timeout and no retries. The spoken cell flow uses
  Muse first when a key is available, with rule fallback on failure; `Executor(None)` remains the offline seam.

Tests: `python -m unittest discover -s tests` from `tools/live` (synthetic, fake model). A bounded real phone/voice
run exists for `video-20261102-173652-6b0b2e13` (see `HANDOFF.md`). The subsequent defect fixes have offline replay
evidence, not a new physical-phone acceptance.

## Voice path (session UI + receiver)

`tools/session-ui/dialogue_flow.py` runs the spoken question round: `POST /api/dialogue/turn {session, command: "next_question" | text}`
-> `{handled, speak:{text,kind,basis_ref}}`. The receiver (`anastylos_voice_routes.py`, `VoiceConfig.dialogue`) relays input there and falls
back to the legacy question/guide routes when the turn is not handled. One open cell question is interpreted by Muse
(`items.py` validates the proposal); counts, names and origins are read back together. Only an unambiguous affirmative
confirms them. A repeated origin may be proposed explicitly in that read-back, saving a separate origin turn; rejection
asks for the actual origin. Raw repeated-fragment ASR uncertainty asks for clarification, never normalization.

Skipped/uncertain cells return once at the round tail with a new coordinator revision; durable
`dialogue-tail-offered.jsonl` prevents another tail offer after restart. Surplus detector questions after a confirmed
cell description are not offered again. Closing can record completely missed parts with count, origin and cell, through
the existing coordinator writer and a fresh read-back confirmation. Origin and storage stay separate memory fields.

When a video job finishes, `videos._bind_voice` writes the receiver's
`guided-voice-bindings.json` (uploading guided session -> new session) and a `dialogue-pending-open` marker: the first voice utterance opens the dialogue.
