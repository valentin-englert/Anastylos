"""Deterministic, network-free proposer for other lanes' tests (CONTRACT §3.1).

Default behaviour replays recorded fixtures (`anastylos-live-provider-fixture@1`, file `<request_sha256>.json`,
as written by `meta.py`) through the same response interpretation as the real provider. A script, keyed by
request_id or else by task, overrides that with one of `MODES`:

- `output`: answer with the given `output` object (still validated; a bad object yields `invalid_output`).
- `invalid_json`, `schema_violation`, `wrong_enum`: model text that fails the named way → `invalid_output`.
- `stale_revision`: a valid `next_action` output whose `question_revision` is one lower than the scripted or
  replayed output's → `ok`; the consumer must reject it as stale.
- `timeout`, `provider_error`, `disabled`, `budget_exhausted`: that status, no output.
"""

from __future__ import annotations

import json
from pathlib import Path

from provider_core import (EFFORT, MODEL, build_request, encode_request, interpret_response, make_error,
                           make_result, read_fixture, sha256_hex)

STATUS_MODES = ("timeout", "provider_error", "disabled", "budget_exhausted")
MODES = ("replay", "output", "invalid_json", "schema_violation", "wrong_enum", "stale_revision") + STATUS_MODES

# One schema-valid output per task; the failure modes are derived from it.
VALID_OUTPUTS = {
    "question_wording": {"text": "Woher kam das Teil in Zelle 4?", "facts_used": []},
    "answer_interpretation": {"intent": "unclear", "choice": None, "place_text": None, "label_text": None},
    "next_action": {"action": "ask", "question_id": "Q-1", "question_revision": 1, "until": None,
                    "prompt_kind": None, "part_ref": None, "reason": None},
    "guide_claims": {"schema": "anastylos-guide-claims@1",
                     "claims": [{"relation": "unknown", "subject": "P-1", "object": None, "basis_word": None}],
                     "insufficient_evidence": True},
}

_WRONG_ENUM = {
    "answer_interpretation": ("intent", "maybe"),
    "next_action": ("action", "request_view"),
    "guide_claims": ("relation", "located_near"),
}


def synthetic_response(text: str, model: str = MODEL, effort: str = EFFORT) -> dict:
    """A `/v1/responses` body shaped like the verified seed trace, carrying `text` as the message output."""
    return {
        "object": "response", "status": "completed", "model": model, "reasoning": {"effort": effort},
        "output": [{"type": "reasoning", "summary": []},
                   {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }


class FakeProposer:
    def __init__(self, fixtures_dir: Path | None = None, script: dict | None = None):
        self._fixtures_dir = None if fixtures_dir is None else Path(fixtures_dir)
        self._script = script or {}
        for step in self._script.values():
            if step.get("mode") not in MODES:
                raise ValueError(f"unknown fake mode {step.get('mode')!r}")
        self.calls: list[dict] = []

    def propose(self, task: str, payload: dict, request_id: str) -> dict:
        digest = sha256_hex(encode_request(build_request(task, payload)))
        step = self._script.get(request_id) or self._script.get(task) or {"mode": "replay"}
        self.calls.append({"request_id": request_id, "task": task, "request_sha256": digest, "mode": step["mode"]})
        common = {"request_id": request_id, "task": task, "request_sha256": digest}
        mode = step["mode"]
        if mode in STATUS_MODES:
            return make_result(**common, status=mode, error=make_error(f"fake_{mode}"))
        if mode == "replay":
            fixture = self._replayed(digest)
            if fixture is None:
                return make_result(**common, status="provider_error", error=make_error("fixture_missing", digest))
            seen = interpret_response(task, fixture["response"])
            return make_result(**common, cache_hit=True, latency_s=fixture["latency_s"], **seen)
        seen = interpret_response(task, synthetic_response(self._model_text(task, mode, step, digest)))
        return make_result(**common, latency_s=0.0, **seen)

    def _replayed(self, digest: str) -> dict | None:
        if self._fixtures_dir is None:
            return None
        return read_fixture(self._fixtures_dir / f"{digest}.json")

    def _model_text(self, task: str, mode: str, step: dict, digest: str) -> str:
        if mode == "invalid_json":
            return '{"truncated": '
        base = dict(step.get("output") or VALID_OUTPUTS[task])
        if mode == "schema_violation":
            base["note"] = "extra property"
        elif mode == "wrong_enum":
            if task not in _WRONG_ENUM:
                raise ValueError(f"{task} output has no enum field")
            field, value = _WRONG_ENUM[task]
            if task == "guide_claims":
                base["claims"] = [{**base["claims"][0], field: value}]
            else:
                base[field] = value
        elif mode == "stale_revision":
            if step.get("output") is None:
                fixture = self._replayed(digest)
                if fixture is not None:
                    base = json.loads(_only_text(fixture["response"]))
            if not isinstance(base.get("question_revision"), int) or base["question_revision"] < 1:
                raise ValueError("stale_revision needs a next_action output with question_revision >= 1")
            base["question_revision"] -= 1
        return json.dumps(base, ensure_ascii=False)


def _only_text(response: dict) -> str:
    return next(part["text"] for item in response["output"] if item.get("type") == "message"
                for part in item["content"] if part.get("type") == "output_text")
