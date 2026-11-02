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
- `executor.py`: Muse (`choose_question`, `extract_fields`, effort `none`/`low`, strict JSON). Muse writes no text;
  chosen type must be allowed, extracted words must be words the operator said (`ground`). Any failure -> rules.
- `agent.py`: catalog questions need no model call (Muse is asked only when a question was asked < 8 s ago or several
  types are allowed). One re-ask, then the defer line plus an open item (`open_items.jsonl`), no write, event closed.
  `on_answer` takes an `ack` callback (called before any model call) and returns per-stage timing.
- `eval/`: pre-registered live batch (`PREREG.md`, `cases.json`, `run_batch.py`).

Tests: `python -m unittest discover -s tests` from `tools/live` (synthetic, fake model). Not validated on hardware.
