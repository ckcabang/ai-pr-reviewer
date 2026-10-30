"""The full scorecard: `render_html(results)` over a multi-arm fixture results file (no provider calls).

The fixture is built in code with the shape documented on `evals.run_eval.build_results`:
three dataset PRs (prA with a diff-level and a cross-file issue, prB with a rules issue, prC clean) and
up to four arms with scripted behaviour. Only raw records are needed: the page re-derives every metric.
"""
import re

import pytest

from evals.scorecard import render_html

EXPECTED = {
    "prA": [("prA-1", "diff", "correctness", "Discount applied twice in the cart total"),
            ("prA-2", "cross_file", "correctness", "Caller still passes dollars to a function that now takes cents")],
    "prB": [("prB-1", "rules", "standards", "New endpoint skips the required audit log call (R4)")],
    "prC": [],
}
TITLES = {"prA": "Add cart discounts", "prB": "Add export endpoint", "prC": "Rename helper"}

# arm -> run -> pr -> [(finding title, expected id it matches or None)]
BASELINE = {0: {"prA": [("Discount double counted", "prA-1")], "prB": [("Missing docstring", None)],
                "prC": [("Looks risky <script>alert(1)</script>", None)]},
            1: {"prA": [("Discount double counted", "prA-1")], "prB": [], "prC": []}}
PIPELINE = {0: {"prA": [("Unit mismatch between caller and callee", "prA-2"), ("Discount double counted", "prA-1")],
                "prB": [("Audit log call missing on export", "prB-1")], "prC": []},
            1: {"prA": [("Discount double counted", "prA-1")], "prB": [("Audit log call missing on export", "prB-1")],
                "prC": []}}
ARM_PLANS = {"baseline": BASELINE, "pipeline": PIPELINE, "pipeline_no_verifier": PIPELINE,
             "pipeline_no_context": BASELINE}


def finding(arm, pr, n, title):
    return {"file": "app/x.py", "line": 10 + n, "end_line": None, "category": "logic", "severity": "high",
            "confidence": 0.9, "title": title, "explanation": f"{title}: because of the surrounding code.",
            "evidence": f"EVIDENCE for {title}", "suggestion": "fix it", "rule_id": None,
            "id": f"{arm}-{pr}-{n}", "reviewer": arm, "verdict": "keep",
            "verifier_reason": f"Verifier confirmed {title}", "duplicate_of": None,
            "triage": "action_required" if n == 0 else "review_recommended"}


def record(arm, run, pr, plan):
    items = plan.get(run, {}).get(pr, [])
    findings = [finding(arm, pr, i, t) for i, (t, _) in enumerate(items)]
    matches = [{"expected_id": exp, "finding_id": findings[i]["id"], "finding_index": i, "score": 0.95,
                "reason": "same problem"} for i, (_, exp) in enumerate(items) if exp]
    matched = sorted(m["expected_id"] for m in matches)
    all_ids = [e[0] for e in EXPECTED[pr]]
    return {"arm": arm, "run": run, "pr": pr, "status": "ok", "wall_s": 6.0 + run + (3 if arm != "baseline" else 0),
            "findings": findings, "scored_finding_ids": [f["id"] for f in findings],
            "stats": {"totals": {"input_tokens": 2000, "output_tokens": 500,
                                 "cost_usd": 0.004 if arm == "baseline" else 0.03}},
            "judge": {"candidates": [], "matches": matches, "tp": len(matches), "fp": len(items) - len(matches),
                      "fn": len(all_ids) - len(matched), "matched_expected": matched,
                      "unmatched_expected": [i for i in all_ids if i not in matched],
                      "unmatched_findings": [f["id"] for f, (_, e) in zip(findings, items) if not e],
                      "usage": {}, "cost_usd": 0.002}}


