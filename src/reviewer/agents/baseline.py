"""The baseline arm (eval only): one prompt that sees the diff and nothing else.

It uses the specialists' prompt scaffold (same JSON output contract) with the correctness and
security checklists merged into one prompt (D6) and the `models.baseline` model (the specialist
model, for fairness). It gets no context bundle, no team rules, no verifier and no triage. Its
findings are scored raw (D7): no confidence threshold, only the per-reviewer cap and the anchor check.
"""
from __future__ import annotations

import time

from reviewer.agents.base import SpecialistRun, SpecialistSpec, run_specialist
from reviewer.agents.correctness import CORRECTNESS
from reviewer.agents.security import SECURITY
from reviewer.config import Config
from reviewer.diff import parse_diff
from reviewer.llm import Provider
from reviewer.models import SEVERITY_ORDER, ReviewInput, ReviewResult, VerifiedFinding


def _merge(*lists: list[str]) -> list[str]:
    return list(dict.fromkeys(item for items in lists for item in items))


BASELINE = SpecialistSpec(
    name="baseline",
    role="code reviewer covering both correctness and security",
    flag=_merge(CORRECTNESS.flag, SECURITY.flag),
    do_not_flag=_merge(CORRECTNESS.do_not_flag, SECURITY.do_not_flag),
)


def review_baseline(review_input: ReviewInput, *, provider: Provider, config: Config) -> ReviewResult:
    """Same signature as `pipeline.review`, so it plugs into the eval as an arm. A failed
    model call raises (the eval records it as an error, never as a clean review)."""
    started = time.perf_counter()
    files = parse_diff(review_input.diff)
    if not files:
        return ReviewResult(findings=[], stats=_stats(None, 0.0, [], time.perf_counter() - started, config))

    t0 = time.perf_counter()
    run = run_specialist(
        BASELINE, review_input=review_input, files=files, provider=provider, config=config,
        model=config.models.baseline,
    )
    wall = time.perf_counter() - t0
    rule = config.pipeline.action_required
    findings = sorted(run.findings, key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence))
    verified = [
        # No verifier and no threshold (D7). The tier only labels the alarm level, using the
        # pipeline's action-required rule, so "any action-required finding on a clean PR"
        # means the same thing for both arms.
        VerifiedFinding(
            **f.model_dump(), verdict="keep", verifier_reason="",
            triage="action_required"
            if f.severity in rule.severities and f.confidence >= rule.min_confidence
            else "review_recommended",
        )
        for f in findings
    ]
    stats = _stats(run, wall, [f.path for f in files], time.perf_counter() - started, config)
    return ReviewResult(findings=verified, stats=stats)


def _stats(run: SpecialistRun | None, wall: float, files_reviewed: list[str], wall_s: float,
           config: Config) -> dict:
    specialists: dict[str, dict] = {}
    totals = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0, "cache_hits": 0,
              "cached": False}
    kept = raw = 0
    if run is not None:
        model = config.models.baseline
        cost = config.cost_usd(model, input_tokens=run.usage.input_tokens, output_tokens=run.usage.output_tokens)
        kept, raw = len(run.findings), run.raw_count
        specialists["baseline"] = {
            "status": "ok", "model": model, "wall_s": round(wall, 3),
            "input_tokens": run.usage.input_tokens, "output_tokens": run.usage.output_tokens,
            "cost_usd": cost, "calls": run.usage.calls, "cache_hits": run.usage.cache_hits,
            "cached": run.usage.cached, "raw_findings": raw, "kept_findings": kept,
            "dropped_invalid_anchor": run.dropped_invalid_anchor,
        }
        totals.update(input_tokens=run.usage.input_tokens, output_tokens=run.usage.output_tokens,
                      cost_usd=cost, calls=run.usage.calls, cache_hits=run.usage.cache_hits,
                      cached=run.usage.cached)
    return {
        "files_reviewed": files_reviewed,
        "specialists": specialists,
        "specialist_failures": {},
        "counts": {"raw": raw, "after_verifier": kept, "after_triage": kept},
        "totals": {**totals, "wall_s": round(wall_s, 3)},
    }
