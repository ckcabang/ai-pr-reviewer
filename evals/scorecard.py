"""The self-contained HTML scorecard, rebuilt from a saved results file with no provider calls (D17).

    uv run python -m evals.scorecard --from evals/out/<timestamp>/

One page, inline CSS and SVG only (no CDN, no script, no external request), readable at a glance:
at-a-glance tiles, headline table, grouped P/R/F1 chart, recall by required context, per-PR table,
two pipeline example findings next to what the baseline said, and a methodology note with the
limitations. The only input is the `results.json` documented in `evals.run_eval.build_results`;
every metric is re-derived from the raw records with `evals.metrics.summarize` (2 to 4 arms; more
render too, the palette just repeats).
"""
from __future__ import annotations

import argparse
import json
import sys
from html import escape
from pathlib import Path

from evals.metrics import REQUIRES, summarize

_CSS = """
:root{--bg:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#6b6963;--grid:#e1e0d9;--axis:#c3c2b7;
--border:rgba(11,11,11,.12);--head:#f0efec;--good:#006300;--warn:#9a3412;--partial:#8a5a00;--bad:#b42323;
--s0:#2a78d6;--s1:#eb6834;--s2:#1baf7a;--s3:#eda100}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
--ink2:#c3c2b7;--muted:#a3a198;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.12);--head:#242422;
--good:#0ca30c;--warn:#fb923c;--partial:#fab219;--bad:#ec6b6b;--s0:#3987e5;--s1:#d95926;--s2:#199e70;--s3:#c98500}}
:root[data-theme="dark"]{--bg:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#a3a198;--grid:#2c2c2a;
--axis:#383835;--border:rgba(255,255,255,.12);--head:#242422;--good:#0ca30c;--warn:#fb923c;--partial:#fab219;
--bad:#ec6b6b;--s0:#3987e5;--s1:#d95926;--s2:#199e70;--s3:#c98500}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1120px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:0 0 4px}h3{font-size:15px;margin:0 0 4px}
section{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:18px 20px;margin:18px 0}
.sub,.note{color:var(--ink2);margin:0 0 12px}.note{font-size:13px}
.warn{color:var(--warn);font-weight:600;margin:8px 0}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:7px 10px;border-bottom:1px solid var(--grid);text-align:right;vertical-align:top}
th:first-child,td:first-child{text-align:left}
td:not(:first-child){white-space:nowrap}
thead th{min-width:84px;background:var(--head);font-size:12.5px;font-weight:600;color:var(--ink2);vertical-align:bottom}
td.best{font-weight:700}
.swatch{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;margin:6px 0 10px;color:var(--ink2);font-size:13px}
.a0{background:var(--s0);fill:var(--s0)}.a1{background:var(--s1);fill:var(--s1)}
.a2{background:var(--s2);fill:var(--s2)}.a3{background:var(--s3);fill:var(--s3)}
svg{width:100%;height:auto;display:block}
svg text{fill:var(--ink2);font:11.5px system-ui,sans-serif}svg .val{fill:var(--ink);font-weight:600}
svg .grid{stroke:var(--grid);stroke-width:1}svg .axis{stroke:var(--axis);stroke-width:1}
svg .err{stroke:var(--ink);stroke-width:1.2}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-top:8px}
.tile{border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.tile .lab{color:var(--ink2);font-size:13px}.tile .big{font-size:30px;font-weight:700;line-height:1.2}
.tile .vs{color:var(--ink2);font-size:13px}
.prrow th{background:var(--head);text-align:left;font-size:14px;color:var(--ink)}
.prrow td{background:var(--head);font-size:13px;color:var(--ink2);font-weight:600}
.iss td:first-child{padding-left:22px;font-size:13.5px}.iss .tags{color:var(--muted);font-size:12px}
.ok{color:var(--good);font-weight:700}.miss{color:var(--bad);font-weight:700}.part{color:var(--partial);font-weight:700}
.badge{font-size:11.5px;font-weight:600;border:1px solid var(--border);border-radius:99px;padding:1px 8px;margin-left:6px;color:var(--ink2)}
.examples{display:grid;gap:16px}
.example{border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:10px}
.card{border:1px solid var(--border);border-radius:8px;padding:10px 12px;background:var(--bg)}
.card h4{margin:0 0 6px;font-size:13px;color:var(--ink2);text-transform:uppercase;letter-spacing:.04em}
.f{margin:0 0 10px}.f .t{font-weight:600}.f .loc{color:var(--muted);font-size:12.5px;font-family:ui-monospace,monospace}
pre{margin:6px 0;padding:8px 10px;background:var(--head);border-radius:6px;overflow-x:auto;font-size:12.5px;white-space:pre-wrap}
ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
code{font-family:ui-monospace,monospace;font-size:.92em}
"""

