"""Metrics from the saved eval results: pure functions, no provider calls (D17).

`summarize(results)` is what the scorecard shows. Per arm and per run, over the PRs that were
reviewed AND judged successfully, it computes (pooled over PRs, i.e. micro-averaged):

- precision = TP / (TP + FP), recall = TP / (TP + FN), F1 = 2TP / (2TP + FP + FN)
  (None when the denominator is 0, e.g. precision of a run with no findings at all)
- findings per PR, clean-PR false-alarm rates, recall by required context and by area
- tokens, cost and latency (p50/p95 over PRs) of the arm itself (the judge's cost is separate)

then reports mean and sample std over runs as `{"mean", "std", "n"}` (std is None for one run).
Failed reviews and failed judge calls are excluded from the metrics and counted in `errors`.
"""
from __future__ import annotations

import statistics
from typing import Iterable

import numpy as np

AREAS = ("correctness", "security", "standards")
REQUIRES = ("diff", "rules", "cross_file")


def agg(values: Iterable[float | None]) -> dict | None:
    """mean / sample std / n over the defined values, or None if there are none."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"mean": statistics.fmean(vals), "std": statistics.stdev(vals) if len(vals) > 1 else None,
            "n": len(vals)}


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _percentile(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


def run_metrics(records: list[dict], dataset: dict[str, dict]) -> dict:
    """Metrics of one arm in one run, from its scored records (status "ok")."""
    tp = sum(r["judge"]["tp"] for r in records)
    fp = sum(r["judge"]["fp"] for r in records)
    fn = sum(r["judge"]["fn"] for r in records)

    clean = [r for r in records if dataset[r["pr"]]["is_clean"]]
    any_finding = [len(r["scored_finding_ids"]) > 0 for r in clean]
    any_action = [any(f["triage"] == "action_required" for f in r["findings"]) for r in clean]

    expected_total: dict[tuple[str, str], int] = {}
    expected_hit: dict[tuple[str, str], int] = {}
    for r in records:
        matched = set(r["judge"]["matched_expected"])
        for issue in dataset[r["pr"]]["expected_issues"]:
            for dim in (("requires", issue["requires"]), ("area", issue["area"])):
                expected_total[dim] = expected_total.get(dim, 0) + 1
                expected_hit[dim] = expected_hit.get(dim, 0) + (issue["id"] in matched)

    totals = [r["stats"].get("totals", {}) for r in records]
    latencies = [r["wall_s"] for r in records]
    return {
        "n_prs": len(records),
        "tp": tp, "fp": fp, "fn": fn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
        "findings_per_pr": _mean([len(r["scored_finding_ids"]) for r in records]),
        "clean_pr": {
            "n": len(clean),
            "any_finding_rate": _mean([float(x) for x in any_finding]),
            "action_required_rate": _mean([float(x) for x in any_action]),
        },
        "recall_by_requires": {k: _ratio(expected_hit.get(("requires", k), 0), expected_total.get(("requires", k), 0))
                               for k in REQUIRES},
        "recall_by_area": {k: _ratio(expected_hit.get(("area", k), 0), expected_total.get(("area", k), 0))
                           for k in AREAS},
        "input_tokens_per_pr": _mean([t.get("input_tokens", 0) for t in totals]),
        "output_tokens_per_pr": _mean([t.get("output_tokens", 0) for t in totals]),
        "cost_usd_per_pr": _mean([t.get("cost_usd", 0.0) for t in totals]),
        "latency_p50_s": _percentile(latencies, 50),
        "latency_p95_s": _percentile(latencies, 95),
    }


_SCALARS = ("precision", "recall", "f1", "findings_per_pr", "input_tokens_per_pr", "output_tokens_per_pr",
            "cost_usd_per_pr", "latency_p50_s", "latency_p95_s")


def summarize_arm(arm: str, records: list[dict], dataset_list: list[dict]) -> dict:
    dataset = {d["id"]: d for d in dataset_list}
    mine = [r for r in records if r["arm"] == arm]
    scored = [r for r in mine if r["status"] == "ok"]
    run_ids = sorted({r["run"] for r in mine})
    runs = []
    for run in run_ids:
        in_run = [r for r in scored if r["run"] == run]
        if in_run:
            runs.append({"run": run, **run_metrics(in_run, dataset)})

    summary: dict = {"n_runs": len(run_ids), "n_scored_runs": len(runs)}
    for key in _SCALARS:
        summary[key] = agg(r[key] for r in runs)
    summary["clean_pr"] = {
        "n": max((r["clean_pr"]["n"] for r in runs), default=0),
        "any_finding_rate": agg(r["clean_pr"]["any_finding_rate"] for r in runs),
        "action_required_rate": agg(r["clean_pr"]["action_required_rate"] for r in runs),
    }
    summary["recall_by_requires"] = {k: agg(r["recall_by_requires"][k] for r in runs) for k in REQUIRES}
    summary["recall_by_area"] = {k: agg(r["recall_by_area"][k] for r in runs) for k in AREAS}
    summary["errors"] = {"review": sum(r["status"] == "error" for r in mine),
                         "judge": sum(r["status"] == "judge_error" for r in mine)}
    summary["review_cost_usd"] = sum(r["stats"].get("totals", {}).get("cost_usd", 0.0)
                                     for r in mine if "stats" in r)
    summary["judge_cost_usd"] = sum(r["judge"]["cost_usd"] for r in scored)
    summary["per_pr"] = _per_pr(scored, dataset_list)
    summary["runs"] = runs
    return summary


def _per_pr(scored: list[dict], dataset_list: list[dict]) -> dict:
    out = {}
    for pr in dataset_list:
        recs = [r for r in scored if r["pr"] == pr["id"]]
        out[pr["id"]] = {
            "scored_runs": len(recs),
            "findings": _mean([len(r["scored_finding_ids"]) for r in recs]),
            "tp": _mean([r["judge"]["tp"] for r in recs]),
            "fp": _mean([r["judge"]["fp"] for r in recs]),
            "fn": _mean([r["judge"]["fn"] for r in recs]),
            "expected": {i["id"]: {"matched": sum(i["id"] in r["judge"]["matched_expected"] for r in recs),
                                   "runs": len(recs)} for i in pr["expected_issues"]},
        }
    return out


def summarize(results: dict) -> dict:
    """arm name -> summary, for every arm in the results file (in the order they were run)."""
    return {arm: summarize_arm(arm, results["records"], results["dataset"])
            for arm in results["config"]["arms"]}
