"""Seam 2 for the pipeline arm (#12): `evaluate` with `--arms baseline,pipeline` over real dataset PRs
and the fake provider. What is scored, how false alarms are counted, and how failures show up."""
import json
import re

import pytest

from evals.arms import ARMS
from evals.dataset import load_pr
from evals.run_eval import evaluate
from evals.scorecard import rebuild
from reviewer.diff import parse_diff
from reviewer.fake import FakeProvider

PR09, PR06 = load_pr("pr09"), load_pr("pr06")
ANCHOR = ("app/services/pricing.py", 54)  # pr09's expected issue (its labels.yaml line_hint)


def commentable(pr):
    return [(f.path, n) for f in parse_diff(pr.patch) for n in f.commentable_lines]


def draft(where, title, *, severity="high", confidence=0.9, category="logic"):
    file, line = where
    return {"file": file, "line": line, "category": category, "severity": severity, "confidence": confidence,
            "title": title, "explanation": f"{title} explanation", "evidence": "x = 1"}


def far_from(pr, anchor, by=10):
    return next(w for w in commentable(pr) if w[0] != anchor[0] or abs(w[1] - anchor[1]) > by)


HIT = draft(ANCHOR, "Totals now returned in cents")
NOTE = draft(far_from(PR09, ANCHOR), "Rounding differs from the old helper", severity="medium", confidence=0.8,
             category="state")
WRONG = draft(far_from(PR09, ANCHOR, by=40), "Looks risky but a guard covers it", category="error_handling")
ALARM_MEDIUM = draft(commentable(PR06)[0], "Rename looks risky", severity="medium", confidence=0.8)
ALARM_HIGH = draft(commentable(PR06)[0], "Rename breaks callers", severity="high", confidence=0.9)


def judge_script(call):
    findings = json.loads(re.search(r"<findings>\n(.*?)\n</findings>", call.user, re.S).group(1))
    return {"matches": [{"expected_id": "pr09-1", "finding_id": f["id"], "score": 0.9, "reason": "same problem"}
                        for f in findings if "cents" in f["title"]]}


def verifier_script(call):
    """Keeps every finding as proposed, except those whose title says a guard covers them."""
    blocks = re.findall(r'<finding id="([^"]+)">\n(?:.*\n)*?severity: (\w+)\nconfidence: ([\d.]+)\n'
                        r'title: (.+)\n', call.user)
    return {"verdicts": [
        {"id": i, "verdict": "drop" if "guard covers" in title else "keep", "confidence": float(c),
         "severity": s, "reason": "a guard covers it" if "guard covers" in title else "real"}
        for i, s, c, title in blocks]}


def provider_for(pr09=(HIT,), pr06=(), *, correctness=None, **extra):
    """Specialists answer by PR (title in the prompt); the baseline says nothing. Security and
    standards find nothing unless given."""
    runs: dict[str, int] = {}

    def correctness_reply(call):
        pr, found = (PR09, pr09) if PR09.title in call.user else (PR06, pr06)
        n = runs[pr.id] = runs.get(pr.id, 0) + 1
        found = found[n - 1] if found and isinstance(found[0], (list, tuple)) else found
        return {"findings": list(found)}

    return FakeProvider({
        "baseline": {"findings": []},
        "correctness": correctness or correctness_reply,
        "security": {"findings": []},
        "standards": {"findings": []},
        "verifier": verifier_script,
        "judge": judge_script,
        **extra,
    })


def run(provider, config, out_dir, **kw):
    kw.setdefault("arms", ["baseline", "pipeline"])
    kw.setdefault("runs", 1)
    kw.setdefault("pr_ids", ["pr06", "pr09"])
    return evaluate(provider=provider, config=config, out_dir=out_dir, **kw)


@pytest.fixture
def out_dir(tmp_path):
    return tmp_path / "out"


def rec(results, arm, pr, run=0):
    return next(r for r in results["records"] if (r["arm"], r["pr"], r["run"]) == (arm, pr, run))


def test_pipeline_is_a_registered_arm_and_scores_both_tiers_but_not_dropped_findings(config, out_dir):
    assert "pipeline" in ARMS and "baseline" in ARMS
    provider = provider_for(pr09=(HIT, NOTE, WRONG))

    results = run(provider, config, out_dir)

    r = rec(results, "pipeline", "pr09")
    tiers = {f["title"]: f["triage"] for f in r["findings"]}
    assert tiers == {HIT["title"]: "action_required", NOTE["title"]: "review_recommended",
                     WRONG["title"]: "dropped"}                 # all kept in the file for debugging
    scored = [f["title"] for f in r["findings"] if f["id"] in r["scored_finding_ids"]]
    assert sorted(scored) == sorted([HIT["title"], NOTE["title"]])  # both tiers, never the dropped one
    assert (r["judge"]["tp"], r["judge"]["fp"], r["judge"]["fn"]) == (1, 1, 0)
    assert r["status"] == "ok" and r["stats"]["stages"]["verifier"]["status"] == "ok"
    s = results["summary"]["pipeline"]
    assert (s["precision"]["mean"], s["recall"]["mean"]) == (pytest.approx(0.5), pytest.approx(1.0))
    assert results["config"]["arms"] == ["baseline", "pipeline"]
    # the pipeline arm runs the same specialists and verifier that the CLI does
    assert provider.calls_for("verifier") and provider.calls_for("security") and provider.calls_for("standards")
    assert s["cost_usd_per_pr"]["mean"] > results["summary"]["baseline"]["cost_usd_per_pr"]["mean"]