_SLOTS = 4



def _e(x: object) -> str:
    return escape(str(x))


def _label(arm: str) -> str:
    return arm


def _cls(i: int) -> str:
    return f"a{i % _SLOTS}"


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


def _mean(a: dict | None, *, pct: bool = False) -> str:
    """The mean alone (for the big tiles)."""
    if a is None:
        return "n/a"
    return f"{a['mean'] * 100:.0f}%" if pct else f"{a['mean']:.2f}"


def _usd(a: dict | None) -> str:
    return "n/a" if a is None else f"${a['mean']:.4f}"


def _secs(a: dict | None) -> str:
    return "n/a" if a is None else f"{a['mean']:.1f} s"


def _avg(x: float | None) -> str:
    """A per-PR mean over runs: whole numbers stay whole."""
    if x is None:
        return "n/a"
    return str(round(x)) if abs(x - round(x)) < 1e-9 else f"{x:.1f}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# ---------------------------------------------------------------- charts

def _grouped_bars(groups: list[tuple[str, list[dict | None]]], arms: list[str], *, pct: bool, aria: str) -> str:
    """Inline-SVG grouped bar chart on a 0..1 axis: one group per label, one bar per arm.

    Each value is an `{"mean", "std"}` aggregate (or None -> "n/a"); std is drawn as a thin error bar.
    """
    w, h, ml, mr, mt, mb = 720, 300, 42, 10, 18, 40
    pw, ph = w - ml - mr, h - mt - mb
    y = lambda v: mt + ph * (1 - v)  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{_e(aria)}">']
    for tick in (0, 0.25, 0.5, 0.75, 1):
        name = f"{tick * 100:.0f}%" if pct else f"{tick:.2f}"
        out.append(f'<line class="{"axis" if tick == 0 else "grid"}" x1="{ml}" x2="{w - mr}" y1="{y(tick):.1f}" '
                   f'y2="{y(tick):.1f}"/><text x="{ml - 6}" y="{y(tick) + 4:.1f}" text-anchor="end">{name}</text>')
    gw = pw / len(groups)
    inner = gw * 0.74
    gap = 2
    bw = (inner - gap * (len(arms) - 1)) / len(arms)
    for gi, (glabel, values) in enumerate(groups):
        x0 = ml + gi * gw + (gw - inner) / 2
        for ai, (arm, agg) in enumerate(zip(arms, values)):
            x = x0 + ai * (bw + gap)
            cx = x + bw / 2
            if agg is None:
                out.append(f'<text class="val" x="{cx:.1f}" y="{y(0) - 5:.1f}" text-anchor="middle">n/a</text>')
                continue
            m = max(0.0, min(1.0, agg["mean"]))
            std = agg["std"]
            shown = f"{m * 100:.0f}%" if pct else f"{m:.2f}"
            tip = f"{_label(arm)} · {glabel}: " + (_pct(agg) if pct else _num(agg))
            top = y(m)
            out.append(f'<g><title>{_e(tip)}</title><rect class="{_cls(ai)}" x="{x:.1f}" y="{top:.1f}" '
                       f'width="{bw:.1f}" height="{max(y(0) - top, 0.5):.1f}" rx="2"/></g>')
            label_y = top - 5
            if std:
                lo, hi = y(max(0.0, m - std)), y(min(1.0, m + std))
                out.append(f'<path class="err" fill="none" d="M{cx:.1f} {lo:.1f}V{hi:.1f}M{cx - 3:.1f} {lo:.1f}h6'
                           f'M{cx - 3:.1f} {hi:.1f}h6"/>')
                label_y = min(label_y, hi - 4)
            out.append(f'<text class="val" x="{cx:.1f}" y="{max(label_y, 11):.1f}" text-anchor="middle">{shown}</text>')
        out.append(f'<text x="{ml + gi * gw + gw / 2:.1f}" y="{h - 14}" text-anchor="middle">{_e(glabel)}</text>')
    out.append("</svg>")
    return "".join(out)


