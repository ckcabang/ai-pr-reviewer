"""Plain HTML tables rebuilt from a saved results file, with no provider calls (D17).

    uv run python -m evals.scorecard --from evals/out/<timestamp>/

This is the tracer version (tables only); the full scorecard (#13) replaces `render_html` and keeps
the same input: the `results.json` documented in `evals.run_eval.build_results`. Metrics are
re-derived from the raw records with `evals.metrics.summarize`, so the page never depends on the
stored `summary`.
"""
from __future__ import annotations

import argparse
import json
import sys
from html import escape
from pathlib import Path

from evals.metrics import AREAS, REQUIRES, summarize

_CSS = """
body{font:14px/1.45 system-ui,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px;color:#1b1f23;background:#fff}
h1{font-size:22px}h2{font-size:17px;margin-top:28px}
table{border-collapse:collapse;margin:8px 0;width:100%}
th,td{border:1px solid #d0d7de;padding:5px 9px;text-align:right}
th:first-child,td:first-child{text-align:left}
th{background:#f3f4f6}.note{color:#57606a}.warn{color:#9a3412;font-weight:600}
@media (prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}th{background:#161b22}
th,td{border-color:#30363d}.note{color:#8b949e}.warn{color:#fb923c}}
"""


def _num(a: dict | None, digits: int = 2) -> str:
    """`mean ± std` (std omitted for a single run); n/a when undefined."""
    if a is None:
        return "n/a"
    out = f"{a['mean']:.{digits}f}"
    return out if a["std"] is None else f"{out} ± {a['std']:.{digits}f}"


def _pct(a: dict | None) -> str:
    if a is None:
        return "n/a"
    out = f"{a['mean'] * 100:.0f}%"
    return out if a["std"] is None else f"{out} ± {a['std'] * 100:.0f}%"


def _usd(a: dict | None) -> str:
    return "n/a" if a is None else f"${a['mean']:.4f}"


def _table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{escape(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def render_html(results: dict) -> str:
    summary = summarize(results)
    cfg = results["config"]
    arms = cfg["arms"]
    parts = [f"<h1>Review eval results</h1>",
             f"<p class='note'>{escape(results['created_at'])} · arms: {escape(', '.join(arms))} · "
             f"{cfg['runs']} run(s) · {len(results['dataset'])} dataset PR(s) "
             f"({sum(d['is_clean'] for d in results['dataset'])} clean) · cache {escape(cfg['cache'])} · "
             f"models: {escape(json.dumps(cfg['models']))}</p>"]

    for arm, s in summary.items():
        if any(s["errors"].values()):
            parts.append(f"<p class='warn'>{escape(arm)}: {s['errors']['review']} review error(s) and "
                         f"{s['errors']['judge']} judge error(s) are left out of the metrics below.</p>")

    parts.append("<h2>Headline metrics (mean ± std over runs)</h2>")
    parts.append(_table(
        ["arm", "precision", "recall", "F1", "findings / PR", "clean PRs with any finding",
         "clean PRs with action-required", "input tok / PR", "output tok / PR", "cost / PR",
         "latency p50 (s)", "latency p95 (s)"],
        [[arm, _num(s["precision"]), _num(s["recall"]), _num(s["f1"]), _num(s["findings_per_pr"]),
          _pct(s["clean_pr"]["any_finding_rate"]), _pct(s["clean_pr"]["action_required_rate"]),
          _num(s["input_tokens_per_pr"], 0), _num(s["output_tokens_per_pr"], 0), _usd(s["cost_usd_per_pr"]),
          _num(s["latency_p50_s"], 1), _num(s["latency_p95_s"], 1)] for arm, s in summary.items()],
    ))

    parts.append("<h2>Recall by required context</h2>")
    parts.append(_table(["arm", *REQUIRES], [[arm, *(_pct(s["recall_by_requires"][k]) for k in REQUIRES)]
                                             for arm, s in summary.items()]))
    parts.append("<h2>Recall by area</h2>")
    parts.append(_table(["arm", *AREAS], [[arm, *(_pct(s["recall_by_area"][k]) for k in AREAS)]
                                          for arm, s in summary.items()]))

    parts.append("<h2>Per PR (mean over runs)</h2>")
    rows = []
    for pr in results["dataset"]:
        row = [f"{pr['id']}: {pr['title']}", str(len(pr["expected_issues"]))]
        for arm in arms:
            p = summary[arm]["per_pr"][pr["id"]]
            hits = ", ".join(f"{i}: {h['matched']}/{h['runs']}" for i, h in p["expected"].items()) or "clean"
            row += [_fmt(p["findings"]), _fmt(p["tp"]), _fmt(p["fp"]), _fmt(p["fn"]), hits]
        rows.append(row)
    head = ["PR", "expected"]
    for arm in arms:
        head += [f"{arm}: findings", "TP", "FP", "FN", "expected issues found (runs)"]
    parts.append(_table(head, rows))

    agreement = results.get("judge_agreement")
    if agreement:
        parts.append(f"<h2>Judge agreement</h2><p>{agreement['agree']} of {agreement['n']} hand-labeled cases "
                     f"agree with the judge ({escape(agreement['judge_model'])}).</p>")
        parts.append(_table(["case", "should match", "judge says", "agrees"],
                            [[c["id"], str(c["should_match"]), str(c["matched"]), "yes" if c["agrees"] else "NO"]
                             for c in agreement["cases"]]))
    totals = results.get("totals", {})
    parts.append(f"<p class='note'>Spend: arms ${totals.get('review_cost_usd', 0):.4f}, "
                 f"judge ${totals.get('judge_cost_usd', 0):.4f}. Metrics are pooled over PRs within a run; "
                 "the judge matches findings to expected issues one-to-one (score >= 0.5).</p>")
    body = "\n".join(parts)
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Review eval results</title><style>{_CSS}</style></head><body>\n{body}\n</body></html>\n")


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1f}"


def rebuild(folder: Path | str) -> Path:
    """Rewrite `scorecard.html` in `folder` from its `results.json` (no provider calls)."""
    folder = Path(folder)
    results = json.loads((folder / "results.json").read_text())
    path = folder / "scorecard.html"
    path.write_text(render_html(results))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.scorecard", description="Rebuild the HTML from saved results.")
    parser.add_argument("--from", dest="folder", required=True, metavar="DIR", help="Results folder.")
    args = parser.parse_args(argv)
    try:
        print(rebuild(args.folder))
    except (OSError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
