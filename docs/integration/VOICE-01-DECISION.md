# VOICE-01 — TL decision on R9 (CR-VOICE-01) and R10 (CR-VOICE-02), 2026-11-01

Requests: `docs/integration/CONTRACT-OWNER-REQUESTS.md` on `integration/spatial-capture-v1` @ `28017113`;
Android side `2ce5b757`; design `docs/integration/VOICE-01.md`. Workstation reference: `research/drawing-proof`
(`tools/live/providers/transcribe.py`, `tools/session-ui`).

## R9 — audio uplink: approved as proposed, with these limits

`POST /v1/guided/sessions/{id}/voice/transcribe` on the receiver, same TLS, guided token and session checks.
- Body `audio/wav`, mono 16-bit PCM 16 kHz, 0.4–8 s, ≤ 260 kB; anything else 415/413 without calling the provider.
- The receiver **only runs** `tools/live/providers/transcribe.py --stdin` as a subprocess (no import of `tools/live`),
  deadline 60 s, and stores neither audio nor transcript (no log line with either).
- Rate limit: one call at a time per guided session (second concurrent call: 429), at most 20 per minute per token.
- Response `{status, transcript, latency_s}`; `status` as from `transcribe.py` (`ok`, `invalid_audio`, `disabled`,
  `timeout`, `provider_error`). `MODEL_API_KEY` exists only in the receiver's process environment.

## R10 — operator input up, speech text down: approved with three changes

`POST /v1/guided/sessions/{id}/voice/input`, same checks.
1. **No contract version bump.** `anastylos-live-operator-input@1` stays unchanged; commands are not a new
   `source`. The body is one of
   `{"kind": "input", "operator_input": {…anastylos-live-operator-input@1, source "speech_transcript"…}}` or
   `{"kind": "command", "command": "next_question" | "repeat"}`.
2. **Binding (a): made on the workstation, never by the phone.** The receiver reads an operator-written file
   `guided-voice-bindings.json` (`{"<guided session id>": "<agent session name>"}`) next to its other config; the
   agent session name is a plain directory name inside the session UI's workspace root. Unbound guided session →
   409 "voice not bound to an agent session". The phone sends no names or paths.
3. **Forwarding over the session UI's local API, not by import.** The receiver calls the running session UI on
   `127.0.0.1` (port in receiver config) — the session UI stays the single writer with its per-session locks:
   - command `next_question` → `POST /api/questions/next`
   - input while a question waits for an answer → `POST /api/questions/answer`
   - input while a read-back waits → `POST /api/questions/confirm`
   - input with no open question → `POST /api/guide/ask`
   - command `repeat` → no call; the receiver re-sends the last `speak` of that guided session.

Response `{status, speak: {text, kind, basis_ref}}`, `text` only from the session UI response:

| Situation | `kind` | `text` | `basis_ref` |
|---|---|---|---|
| question asked | `question` | `text` (plus "Please look at the screen for the options." when `screen_options` exist) | `op_key` |
| answer interpreted | `read_back` | `read_back` | `op_key` |
| committed | `status` | "Recorded." | `op_key` |
| guide answer accepted | `guide_verified` | `text` | `package_sha256` |
| guide answer not accepted | `status` | "I cannot confirm that from the evidence." | `package_sha256` |
| anything else | `status` | fixed sentence chosen by the receiver | — |

No free model text is ever spoken. The "yes" rule and memory rules are unchanged (they live in the coordinator).

## Acceptance (unchanged from the app prompt)

"Muse, next question" → spoken question → "Screw from the back shell" → spoken read-back → "yes, that is correct"
→ committed in a demo copy; sentences without the wake word trigger nothing. Receiver tests: auth, unbound session,
size/type limits, rate limit, no audio or transcript in logs, forwarding table above with a fake session UI.