def _legend(arms: list[str]) -> str:
    return ('<div class="legend">' + "".join(f'<span><span class="swatch {_cls(i)}"></span>{_e(_label(a))}</span>'
                                              for i, a in enumerate(arms)) + "</div>")


# ---------------------------------------------------------------- sections

def _featured(arms: list[str]) -> tuple[str | None, str | None]:
    """(arm shown in examples and tiles, arm it is compared with). The pipeline vs the baseline."""
    ref = "baseline" if "baseline" in arms else None
    if "pipeline" in arms:
        return "pipeline", ref
    others = [a for a in arms if a != ref and a.startswith("pipeline")]
    return (others[0] if others else None), ref


def _glance(summary: dict, arms: list[str]) -> str:
    feat, ref = _featured(arms)
    if feat is None or ref is None:
        return ""
    f, r = summary[feat], summary[ref]
    tiles = [
        ("Precision", _mean(f["precision"]), _mean(r["precision"]), "how much of what it says is real"),
        ("Recall", _mean(f["recall"]), _mean(r["recall"]), "how many planted issues it finds"),
        ("False alarms on clean PRs", _mean(f["clean_pr"]["any_finding_rate"], pct=True),
         _mean(r["clean_pr"]["any_finding_rate"], pct=True), f"clean PRs with any finding (of {f['clean_pr']['n']})"),
        ("Cost per PR", _usd(f["cost_usd_per_pr"]), _usd(r["cost_usd_per_pr"]), "model spend, judge excluded"),
    ]
    cards = "".join(f'<div class="tile"><div class="lab">{_e(lab)}</div><div class="big">{_e(a)}</div>'
                    f'<div class="vs">{_e(_label(ref))}: <b>{_e(b)}</b></div><div class="vs">{_e(note)}</div></div>' for lab, a, b, note in tiles)
    return (f'<section id="glance"><h2>At a glance: {_e(_label(feat))} vs {_e(_label(ref))}</h2>'
            f'<p class="sub">Mean over runs on the same dataset. Details, spread and caveats below.</p>'
            f'<div class="tiles">{cards}</div></section>')


def _headline(summary: dict, arms: list[str]) -> str:
    def best(key: str) -> str | None:
        vals = {a: summary[a][key]["mean"] for a in arms if summary[a][key]}
        return max(vals, key=vals.get) if len(vals) > 1 and len(set(vals.values())) > 1 else None

    winners = {k: best(k) for k in ("precision", "recall", "f1")}
    head = ["Arm", "Precision", "Recall", "F1", "Findings / PR", "False alarms (any finding on a clean PR)",
            "False alarms (action-required on a clean PR)", "Cost / PR", "Latency p50 / p95"]
    rows = []
    for a in arms:
        s = summary[a]
        cells = []
        for key in ("precision", "recall", "f1"):
            cells.append(f'<td class="{"best" if winners[key] == a else ""}">{_e(_num(s[key]))}</td>')
        cells.append(f"<td>{_e(_num(s['findings_per_pr'], 1))}</td>")
        cells.append(f"<td>{_e(_pct(s['clean_pr']['any_finding_rate']))}</td>")
        cells.append(f"<td>{_e(_pct(s['clean_pr']['action_required_rate']))}</td>")
        cells.append(f"<td>{_e(_usd(s['cost_usd_per_pr']))}</td>")
        cells.append(f"<td>{_e(_secs(s['latency_p50_s']))} / {_e(_secs(s['latency_p95_s']))}</td>")
        rows.append(f'<tr><td><span class="swatch {_cls(arms.index(a))}"></span>{_e(_label(a))}</td>{"".join(cells)}</tr>')
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    n = max((s["n_runs"] for s in summary.values()), default=0)
    spread = "mean ± sample std over runs" if n > 1 else "a single run, so no spread is shown"
    return (f'<section id="headline"><h2>Headline numbers</h2>'
            f'<p class="sub">{_e(spread)}; precision, recall and F1 are pooled over all PRs in a run. '
            f'Bold marks the best arm.</p>'
            f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'</section>')