def make_results(arms=("baseline", "pipeline"), runs=2, agreement=True):
    dataset = [{"id": pr, "title": TITLES[pr], "description": "d", "is_clean": not issues,
                "expected_issues": [{"id": i, "file": "app/x.py", "line_hint": 10, "area": area, "severity": "high",
                                     "requires": req, "rule_id": None, "description": desc, "related": []}
                                    for i, req, area, desc in issues]}
               for pr, issues in EXPECTED.items()]
    records = [record(a, run, pr, ARM_PLANS[a]) for a in arms for run in range(runs) for pr in EXPECTED]
    return {"schema_version": 1, "created_at": "2026-10-30T03:52:36+00:00", "out_dir": "x",
            "config": {"arms": list(arms), "runs": runs, "pr_ids": list(EXPECTED), "workers": 4, "cache": "off",
                       "models": {"specialist": "spec-model", "verifier": "verif-model", "baseline": "spec-model",
                                  "judge": "judge-model", "embedding": "emb"},
                       "pipeline": {}},
            "totals": {"review_cost_usd": 0.5, "judge_cost_usd": 0.1, "wall_s": 90.0},
            "dataset": dataset, "records": records,
            "judge_agreement": {"n": 6, "agree": 5, "agreement": 5 / 6, "judge_model": "judge-model", "cost_usd": 0.01,
                                "cases": [{"id": "case-x", "should_match": True, "matched": False, "score": 0.2,
                                           "agrees": False, "note": "n", "candidate_status": []}]}
            if agreement else None}


def section(html: str, section_id: str) -> str:
    m = re.search(rf"<section id=\"{section_id}\".*?</section>", html, re.S)
    assert m, f"section {section_id} missing"
    return m.group(0)


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


SECTIONS = ["headline", "chart", "context", "area", "per-pr", "examples", "method"]


@pytest.mark.parametrize("arms", [("baseline", "pipeline"),
                                  ("baseline", "pipeline", "pipeline_no_verifier"),
                                  ("baseline", "pipeline", "pipeline_no_verifier", "pipeline_no_context")])
def test_every_section_renders_for_two_to_four_arms(arms):
    html = render_html(make_results(arms))
    for sid in SECTIONS:
        body = section(html, sid)
        for arm in arms if sid in ("headline", "chart", "context", "area", "per-pr") else ():
            assert arm in body, f"{arm} missing from {sid}"
    assert html.count("<svg") >= 3  # P/R/F1, recall-by-context and recall-by-area charts


def test_page_is_self_contained():
    html = render_html(make_results(("baseline", "pipeline", "pipeline_no_verifier", "pipeline_no_context")))
    assert not re.search(r"https?://|//cdn|<script|<link|@import|url\(|<img|<iframe", html)


def test_headline_table_shows_the_numbers_a_manager_asks_for():
    body = section(render_html(make_results()), "headline")
    for header in ("Precision", "Recall", "F1", "Findings / PR", "False alarms", "Cost / PR", "Latency"):
        assert header in body
    t = text(body)
    # baseline: run0 tp=1 fp=3 fn=2 -> P .25 R .33; run1 tp=1 fp=0 fn=2 -> P 1.0 R .33: mean .62, sample std .53
    assert "0.67 ± 0.47" in t
    assert "$0.0040" in t and "$0.0300" in t
    assert "50%" in t  # baseline: 1 of 2 runs put a finding on the clean PR (a "false alarm")
    assert "6.0 s" in t or "6.5 s" in t


def test_chart_has_bars_per_metric_and_arm_with_labels():
    chart = section(render_html(make_results()), "chart")
    assert chart.count("<rect") >= 6  # 3 metrics x 2 arms
    assert "<title>" in chart  # tooltips / accessible names
    assert all(label in text(chart) for label in ("Precision", "Recall", "F1"))


def test_recall_by_context_covers_every_required_context_and_arm():
    body = text(section(render_html(make_results()), "context"))
    for ctx in ("diff", "rules", "cross_file"):
        assert ctx in body or ctx.replace("_", "-") in body or ctx.replace("_", " ") in body
    assert "100%" in body and "0%" in body  # pipeline gets cross-file in 1 of 2 runs, baseline never