def test_false_alarms_on_a_clean_pr_are_reported_for_any_finding_and_for_action_required_only(config, out_dir):
    provider = provider_for(pr06=([ALARM_MEDIUM], [ALARM_HIGH], []), pr09=((HIT,),) * 3)

    results = run(provider, config, out_dir, runs=3)

    s = results["summary"]["pipeline"]["clean_pr"]
    assert s["n"] == 1
    assert s["any_finding_rate"]["mean"] == pytest.approx(2 / 3)          # medium and high alarms
    assert s["action_required_rate"]["mean"] == pytest.approx(1 / 3)      # only the high one
    base = results["summary"]["baseline"]["clean_pr"]
    assert base["any_finding_rate"]["mean"] == 0.0 and base["action_required_rate"]["mean"] == 0.0


def test_a_dropped_finding_on_a_clean_pr_is_not_a_false_alarm(config, out_dir):
    guard = draft(commentable(PR06)[0], "Looks risky but a guard covers it")
    results = run(provider_for(pr06=([guard],)), config, out_dir)

    r = rec(results, "pipeline", "pr06")
    assert [f["triage"] for f in r["findings"]] == ["dropped"] and r["scored_finding_ids"] == []
    clean = results["summary"]["pipeline"]["clean_pr"]
    assert clean["any_finding_rate"]["mean"] == 0.0 and clean["action_required_rate"]["mean"] == 0.0


def test_the_pipeline_erroring_on_a_pr_is_visible_not_scored_as_clean(config, out_dir):
    def correctness(call):
        return RuntimeError("provider down") if PR06.title in call.user else {"findings": [HIT]}

    provider = provider_for(correctness=correctness, security=RuntimeError("down"), standards=RuntimeError("down"))

    results = run(provider, config, out_dir)

    bad = rec(results, "pipeline", "pr06")
    assert bad["status"] == "error" and "all specialists failed" in bad["error"] and "judge" not in bad
    s = results["summary"]["pipeline"]
    assert s["errors"] == {"review": 1, "judge": 0}
    assert s["clean_pr"]["n"] == 0 and s["clean_pr"]["any_finding_rate"] is None
    assert results["summary"]["baseline"]["errors"] == {"review": 0, "judge": 0}
    html = (out_dir / "scorecard.html").read_text()
    assert "1 review error" in html


def test_a_specialist_failing_is_kept_in_the_record_counted_in_the_summary_and_shown_in_the_scorecard(
        config, out_dir):
    provider = provider_for(security=RuntimeError("security model timed out"))

    results = run(provider, config, out_dir, runs=2)

    r = rec(results, "pipeline", "pr09")
    assert r["status"] == "ok"                                              # the other specialists still scored
    assert r["stats"]["specialists"]["security"]["status"] == "failed"
    assert "timed out" in r["stats"]["specialist_failures"]["security"]
    s = results["summary"]["pipeline"]
    assert s["degraded"]["specialists"] == {"security": 4}                  # 2 runs x 2 PRs
    assert s["degraded"]["verifier"] == 0 and s["degraded"]["reviews"] == 4
    assert s["recall"]["mean"] == 1.0                                       # still in the metrics, not hidden
    assert results["summary"]["baseline"]["degraded"] == {"specialists": {}, "verifier": 0, "reviews": 0}
    html = (out_dir / "scorecard.html").read_text()
    assert "pipeline: 4 reviews ran with a stage down (security failed in 4 reviews" in html
    assert "baseline: " not in re.sub(r"<[^>]+>", " ", html.split("<section")[0])  # no warning for the healthy arm


def test_a_verifier_failure_fails_open_and_is_flagged(config, out_dir):
    provider = provider_for(verifier=RuntimeError("verifier down"))

    results = run(provider, config, out_dir)

    s = results["summary"]["pipeline"]
    assert s["degraded"]["verifier"] == 1                                   # only pr09 has findings to verify
    assert rec(results, "pipeline", "pr09")["stats"]["stages"]["verifier"]["status"] == "failed"
    assert ("pipeline: 1 review ran with a stage down (the verifier failed in 1 review"
            in (out_dir / "scorecard.html").read_text())


def test_the_scorecard_features_the_pipeline_against_the_baseline(config, out_dir):
    results = run(provider_for(pr09=(HIT, NOTE)), config, out_dir)

    html = rebuild(out_dir).read_text()
    assert 'id="glance"' in html and "pipeline" in html and "baseline" in html
    assert results["summary"]["pipeline"]["f1"] is not None


def test_the_command_line_summary_warns_about_failed_stages(config, tmp_path, capsys):
    from evals.run_eval import main

    code = main(["--arms", "baseline,pipeline", "--runs", "1", "--out", str(tmp_path / "res"), "--prs", "pr09",
                 "--skip-judge-check"], provider=provider_for(security=RuntimeError("down")))

    shown = capsys.readouterr().out
    assert code == 0 and "pipeline: precision" in shown
    assert "WARNING: pipeline: 1 review ran with a stage down (security failed in 1 review" in shown