def _chart(summary: dict, arms: list[str]) -> str:
    groups = [(name, [summary[a][key] for a in arms])
              for name, key in (("Precision", "precision"), ("Recall", "recall"), ("F1", "f1"))]
    return ('<section id="chart"><h2>Precision, recall and F1</h2>'
            '<p class="sub">Precision: how much of what the arm says is real. Recall: how much of what was planted '
            'it finds. Whiskers show the spread over runs.</p>'
            + _legend(arms)
            + _grouped_bars(groups, arms, pct=False, aria="Precision, recall and F1 per arm")
            + "</section>")


_CONTEXT_NOTE = {"diff": "diff only", "rules": "needs the repo's review rules", "cross_file": "needs other files"}


def _context(summary: dict, results: dict, arms: list[str]) -> str:
    counts = {k: sum(i["requires"] == k for d in results["dataset"] for i in d["expected_issues"]) for k in REQUIRES}
    groups = [(f"{k.replace('_', '-')} ({counts[k]} issues)", [summary[a]["recall_by_requires"][k] for a in arms])
              for k in REQUIRES]
    head = "".join(f"<th>{_e(k.replace('_', '-'))}</th>" for k in REQUIRES)
    rows = "".join(f'<tr><td><span class="swatch {_cls(i)}"></span>{_e(_label(a))}</td>'
                   + "".join(f"<td>{_e(_pct(summary[a]['recall_by_requires'][k]))}</td>" for k in REQUIRES) + "</tr>"
                   for i, a in enumerate(arms))
    legend = "; ".join(f"<b>{_e(k.replace('_', '-'))}</b>: {_e(_CONTEXT_NOTE[k])}" for k in REQUIRES)
    return ('<section id="context"><h2>Recall by context the issue needs</h2>'
            f'<p class="sub">Each planted issue is tagged with the least context that makes it findable. {legend}.</p>'
            + _legend(arms) + _grouped_bars(groups, arms, pct=True, aria="Recall by required context per arm")
            + f'<div class="scroll"><table><thead><tr><th>Arm</th>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
            "</section>")


def _mark(matched: int, runs: int) -> str:
    if runs == 0:
        return '<span class="miss">n/a</span>'
    frac = "" if runs == 1 else f" {matched}/{runs}"
    if matched == runs:
        return f'<span class="ok">✓{frac}</span>'
    if matched == 0:
        return f'<span class="miss">✗{frac}</span>'
    return f'<span class="part">◐{frac}</span>'


def _per_pr(summary: dict, results: dict, arms: list[str]) -> str:
    head = "".join(f'<th><span class="swatch {_cls(i)}"></span>{_e(_label(a))}</th>' for i, a in enumerate(arms))
    rows = []
    for pr in results["dataset"]:
        n = len(pr["expected_issues"])
        tag = '<span class="badge">clean PR</span>' if pr["is_clean"] else f'<span class="badge">{n} expected</span>'
        cells = ""
        for a in arms:
            p = summary[a]["per_pr"][pr["id"]]
            cells += (f"<td>TP {_avg(p['tp'])} · FP {_avg(p['fp'])} · FN {_avg(p['fn'])}</td>"
                      if p["scored_runs"] else "<td>n/a</td>")
        rows.append(f'<tr class="prrow"><th>{_e(pr["id"])}: {_e(pr["title"])}{tag}</th>{cells}</tr>')
        if pr["is_clean"]:
            rows.append(f'<tr class="iss"><td colspan="{len(arms) + 1}">Nothing to find here: every finding on this PR '
                        f'counts as a false positive.</td></tr>')
        for issue in pr["expected_issues"]:
            cells = ""
            for a in arms:
                e = summary[a]["per_pr"][pr["id"]]["expected"][issue["id"]]
                cells += f"<td>{_mark(e['matched'], e['runs'])}</td>"
            rows.append(f'<tr class="iss"><td><b>{_e(issue["id"])}</b> {_e(issue["description"])}'
                        f'<div class="tags">{_e(issue["requires"].replace("_", "-"))} · {_e(issue["area"])} · '
                        f'{_e(issue["severity"])}</div></td>{cells}</tr>')
    return ('<section id="per-pr"><h2>Per PR</h2>'
            '<p class="sub">TP, FP and FN are averages over runs. ✓ = found, ✗ = missed, ◐ = found in some runs '
            '(found / runs).</p>'
            f'<div class="scroll"><table><thead><tr><th>PR / expected issue</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></section>')


