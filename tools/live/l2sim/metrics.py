"""Per-arm metrics pooled over episode-runs (sums first, then ratios) per PREREG §6, and the §7 acceptance check."""

from collections import Counter

from simulator import COUNTERS, INTEGRITY

INVALID_RATE_MAX = 0.05
FALLBACK_RATE_MAX = 0.20


def _ratio(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else None


def episode_metrics(counters):
    resolved = counters["resolved_by_operator"] + counters["resolved_by_linker"]
    return {
        "resolved_parts": resolved,
        "resolved_per_interruption": _ratio(resolved, counters["interruptions"]),
        "resolved_per_question_interruption": _ratio(resolved, counters["question_interruptions"]),
        "invalid_action_rate": _ratio(counters["invalid_proposals"], counters["proposals"]),
        "provider_fallback_rate": _ratio(counters["fallbacks_provider"], counters["turns"]),
        "calls_per_resolved_part": _ratio(counters["proposer_calls"], resolved),
    }


def summarize(results):
    totals = Counter({name: 0 for name in COUNTERS})
    rejections = Counter()
    for result in results:
        totals.update(result["counters"])
        rejections.update(result["validator_rejections"])
    totals = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in totals.items()}
    integrity = {name: totals[name] for name in INTEGRITY}
    return {
        "episode_runs": len(results),
        "totals": totals,
        **episode_metrics(totals),
        "validator_rejections": dict(sorted(rejections.items())),
        "effect_unknown": {"protocol_prompt": totals["effect_unknown_protocol_prompt"],
                           "ask_dependency": totals["effect_unknown_ask_dependency"],
                           "request_view": "disabled until AgentCam G1; never executed"},
        "integrity": integrity,
        "integrity_ok": all(v == 0 for v in integrity.values()),
    }


def accept(l2, p0, pr, complete):
    """PREREG §7 verdict from three arm summaries: accepted | rejected | inconclusive, with the checked items."""
    checks = {
        "complete": complete,
        "provider_fallback_rate_ok": (l2["provider_fallback_rate"] or 0.0) <= FALLBACK_RATE_MAX,
        "beats_p0": _gt(l2["resolved_per_interruption"], p0["resolved_per_interruption"]),
        "beats_pr": _gt(l2["resolved_per_interruption"], pr["resolved_per_interruption"]),
        "integrity_ok": l2["integrity_ok"],
        "invalid_rate_ok": (l2["invalid_action_rate"] or 0.0) <= INVALID_RATE_MAX,
    }
    if not (checks["complete"] and checks["provider_fallback_rate_ok"]):
        verdict = "inconclusive"
    else:
        verdict = "accepted" if all(checks.values()) else "rejected"
    return {"verdict": verdict, "checks": checks}


def _gt(a, b):
    return a is not None and (b is None or a > b)


def paired(results_a, results_b):
    """Win/tie/loss of arm A against arm B on resolved-per-interruption per (episode, replication seed)."""
    key = lambda r: (r["episode"], r["replication_seed"])  # noqa: E731
    b_by_key = {key(r): r for r in results_b}
    tally = Counter()
    for r in results_a:
        other = b_by_key.get(key(r))
        if other is None:
            tally["unpaired"] += 1
            continue
        a_val = episode_metrics(r["counters"])["resolved_per_interruption"] or 0.0
        b_val = episode_metrics(other["counters"])["resolved_per_interruption"] or 0.0
        tally["win" if a_val > b_val else "loss" if a_val < b_val else "tie"] += 1
    return {k: tally[k] for k in ("win", "tie", "loss", "unpaired")}
