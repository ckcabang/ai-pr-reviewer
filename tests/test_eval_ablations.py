"""Seam 2 for the ablation arms (#14): `evaluate` with `pipeline_no_verifier` / `pipeline_no_context`
next to `pipeline`, on real dataset PRs and the fake provider (no network)."""
import json
import re

from evals.arms import ARMS
from evals.dataset import load_pr
from evals.run_eval import evaluate, main
from evals.scorecard import rebuild
from reviewer.diff import parse_diff
from reviewer.fake import FakeProvider

PR09 = load_pr("pr09")
FAR = next((f.path, n) for f in parse_diff(PR09.patch) for n in f.commentable_lines
           if f.path != "app/services/pricing.py" or abs(n - 54) > 10)
HIT = {"file": "app/services/pricing.py", "line": 54, "category": "logic", "severity": "high", "confidence": 0.9,
       "title": "Totals now returned in cents", "explanation": "callers expect dollars", "evidence": "x = 1"}
# Would be dropped by the verifier, so it is the visible difference between the two arms.
GUARDED = {**HIT, "file": FAR[0], "line": FAR[1], "title": "Looks risky but a guard covers it", "severity": "medium",
           "confidence": 0.8, "category": "error_handling"}
ALL = ["pipeline", "pipeline_no_verifier", "pipeline_no_context"]
TAGS = ("<context>", "<team_rules>", "<do_not_flag>")


def verifier_script(call):
    blocks = re.findall(r'<finding id="([^"]+)">\n(?:.*\n)*?severity: (\w+)\nconfidence: ([\d.]+)\n'
                        r'title: (.+)\n', call.user)
    return {"verdicts": [
        {"id": i, "verdict": "drop" if "guard covers" in t else "keep", "confidence": float(c), "severity": s,
         "reason": "r"} for i, s, c, t in blocks]}


def judge_script(call):
    findings = json.loads(re.search(r"<findings>\n(.*?)\n</findings>", call.user, re.S).group(1))
    return {"matches": [{"expected_id": "pr09-1", "finding_id": f["id"], "score": 0.9, "reason": "same"}
                        for f in findings if "cents" in f["title"]]}


def provider(found=(HIT, GUARDED)):
    return FakeProvider({"baseline": {"findings": []}, "correctness": {"findings": list(found)},
                         "security": {"findings": []}, "standards": {"findings": []},
                         "verifier": verifier_script, "judge": judge_script})


def run(prov, config, out_dir, arms=ALL, **kw):
    return evaluate(arms=arms, runs=1, pr_ids=["pr09"], provider=prov, config=config, out_dir=out_dir, **kw)


def rec(results, arm):
    return next(r for r in results["records"] if r["arm"] == arm)


def run_one(config, arm, tmp_path):
    prov = provider()
    results = run(prov, config, tmp_path / "o", arms=[arm])
    return prov, results


def test_the_ablations_are_registered_with_descriptions():
    for name in ("pipeline_no_verifier", "pipeline_no_context"):
        assert name in ARMS and ARMS[name].description


def test_no_verifier_makes_no_verifier_call_and_keeps_every_finding_at_its_own_confidence(config, tmp_path):
    prov, results = run_one(config, "pipeline_no_verifier", tmp_path)

    assert prov.calls_for("verifier") == []
    r = rec(results, "pipeline_no_verifier")
    assert r["stats"]["stages"]["verifier"]["status"] == "skipped"
    by_title = {f["title"]: f for f in r["findings"]}
    assert set(by_title) == {HIT["title"], GUARDED["title"]}
    assert by_title[GUARDED["title"]]["triage"] != "dropped"       # the verifier would have dropped it
    assert by_title[GUARDED["title"]]["confidence"] == 0.8
    assert results["summary"]["pipeline_no_verifier"]["degraded"]["verifier"] == 0   # skipped on purpose


def test_no_verifier_still_gets_context_and_all_three_specialists(config, tmp_path):
    prov, _ = run_one(config, "pipeline_no_verifier", tmp_path)
    for role in ("correctness", "security", "standards"):
        assert prov.calls_for(role), role
    assert "<context>" in prov.calls_for("correctness")[0].user


def test_no_context_sends_no_bundle_and_no_rules_to_the_specialists_and_nothing_to_the_verifier(config, tmp_path):
    prov, _ = run_one(config, "pipeline_no_context", tmp_path)

    spec_calls = [c for role in ("correctness", "security", "standards") for c in prov.calls_for(role)]
    assert spec_calls                                    # specialists still run (standards has no rules: skipped)
    for c in spec_calls:
        for tag in TAGS:
            assert tag not in c.user, (c.role, tag)
        assert "Context you are given" not in c.system
    verifier_calls = prov.calls_for("verifier")
    assert verifier_calls                                # the verifier still runs, on the diff and source window
    for c in verifier_calls:
        for tag in TAGS:
            assert tag not in c.user, tag
    assert prov.embed_calls == []                        # no semantic search either


def test_full_pipeline_does_send_context_and_rules_so_the_contrast_is_real(config, tmp_path):
    prov, _ = run_one(config, "pipeline", tmp_path)
    assert "<context>" in prov.calls_for("correctness")[0].user
    assert "<team_rules>" in prov.calls_for("correctness")[0].user
    assert "<team_rules>" in prov.calls_for("verifier")[0].user
    assert "<context>" in prov.calls_for("verifier")[0].user


def test_no_context_keeps_the_verifier_and_triage(config, tmp_path):
    _, results = run_one(config, "pipeline_no_context", tmp_path)
    r = rec(results, "pipeline_no_context")
    assert r["status"] == "ok" and r["stats"]["context"] == {"enabled": False}
    assert r["stats"]["stages"]["verifier"]["status"] == "ok"
    tiers = {f["title"]: f["triage"] for f in r["findings"]}
    assert tiers[GUARDED["title"]] == "dropped" and tiers[HIT["title"]] == "action_required"


def test_an_ablation_does_not_leak_into_the_next_arm_or_the_callers_config(config, tmp_path):
    results = run(provider(), config, tmp_path / "o")

    assert config.context.enabled is True
    assert rec(results, "pipeline")["stats"]["context"]["enabled"] is True
    assert rec(results, "pipeline")["stats"]["stages"]["verifier"]["status"] == "ok"
    assert rec(results, "pipeline_no_context")["stats"]["context"] == {"enabled": False}


def test_three_pipeline_arms_and_baseline_render_in_the_scorecard(config, tmp_path):
    out = tmp_path / "o"
    results = run(provider(), config, out, arms=["baseline", *ALL])

    assert results["config"]["arms"] == ["baseline", *ALL]
    assert set(results["summary"]) == {"baseline", *ALL}
    html = rebuild(out).read_text()
    for arm in ["baseline", *ALL]:
        assert arm in html
    # no_verifier lets the guarded finding through: more findings per PR, lower precision than the pipeline
    s = results["summary"]
    assert s["pipeline_no_verifier"]["findings_per_pr"]["mean"] > s["pipeline"]["findings_per_pr"]["mean"]
    assert s["pipeline_no_verifier"]["precision"]["mean"] < s["pipeline"]["precision"]["mean"]


def test_ablations_are_selectable_from_the_command_line(config, tmp_path, capsys):
    code = main(["--arms", "pipeline,pipeline_no_verifier,pipeline_no_context", "--runs", "1", "--prs", "pr09",
                 "--out", str(tmp_path / "res"), "--skip-judge-check"], provider=provider())

    shown = capsys.readouterr().out
    assert code == 0
    assert "pipeline_no_verifier: precision" in shown and "pipeline_no_context: precision" in shown