def _finding_html(f: dict, *, full: bool, tag: str = "") -> str:
    where = f["file"] + (f":{f['line']}" if f.get("line") else "")
    meta = f"{f['severity']} · confidence {f['confidence']:.2f}" + (f" · {f['triage'].replace('_', ' ')}" if f.get("triage") else "")
    out = (f'<div class="f"><div class="t">{_e(f["title"])}{tag}</div>'
           f'<div class="loc">{_e(where)} · {_e(meta)}</div>')
    if full:
        out += f'<div>{_e(f["explanation"])}</div>'
        if f.get("evidence"):
            out += f'<div class="loc">Evidence</div><pre>{_e(f["evidence"])}</pre>'
        if f.get("verifier_reason"):
            out += f'<div class="loc">Verifier ({_e(f.get("verdict", ""))})</div><div>{_e(f["verifier_reason"])}</div>'
    return out + "</div>"


def _scored(rec: dict) -> list[dict]:
    """The findings the judge saw, in the order `finding_index` refers to."""
    return [f for f in rec["findings"] if f.get("triage") != "dropped"]


def _pick_examples(results: dict, summary: dict, feat: str, ref: str | None) -> list[dict]:
    """Up to two (issue, pipeline finding, baseline record) triples, chosen by rule, not by hand:
    issues the featured arm found, ranked by how rarely the reference found them, then how reliably the
    featured arm did. A different PR for each example when possible."""
    recs = [r for r in results["records"] if r["status"] == "ok"]
    issues = {i["id"]: (pr, i) for pr in results["dataset"] for i in pr["expected_issues"]}
    candidates = []
    for eid, (pr, issue) in issues.items():
        fp = summary[feat]["per_pr"][pr["id"]]["expected"][eid]
        if fp["matched"] == 0:
            continue
        rp = summary[ref]["per_pr"][pr["id"]]["expected"][eid] if ref else {"matched": 0, "runs": 0}
        rate = lambda e: e["matched"] / e["runs"] if e["runs"] else 0  # noqa: E731
        candidates.append((rate(rp), -rate(fp), eid, pr, issue))
    candidates.sort(key=lambda c: c[:3])
    chosen, seen_prs = [], set()
    for c in candidates:  # first pass: distinct PRs; then fill up
        if c[3]["id"] not in seen_prs:
            chosen.append(c)
            seen_prs.add(c[3]["id"])
    chosen = (chosen + [c for c in candidates if c not in chosen])[:2]

    out = []
    for _, _, eid, pr, issue in chosen:
        mine = sorted((r for r in recs if r["arm"] == feat and r["pr"] == pr["id"]), key=lambda r: r["run"])
        rec = next(r for r in mine if eid in r["judge"]["matched_expected"])
        match = next(m for m in rec["judge"]["matches"] if m["expected_id"] == eid)
        found = _scored(rec)[match["finding_index"]]
        ref_recs = sorted((r for r in recs if r["arm"] == ref and r["pr"] == pr["id"]), key=lambda r: r["run"]) if ref else []
        ref_rec = next((r for r in ref_recs if r["run"] == rec["run"]), ref_recs[0] if ref_recs else None)
        out.append({"pr": pr, "issue": issue, "finding": found, "rec": rec, "ref_rec": ref_rec})
    return out


