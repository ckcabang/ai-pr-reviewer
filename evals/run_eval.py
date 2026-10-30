"""The eval command: arms x runs x dataset PRs -> scored results saved under `evals/out/` (D17).

    uv run python -m evals.run_eval --arms baseline --runs 3 [--out evals/out/<timestamp>/] [--prs pr06,pr09]

Every dataset PR is materialized into a real git repo, turned into a `ReviewInput` the way the CLI
does (diff of base...head, title, description) and reviewed by each arm, `--runs` times, with the
disk cache OFF (a cache would make repeat runs meaningless). Tasks run in parallel up to
`pipeline.max_workers`. The judge then matches each arm's findings to the PR's expected issues
(`evals.judge`). Results go to `<out>/results.json` (shape: see `build_results`) and a plain
`<out>/scorecard.html` is rebuilt from that file without any provider call.
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from evals.arms import ARMS, Arm, resolve
from evals.dataset import DatasetPR, Materialized, load_all, load_pr, materialize
from evals.judge import FunctionLocator, judge_pr
from evals.judge_fixtures import check_judge_agreement
from evals.metrics import summarize
from reviewer.cli import _git_diff
from reviewer.config import Config, load_config
from reviewer.llm import Provider, build_provider
from reviewer.models import ReviewInput

log = logging.getLogger("evals")

OUT_ROOT = Path(__file__).resolve().parent / "out"
SCHEMA_VERSION = 1


@dataclasses.dataclass
class _Prepared:
    pr: DatasetPR
    review_input: ReviewInput
    locator: FunctionLocator


def evaluate(
    *,
    arms: Sequence[str],
    runs: int,
    config: Config,
    provider: Provider | None = None,
    out_dir: Path | str | None = None,
    pr_ids: Sequence[str] | None = None,
    arm_registry: Mapping[str, Arm] | None = None,
    check_judge: bool = False,
) -> dict:
    """Run the eval and return (and save) the results dict.

    `provider` replaces the real model provider (tests inject a fake); the cache is never used.
    `pr_ids` restricts the dataset (default: every PR). `check_judge` also runs the judge
    agreement fixture and stores it under `judge_agreement`.
    """
    chosen = resolve(list(arms), arm_registry)
    if runs < 1:
        raise ValueError("--runs must be at least 1")
    prs = [load_pr(i) for i in pr_ids] if pr_ids else load_all()
    if not prs:
        raise ValueError("no dataset PRs to evaluate")
    prs.sort(key=lambda p: p.id)
    provider = build_provider(config, use_cache=False, inner=provider, lazy=False)  # cache off; key needed up front
    started = time.perf_counter()

    materialized: list[Materialized] = []
    try:
        prepared: dict[str, _Prepared] = {}
        for pr in prs:
            m = materialize(pr)
            materialized.append(m)
            prepared[pr.id] = _Prepared(
                pr=pr,
                review_input=ReviewInput(repo_root=m.repo, diff=_git_diff(m.repo, m.base_ref),
                                         title=pr.title, description=pr.description),
                locator=FunctionLocator(m.repo),
            )

        tasks = [(arm, run, prepared[pr.id]) for run in range(runs) for arm in chosen for pr in prs]
        workers = max(1, config.pipeline.max_workers)
        log.info("evaluating %d arm(s) x %d run(s) x %d PR(s) = %d reviews, %d worker(s)",
                 len(chosen), runs, len(prs), len(tasks), workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            records = list(pool.map(lambda t: _run_task(*t, provider=provider, config=config, runs=runs), tasks))
        agreement = check_judge_agreement(provider, config) if check_judge else None
    finally:
        for m in materialized:
            shutil.rmtree(m.repo, ignore_errors=True)

    results = build_results(
        arms=[a.name for a in chosen], runs=runs, prs=prs, records=records, config=config,
        workers=workers, agreement=agreement, wall_s=time.perf_counter() - started,
    )
    folder = Path(out_dir) if out_dir is not None else OUT_ROOT / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results["out_dir"] = str(folder)
    save(results, folder)
    return results


def _run_task(arm: Arm, run: int, prep: _Prepared, *, provider: Provider, config: Config, runs: int) -> dict:
    """One arm reviewing one PR once, then judged. Never raises: failures become the record's status."""
    record: dict = {"arm": arm.name, "run": run, "pr": prep.pr.id}
    t0 = time.perf_counter()
    try:
        result = arm.run(prep.review_input, provider=provider, config=config)
    except Exception as e:
        record.update(status="error", error=f"{type(e).__name__}: {e}", wall_s=round(time.perf_counter() - t0, 3))
        log.warning("[%s run %d/%d] %s: review failed: %s", arm.name, run + 1, runs, prep.pr.id, record["error"])
        return record
    record["wall_s"] = round(time.perf_counter() - t0, 3)
    scored = [f for f in result.findings if f.triage != "dropped"]  # what a user would have seen
    record.update(
        findings=[f.model_dump(mode="json") for f in result.findings],
        scored_finding_ids=[f.id for f in scored],
        stats=result.stats,
    )
    try:
        outcome = judge_pr(prep.pr, scored, provider=provider, config=config, locator=prep.locator)
    except Exception as e:
        record.update(status="judge_error", error=f"{type(e).__name__}: {e}")
        log.warning("[%s run %d/%d] %s: judge failed: %s", arm.name, run + 1, runs, prep.pr.id, record["error"])
        return record
    record.update(status="ok", judge=outcome.to_dict())
    log.info("[%s run %d/%d] %s: %d finding(s), tp=%d fp=%d fn=%d", arm.name, run + 1, runs, prep.pr.id,
             len(scored), outcome.tp, outcome.fp, outcome.fn)
    return record


