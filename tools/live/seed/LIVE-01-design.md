# LIVE-01 — evaluation design that separates live operation from model effects

Status: design for preregistration (to become docs/LIVE-01.md once the owner authorises the lane).

## Effects to separate

| effect | comparison | how it is isolated |
|---|---|---|
| live timing | L0-live vs offline round | same deterministic question generator, same wording family; only *when* differs |
| model wording (L1) | L1 vs L0 inside the live arm | per-question randomisation from a sealed die; identical question set; **fixed delivery delay** for both arms (question shown at deposit + D s, D ≥ L1 p95 latency) so latency does not masquerade as wording |
| model action selection (L2) | not in LIVE-01 | AGENT-02 replay first; a live L2 arm only after it passes |
| voice | not in LIVE-01 v1 | text/screen answers only; voice enters as a separate arm after VOICE-01 |

## Operator-recall contamination

1. **Each part is asked exactly once, in one mode.** Before the take a sealed die assigns every deposit
   *slot* (1st, 2nd, … deposit) to `live` or `offline`. A part answered live is never asked again offline, and
   vice versa. No "replay the offline round on the same session" for live-answered parts.
2. **Answers before ground truth.** All answers (live and offline) are collected and hashed *before* the owner
   reviews the witness video and writes the actual file. The offline round must not show the witness video;
   it shows only the overhead recording, as today.
3. **No script-derived recall.** The operator chooses order and cells freely (no rolled script with sources);
   ground truth comes from the witness video afterwards. A rolled script would let both arms answer from the
   script instead of from the event.
4. **Unseen object.** Not the SNES controller, not the E3-B object; the operator has not disassembled it before.
5. **Delay parity for offline.** The offline round starts at a fixed time after the take (e.g. 10 min), recorded.

## Autonomy accounting

- The automatic score is computed from a pipeline run **without** any declared inputs (pure observer + linker).
- Live declarations fed back via `custody-declared-*@1` produce a second, separate run whose links are
  `operator_assisted`; they never enter the automatic count (linker 1.2 accounting).
- Audio: answers are typed or on-screen in v1, so the overhead audio track carries no answers.

## Metrics (preregistered)

Primary: correctly resolved part sources per operator minute (operator time = time from question shown to
confirmed read-back, plus offline-round time). Safety: wrong declarations vs ground truth, false-alarm questions
(≤ 25 %), declarations promoted to evidence (= 0), scrubbing events in the live arm (= 0), stale or duplicate
commits (= 0). Secondary: time from deposit to answer, H3 rubric per question, L1 vs L0 answer time and error.

## Accept / reject (from Research Pass 01, unchanged)

Accept live timing if resolved-per-minute is higher than offline and accuracy is not lower. Reject if accuracy is
lower or operator time per resolved part is more than twice the offline value. L1 is adopted only if it is not
worse than L0 on wrong declarations and better on answer time or H3 rating. n = 1 operator, 1 object:
descriptive only.