def _examples(summary: dict, results: dict, arms: list[str]) -> str:
    feat, ref = _featured(arms)
    intro = '<section id="examples"><h2>Example findings</h2>'
    if feat is None:
        return intro + ('<p class="sub">There is no pipeline arm in this results file, so there are no pipeline '
                        'examples to show.</p></section>')
    picks = _pick_examples(results, summary, feat, ref)
    if not picks:
        return intro + (f'<p class="sub">The {_e(feat)} arm matched no expected issues in this results file, so there '
                        f'are no examples to show.</p></section>')
    n_ex = len(picks)
    how = (f"Chosen by rule, not by hand: planted issues the {_e(_label(feat))} found that the "
           f"{_e(_label(ref))} found least often" if ref else f"Planted issues the {_e(_label(feat))} found")
    cards = []
    for p in picks:
        pr, issue, rec = p["pr"], p["issue"], p["rec"]
        fe = summary[feat]["per_pr"][pr["id"]]["expected"][issue["id"]]
        left = f'<div class="card"><h4>{_e(_label(feat))} (run {rec["run"] + 1})</h4>{_finding_html(p["finding"], full=True)}</div>'
        right = ""
        if ref and p["ref_rec"]:
            rr = p["ref_rec"]
            ref_matched = {m["finding_index"] for m in rr["judge"]["matches"] if m["expected_id"] == issue["id"]}
            findings = _scored(rr)
            body = "".join(_finding_html(f, full=False, tag=' <span class="ok">✓ same problem</span>' if i in ref_matched else "")
                           for i, f in enumerate(findings))
            re_ = summary[ref]["per_pr"][pr["id"]]["expected"][issue["id"]]
            verdict = (f'<p class="ok">Found this issue.</p>' if ref_matched else
                       f'<p class="miss">Did not find this issue in this run'
                       f' ({re_["matched"]} of {re_["runs"]} runs found it).</p>')
            if not findings:
                body = "<p>Reported nothing on this PR.</p>"
            right = f'<div class="card"><h4>{_e(_label(ref))} (run {rr["run"] + 1}), same PR</h4>{verdict}{body}</div>'
        cards.append(
            f'<div class="example"><h3>{_e(pr["id"])}: {_e(pr["title"])}</h3>'
            f'<div class="sub"><b>Planted issue {_e(issue["id"])}</b> '
            f'({_e(issue["requires"].replace("_", "-"))}): {_e(issue["description"])} '
            f'{_e(_label(feat))} found it in {fe["matched"]} of {fe["runs"]} runs.</div>'
            f'<div class="cols">{left}{right}</div></div>')
    return (intro + f'<p class="sub">{how}. {_plural(n_ex, "example")}.</p><div class="examples">{"".join(cards)}</div>'
            "</section>")