def build_results(*, arms, runs, prs, records, config, workers, agreement, wall_s) -> dict:
    """The results file (`results.json`):

    schema_version, created_at, out_dir
    config      {arms, runs, pr_ids, workers, cache: "off", models, pipeline}
    dataset     [{id, title, description, is_clean, expected_issues: [{id, file, line_hint, area,
                 severity, requires, rule_id, description, related: [{file, line_hint}]}]}]
    records     one per (arm, run, pr), sorted by (arm order, run, pr):
                {arm, run (0-based), pr, status ok | error | judge_error, error?, wall_s,
                 findings [Finding + triage, dropped ones included], scored_finding_ids [ids judged],
                 stats (the arm's ReviewResult.stats),
                 judge {candidates [{expected_id, finding_key, finding_index, finding_id, score, reason,
                                     status assigned | superseded | below_threshold | location_rejected | unknown_id}],
                        matches [{expected_id, finding_id, finding_index, score, reason}],
                        tp, fp, fn, matched_expected, unmatched_expected, unmatched_findings, usage, cost_usd}}
                (`finding_index` indexes `scored_finding_ids`.)
    judge_agreement  {n, agree, agreement, judge_model, cost_usd, cases: [...]} or null
    summary     metrics per arm (`evals.metrics.summarize`; always re-derivable from the above)
    totals      {review_cost_usd, judge_cost_usd, wall_s}
    """
    arm_order = {a: i for i, a in enumerate(arms)}
    records = sorted(records, key=lambda r: (arm_order[r["arm"]], r["run"], r["pr"]))
    results = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "arms": list(arms), "runs": runs, "pr_ids": [p.id for p in prs], "workers": workers,
            "cache": "off", "models": config.models.model_dump(),
            "pipeline": config.pipeline.model_dump(mode="json"),
        },
        "dataset": [_dataset_entry(p) for p in prs],
        "records": records,
        "judge_agreement": agreement,
    }
    results["summary"] = summarize(results)
    results["totals"] = {
        "review_cost_usd": sum(s["review_cost_usd"] for s in results["summary"].values()),
        "judge_cost_usd": sum(s["judge_cost_usd"] for s in results["summary"].values())
        + (agreement["cost_usd"] if agreement else 0.0),
        "wall_s": round(wall_s, 1),
    }
    return results


def _dataset_entry(pr: DatasetPR) -> dict:
    return {
        "id": pr.id, "title": pr.title, "description": pr.description, "is_clean": pr.is_clean,
        "expected_issues": [dataclasses.asdict(i) for i in pr.expected_issues],
    }


def save(results: dict, folder: Path) -> None:
    """Write `results.json` and the plain `scorecard.html` rebuilt from it."""
    import json

    from evals.scorecard import render_html

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    (folder / "scorecard.html").write_text(render_html(json.loads((folder / "results.json").read_text())))


def main(argv: list[str] | None = None, *, provider: Provider | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.run_eval", description="Score review arms on the dataset.")
    parser.add_argument("--arms", default="baseline", help=f"Comma-separated arms (known: {', '.join(ARMS)}).")
    parser.add_argument("--runs", type=int, default=3, help="Full passes over the dataset (default 3).")
    parser.add_argument("--out", metavar="DIR", help="Output folder (default: evals/out/<timestamp>/).")
    parser.add_argument("--prs", metavar="IDS", help="Comma-separated dataset PR ids (default: all).")
    parser.add_argument("--config", metavar="FILE", help="Config file (default: config.yaml).")
    parser.add_argument("--skip-judge-check", action="store_true", help="Skip the judge agreement fixture.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(message)s", force=True)
    log.setLevel(logging.INFO)
    try:
        results = evaluate(
            arms=[a.strip() for a in args.arms.split(",") if a.strip()], runs=args.runs,
            config=load_config(args.config), provider=provider, out_dir=args.out,
            pr_ids=[p.strip() for p in args.prs.split(",") if p.strip()] if args.prs else None,
            check_judge=not args.skip_judge_check,
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    _print_summary(results)
    return 0


def _print_summary(results: dict) -> None:
    from evals.scorecard import degraded_text

    def cell(a):
        return "n/a" if a is None else f"{a['mean']:.2f}" + ("" if a["std"] is None else f" ± {a['std']:.2f}")

    print(f"results: {results['out_dir']}/results.json   scorecard: {results['out_dir']}/scorecard.html")
    for arm, s in results["summary"].items():
        print(f"{arm}: precision {cell(s['precision'])}  recall {cell(s['recall'])}  F1 {cell(s['f1'])}  "
              f"findings/PR {cell(s['findings_per_pr'])}  cost/PR ${s['cost_usd_per_pr']['mean']:.4f}"
              if s["cost_usd_per_pr"] else f"{arm}: no scored runs")
        if any(s["errors"].values()):
            print(f"  WARNING: {s['errors']['review']} review error(s), {s['errors']['judge']} judge error(s) "
                  f"were left out of the metrics")
        if note := degraded_text(arm, s["degraded"]):
            print(f"  WARNING: {note}")
    agreement = results["judge_agreement"]
    if agreement:
        print(f"judge agreement: {agreement['agree']}/{agreement['n']}")
    t = results["totals"]
    print(f"cost: arms ${t['review_cost_usd']:.4f} + judge ${t['judge_cost_usd']:.4f}   wall {t['wall_s']}s")


if __name__ == "__main__":
    sys.exit(main())