def test_recall_by_area_covers_every_area_and_arm_with_issue_counts():
    body = section(render_html(make_results()), "area")
    t = text(body)
    for area, n in (("correctness", 2), ("security", 0), ("standards", 1)):
        assert f"{area} ({n} issues)" in t
    # standards (prB-1): baseline never finds it, pipeline finds it in both runs; security has no issues
    assert "100%" in t and "0%" in t and "n/a" in t
    assert body.count("<svg") == 1 and "baseline" in t and "pipeline" in t


def test_per_pr_table_marks_each_expected_issue_found_or_missed_per_arm():
    body = section(render_html(make_results()), "per-pr")
    for pr in TITLES.values():
        assert pr in body
    assert "prA-1" in body and "prA-2" in body and "prB-1" in body
    assert "✓" in body and "✗" in body
    assert "1/2" in body  # pipeline found prA-2 in one of two runs
    assert "TP" in body and "FP" in body and "FN" in body


def test_examples_put_a_pipeline_finding_next_to_what_the_baseline_said():
    body = section(render_html(make_results()), "examples")
    t = text(body)
    # chosen because the baseline never found them: prA-2 first, prB-1 second
    assert "Unit mismatch between caller and callee" in t and "Audit log call missing on export" in t
    assert "EVIDENCE for Unit mismatch" in t and "Verifier confirmed" in t
    assert "Missing docstring" in t  # what the baseline said on prB
    assert "Did not find this issue" in t  # the baseline never found prA-2 or prB-1
    assert body.count("class=\"example\"") == 2


def test_examples_degrade_when_no_pipeline_arm_was_run():
    html = render_html(make_results(("baseline",)))
    assert "no pipeline" in text(section(html, "examples")).lower()
    for sid in SECTIONS:
        section(html, sid)


def test_examples_degrade_when_the_pipeline_matched_nothing():
    results = make_results()
    for r in results["records"]:
        if r["arm"] == "pipeline":
            r["judge"].update(matches=[], matched_expected=[], tp=0)
    assert "matched no expected" in text(section(render_html(results), "examples")).lower()


def test_methodology_note_states_scale_models_judge_agreement_and_limitations():
    t = text(section(render_html(make_results()), "method"))
    assert "3 PRs" in t and "1 clean" in t and "3 expected issues" in t
    assert "2 runs" in t
    assert "spec-model" in t and "verif-model" in t and "judge-model" in t
    assert "5 of 6" in t
    low = t.lower()
    assert "synthetic" in low and "single repo" in low or "one repo" in low
    assert "same author" in low and "checklist" in low  # D6
    assert "different model" in low  # D8


def test_methodology_without_judge_agreement_says_so():
    t = text(section(render_html(make_results(agreement=False)), "method"))
    assert "not run" in t.lower()


def test_single_run_has_no_std_and_undefined_metrics_say_na():
    results = make_results(runs=1)
    for r in results["records"]:
        if r["arm"] == "baseline":
            r["findings"], r["scored_finding_ids"] = [], []
            r["judge"].update(tp=0, fp=0, fn=len(EXPECTED[r["pr"]]), matches=[], matched_expected=[])
    html = render_html(results)
    assert "±" not in text(section(html, "headline"))
    assert "n/a" in text(section(html, "headline"))  # precision with no findings


def test_errors_are_called_out_not_hidden():
    results = make_results()
    results["records"][0] = {"arm": "baseline", "run": 0, "pr": "prA", "status": "error", "error": "boom", "wall_s": 1}
    t = text(render_html(results))
    assert "1 review error" in t


def test_text_from_models_is_escaped():
    results = make_results()
    for r in results["records"]:
        for f in r["findings"]:
            f["title"] = f["title"] + " <script>alert(1)</script>"
            f["evidence"] = "<img src=x onerror=alert(1)>"
    html = render_html(results)
    assert "<script>alert(1)" not in html and "<img" not in html
    assert "&lt;script&gt;alert(1)" in html


def test_works_with_the_dark_scheme_and_narrow_screens():
    html = render_html(make_results())
    assert "prefers-color-scheme: dark" in html and "viewport" in html