def _method(results: dict, summary: dict, arms: list[str]) -> str:
    cfg, ds = results["config"], results["dataset"]
    models = cfg["models"]
    n_clean = sum(d["is_clean"] for d in ds)
    n_issues = sum(len(d["expected_issues"]) for d in ds)
    agreement = results.get("judge_agreement")
    if agreement:
        bad = [c["id"] for c in agreement["cases"] if not c["agrees"]]
        rows = "".join(f"<tr><td>{_e(c['id'])}</td><td>{'match' if c['should_match'] else 'no match'}</td>"
                       f"<td>{'match' if c['matched'] else 'no match'}</td><td>{'yes' if c['agrees'] else 'NO'}</td></tr>"
                       for c in agreement["cases"])
        cases = ("<details><summary>The judge's test cases</summary><div class='scroll'><table><thead><tr>"
                 "<th>Case</th><th>Hand label</th><th>Judge says</th><th>Agrees</th></tr></thead>"
                 f"<tbody>{rows}</tbody></table></div></details>")
        judge_line = (f"Judge agreement: the judge ({_e(agreement['judge_model'])}) agrees with the hand-labeled answer on "
                      f"{agreement['agree']} of {agreement['n']} test cases"
                      + (f"; it disagrees on {_e(', '.join(bad))}." if bad else ".") + cases)
    else:
        judge_line = "Judge agreement check: not run for this results file, so the judge is unvalidated here."
    same_model = models.get("judge") == models.get("verifier")
    d8 = ("The judge and the verifier are the <b>same model</b> in this run, so the judge may favour findings its own "
          "model already approved."
          if same_model else
          f"The judge ({_e(models.get('judge'))}) is a different model from the verifier ({_e(models.get('verifier'))}) "
          "and from the specialists, so it should not favour either arm (D8). It is still a model and can be wrong; "
          "the agreement check above is the evidence for how often.")
    totals = results.get("totals", {})
    errs = [(a, s["errors"]) for a, s in summary.items() if any(s["errors"].values())]
    err_line = "".join(f"<li class='warn'>{_e(a)}: {_plural(e['review'], 'review error')} and "
                       f"{_plural(e['judge'], 'judge error')} left out of the metrics.</li>" for a, e in errs)
    return (
        '<section id="method"><h2>Methodology and limitations</h2><ul>'
        f"<li><b>Dataset:</b> {len(ds)} PRs ({n_clean} clean) with {n_issues} expected issues, each reviewed against "
        "a real git repo. Issues were planted on purpose; clean PRs have none, so any finding there is a false alarm.</li>"
        f"<li><b>Runs:</b> {_plural(cfg['runs'], 'run')} per arm, cache off; arms: {_e(', '.join(arms))}. "
        "Precision, recall and F1 are pooled over PRs in each run, then averaged over runs "
        "(sample std; with a handful of runs treat it as a rough indication).</li>"
        f"<li><b>Models:</b> specialists {_e(models.get('specialist'))}, baseline {_e(models.get('baseline'))}, "
        f"verifier {_e(models.get('verifier'))}, judge {_e(models.get('judge'))}.</li>"
        "<li><b>Scoring:</b> the judge decides only whether a finding describes the same problem as an expected "
        "issue; code checks the location and assigns matches one-to-one. A finding the pipeline dropped is not "
        "scored.</li>"
        f"<li>{judge_line}</li>"
        f"<li><b>Spend this run:</b> arms ${totals.get('review_cost_usd', 0):.2f}, judge "
        f"${totals.get('judge_cost_usd', 0):.2f}, {totals.get('wall_s', 0):.0f} s wall clock.</li>"
        f"{err_line}</ul>"
        "<h3>Limitations</h3><ul>"
        "<li><b>Synthetic data.</b> The PRs are written, not collected from real projects. They are probably tidier "
        "and more clearly wrong than real bugs.</li>"
        "<li><b>Single repo.</b> Everything is one small Python service. Results may not carry over to other "
        "languages, sizes or codebases.</li>"
        "<li><b>Same author for the dataset and the checklists (D6).</b> The same author wrote the planted issues and "
        "the specialists' checklists, so both arms are favoured. The baseline therefore gets the same checklist, "
        "which keeps the comparison fair on checklists but cannot remove that shared bias.</li>"
        f"<li><b>Judge model choice (D8).</b> {d8}</li>"
        f"<li><b>Small sample.</b> {n_issues} expected issues and {_plural(len(ds), 'PR')}: one issue moves recall by "
        f"{(100 / n_issues if n_issues else 0):.0f} points. Model output also varies between runs.</li>"
        "</ul>"
        f"<p class=\"note\">Generated {_e(results.get('created_at', ''))} from results.json; rebuild with "
        "<code>python -m evals.scorecard --from DIR</code>.</p></section>")


def render_html(results: dict) -> str:
    summary = summarize(results)
    arms = results["config"]["arms"]
    warnings = "".join(
        f"<p class='warn'>{_e(arm)}: {_plural(s['errors']['review'], 'review error')} and "
        f"{_plural(s['errors']['judge'], 'judge error')} are left out of the numbers below.</p>"
        for arm, s in summary.items() if any(s["errors"].values()))
    body = "".join([
        f'<h1>Review eval scorecard</h1><p class="sub">{len(arms)} arms · {_plural(results["config"]["runs"], "run")} · '
        f'{_plural(len(results["dataset"]), "PR")} ({sum(d["is_clean"] for d in results["dataset"])} clean) · '
        f'{_e(results["created_at"][:10])}</p>{warnings}',
        _glance(summary, arms), _headline(summary, arms), _chart(summary, arms),
        _context(summary, results, arms), _per_pr(summary, results, arms),
        _examples(summary, results, arms), _method(results, summary, arms),
    ])
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Review eval scorecard</title><style>{_CSS}</style></head><body><main>{body}</main></body></html>\n")


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
