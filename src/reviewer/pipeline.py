"""The review entry point (Seam 1): ReviewInput -> ReviewResult.

Walking skeleton: diff -> correctness specialist -> result. Later stages (context,
more specialists, verifier, triage) slot in between; the stats shape is already
per-stage so they only add keys.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from reviewer.agents.base import SpecialistRun, SpecialistSpec, run_specialist
from reviewer.agents.correctness import CORRECTNESS
from reviewer.config import Config
from reviewer.context import build_context
from reviewer.diff import parse_diff
from reviewer.llm import Provider, Usage
from reviewer.models import SEVERITY_ORDER, ReviewInput, ReviewResult
from reviewer.triage import triage
from reviewer.verifier import run_verifier, skip_verifier

log = logging.getLogger("reviewer")

SPECIALISTS: list[SpecialistSpec] = [CORRECTNESS]


class ReviewError(Exception):
    """The review could not be produced."""


class AllSpecialistsFailed(ReviewError):
    """Every specialist failed, so there is no review to show."""


def review(review_input: ReviewInput, *, provider: Provider, config: Config, verify: bool = True) -> ReviewResult:
    """`verify=False` skips the verifier (ablation): every finding keeps its own confidence."""
    started = time.perf_counter()
    files = parse_diff(review_input.diff)
    if not files:
        log.info("nothing to review (empty diff, or only lock/binary/generated files)")
        return ReviewResult(findings=[], stats=_stats({}, [], 0, time.perf_counter() - started, config))

    log.info("reviewing %d file(s) with %d specialist(s)", len(files), len(SPECIALISTS))
    bundle = build_context(review_input, files, config)  # context engine (#5): same bundle for every specialist
    runs: dict[str, tuple[SpecialistRun | Exception, float]] = {}

    def work(spec: SpecialistSpec):
        t0 = time.perf_counter()
        try:
            run: SpecialistRun | Exception = run_specialist(
                spec, review_input=review_input, files=files, provider=provider, config=config,
                bundle=bundle,
            )
        except Exception as e:  # one specialist failing must not lose the others (spec story 44)
            run = e
        return spec.name, run, time.perf_counter() - t0

    with ThreadPoolExecutor(max_workers=max(1, config.pipeline.max_workers)) as pool:
        for name, run, wall in pool.map(work, SPECIALISTS):
            runs[name] = (run, wall)
            if isinstance(run, Exception):
                log.warning("specialist %s failed: %s", name, run)
            else:
                log.info("specialist %s: %d finding(s)", name, len(run.findings))

    failures = {n: str(r) or type(r).__name__ for n, (r, _) in runs.items() if isinstance(r, Exception)}
    if len(failures) == len(runs):
        details = "; ".join(f"{n}: {msg}" for n, msg in failures.items())
        raise AllSpecialistsFailed(f"all specialists failed: {details}")

    findings = sorted(
        (f for r, _ in runs.values() if not isinstance(r, Exception) for f in r.findings),
        key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence),
    )

    # Verifier (skippable for ablations) -> triage.
    t0 = time.perf_counter()
    if verify:
        vrun = run_verifier(findings, review_input=review_input, files=files, provider=provider, config=config)
        verified = vrun.findings
    else:
        vrun, verified = None, skip_verifier(findings)
    verifier_wall = time.perf_counter() - t0
    after_verifier = sum(1 for f in verified if f.verdict == "keep" and not f.duplicate_of)
    triaged = triage(verified, config.pipeline)

    stats = _stats(runs, [f.path for f in files], len(triaged.findings), time.perf_counter() - started, config)
    stats["counts"]["after_verifier"] = after_verifier
    stats["counts"]["after_triage"] = sum(1 for f in triaged.findings if f.triage != "dropped")
    stats["context"] = bundle.stats  # context engine (#5)
    stats.setdefault("stages", {}).update({
        "verifier": _verifier_stats(vrun, len(findings), verifier_wall, config),
        "triage": {"dropped_by_reason": triaged.dropped_by_reason, "cap_removed": triaged.cap_removed},
    })
    if vrun:
        _add_usage(stats, vrun.usage, config.cost_usd(
            config.models.verifier, input_tokens=vrun.usage.input_tokens, output_tokens=vrun.usage.output_tokens))
    return ReviewResult(findings=triaged.findings, stats=stats)


def _verifier_stats(vrun, findings_in: int, wall_s: float, config: Config) -> dict:
    if vrun is None:
        return {"status": "skipped", "findings_in": findings_in}
    u = vrun.usage
    return {
        "status": vrun.status, "model": config.models.verifier, "wall_s": round(wall_s, 3),
        "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
        "cost_usd": config.cost_usd(config.models.verifier, input_tokens=u.input_tokens,
                                    output_tokens=u.output_tokens),
        "calls": u.calls, "cache_hits": u.cache_hits, "cached": u.cached,
        "findings_in": findings_in, "batches": vrun.batches, "failed_batches": vrun.failed_batches,
        "missing_verdicts": vrun.missing_verdicts, "errors": vrun.errors,
    }


def _add_usage(stats: dict, usage: Usage, cost: float) -> None:
    """Fold a later stage's usage into the totals."""
    t = stats["totals"]
    t["input_tokens"] += usage.input_tokens
    t["output_tokens"] += usage.output_tokens
    t["cost_usd"] += cost
    t["calls"] += usage.calls
    t["cache_hits"] += usage.cache_hits
    t["cached"] = t["cached"] and usage.cached if usage.calls else t["cached"]


def _stats(runs, files_reviewed: list[str], kept: int, wall_s: float, config: Config) -> dict:
    specialists: dict[str, dict] = {}
    total = Usage()
    raw = 0
    cost = 0.0
    for name, (run, wall) in runs.items():
        if isinstance(run, Exception):
            specialists[name] = {"status": "failed", "error": str(run) or type(run).__name__,
                                 "wall_s": round(wall, 3)}
            continue
        model = config.models.specialist
        spec_cost = config.cost_usd(model, input_tokens=run.usage.input_tokens,
                                    output_tokens=run.usage.output_tokens)
        specialists[name] = {
            "status": "ok",
            "model": model,
            "wall_s": round(wall, 3),
            "input_tokens": run.usage.input_tokens,
            "output_tokens": run.usage.output_tokens,
            "cost_usd": spec_cost,
            "calls": run.usage.calls,
            "cache_hits": run.usage.cache_hits,
            "cached": run.usage.cached,
            "raw_findings": run.raw_count,
            "kept_findings": len(run.findings),
            "dropped_invalid_anchor": run.dropped_invalid_anchor,
        }
        total += run.usage
        raw += run.raw_count
        cost += spec_cost
    return {
        "files_reviewed": files_reviewed,
        "specialists": specialists,
        "specialist_failures": {n: s["error"] for n, s in specialists.items() if s["status"] == "failed"},
        "counts": {"raw": raw, "after_verifier": kept, "after_triage": kept},
        "totals": {
            "input_tokens": total.input_tokens,
            "output_tokens": total.output_tokens,
            "cost_usd": cost,
            "wall_s": round(wall_s, 3),
            "calls": total.calls,
            "cache_hits": total.cache_hits,
            "cached": total.cached,
        },
    }
