"""Seam 2: `evaluate` over real dataset PRs with the fake provider (baseline arm, judge, metrics,
saved results, offline HTML rebuild)."""
import json
import re
import threading
import time

import pytest

from evals.arms import Arm, UnknownArm
from evals.dataset import load_pr
from evals.run_eval import evaluate
from evals.scorecard import rebuild
from reviewer.diff import parse_diff
from reviewer.fake import FakeProvider
from reviewer.models import Finding, FindingDraft, ReviewResult, VerifiedFinding
from reviewer.pipeline import review

PR09, PR06 = load_pr("pr09"), load_pr("pr06")
ANCHOR = ("app/services/pricing.py", 54)  # pr09's expected issue (its labels.yaml line_hint)


def commentable(pr):
    return [(f.path, n) for f in parse_diff(pr.patch) for n in f.commentable_lines]


def draft(file, line, title, severity="high", confidence=0.9):
    return {"file": file, "line": line, "category": "logic", "severity": severity, "confidence": confidence,
            "title": title, "explanation": f"{title} explanation", "evidence": "x = 1"}


HIT = draft(*ANCHOR, "Totals now returned in cents")
NOISE = draft("app/services/pricing.py", 1, "Docstring wording", severity="low", confidence=0.7)
CLEAN_PR_ALARM = draft(*commentable(PR06)[0], "Rename looks risky")


def baseline_script(plan):
    """plan[run][pr_id] -> list of finding dicts; the run is the n-th time that PR is reviewed."""
    seen: dict[str, int] = {}
    lock = threading.Lock()

    def reply(call):
        pr = PR09 if PR09.title in call.user else PR06
        with lock:
            run = seen.get(pr.id, 0)
            seen[pr.id] = run + 1
        return {"findings": plan[run].get(pr.id, [])}

    return reply


def judge_script(call):
    """A judge that matches any finding whose title mentions cents to pr09's expected issue."""
    findings = json.loads(re.search(r"<findings>\n(.*?)\n</findings>", call.user, re.S).group(1))
    return {"matches": [
        {"expected_id": "pr09-1", "finding_id": f["id"], "score": 0.9, "reason": "same problem"}
        for f in findings if "cents" in f["title"]
    ]}


def two_run_provider():
    return FakeProvider({
        "baseline": baseline_script([
            {"pr09": [HIT, NOISE], "pr06": []},
            {"pr09": [NOISE], "pr06": [CLEAN_PR_ALARM]},
        ]),
        "judge": judge_script,
    })


@pytest.fixture
def out_dir(tmp_path):
    return tmp_path / "out"


def run(provider, config, out_dir, **kw):
    kw.setdefault("arms", ["baseline"])
    kw.setdefault("runs", 2)
    kw.setdefault("pr_ids", ["pr06", "pr09"])
    return evaluate(provider=provider, config=config, out_dir=out_dir, **kw)


def test_baseline_arm_is_scored_end_to_end_with_mean_and_std_over_runs(config, out_dir):
    results = run(two_run_provider(), config, out_dir)

    s = results["summary"]["baseline"]
    # run 1: TP 1, FP 1, FN 0 -> P .5 R 1 F1 2/3.   run 2: TP 0, FP 2, FN 1 -> P 0 R 0 F1 0
    assert s["precision"]["mean"] == pytest.approx(0.25)
    assert s["precision"]["std"] == pytest.approx(0.35355, abs=1e-4)
    assert s["recall"]["mean"] == pytest.approx(0.5)
    assert s["recall"]["std"] == pytest.approx(0.70711, abs=1e-4)
    assert s["f1"]["mean"] == pytest.approx(1 / 3)
    assert s["n_runs"] == 2
    assert s["findings_per_pr"]["mean"] == pytest.approx(1.0)  # (2+0)/2 and (1+1)/2
    # clean PR (pr06): quiet in run 1, one action-required finding in run 2
    assert s["clean_pr"]["n"] == 1
    assert s["clean_pr"]["any_finding_rate"]["mean"] == pytest.approx(0.5)
    assert s["clean_pr"]["action_required_rate"]["mean"] == pytest.approx(0.5)
    # recall by required context and by area (pr09-1 is cross_file / correctness)
    assert s["recall_by_requires"]["cross_file"]["mean"] == pytest.approx(0.5)
    assert s["recall_by_requires"]["diff"] is None and s["recall_by_requires"]["rules"] is None
    assert s["recall_by_area"]["correctness"]["mean"] == pytest.approx(0.5)
    assert s["recall_by_area"]["security"] is None
    # cost, tokens and latency are reported per PR
    assert s["cost_usd_per_pr"]["mean"] > 0 and s["input_tokens_per_pr"]["mean"] > 0
    assert s["latency_p50_s"]["mean"] >= 0 and s["latency_p95_s"]["mean"] >= s["latency_p50_s"]["mean"]
    assert s["errors"] == {"review": 0, "judge": 0}


def test_single_run_has_no_std(config, out_dir):
    provider = FakeProvider({"baseline": baseline_script([{"pr09": [HIT], "pr06": []}]), "judge": judge_script})
    s = run(provider, config, out_dir, runs=1)["summary"]["baseline"]
    assert s["precision"] == {"mean": 1.0, "std": None, "n": 1}
    assert s["clean_pr"]["any_finding_rate"]["mean"] == 0.0


def test_every_pr_goes_through_the_materializer_and_the_diff_the_cli_would_review(config, out_dir):
    provider = FakeProvider({"baseline": {"findings": []}, "judge": {"matches": []}})
    run(provider, config, out_dir, runs=1)

    users = {c.user for c in provider.calls_for("baseline")}
    assert len(users) == 2
    for pr in (PR06, PR09):
        [prompt] = [u for u in users if pr.title in u]
        assert pr.description.split(".")[0] in prompt          # title and description from meta.yaml
        assert "app/services/pricing.py" in prompt              # the numbered diff of base...head
        assert "labels" not in prompt.lower()                   # no expected issue ever reaches an arm
        assert PR09.expected_issues[0].description[:40] not in prompt


def test_results_file_holds_raw_findings_judge_matches_and_stats_and_is_timestamped_by_default(
    config, out_dir
):
    results = run(two_run_provider(), config, out_dir)

    saved = json.loads((out_dir / "results.json").read_text())
    assert saved == json.loads(json.dumps(results))
    assert saved["schema_version"] == 1 and saved["config"]["arms"] == ["baseline"]
    assert [d["id"] for d in saved["dataset"]] == ["pr06", "pr09"]
    assert saved["dataset"][1]["expected_issues"][0]["requires"] == "cross_file"

    recs = {(r["run"], r["pr"]): r for r in saved["records"]}
    assert len(recs) == 4 and all(r["arm"] == "baseline" and r["status"] == "ok" for r in recs.values())
    hit = recs[(0, "pr09")]
    assert [f["title"] for f in hit["findings"]] == ["Totals now returned in cents", "Docstring wording"]
    assert all(f["reviewer"] == "baseline" for f in hit["findings"])
    assert hit["stats"]["totals"]["input_tokens"] > 0 and hit["wall_s"] >= 0
    assert hit["judge"]["matches"][0]["expected_id"] == "pr09-1"
    assert (hit["judge"]["tp"], hit["judge"]["fp"], hit["judge"]["fn"]) == (1, 1, 0)
    assert hit["judge"]["candidates"][0]["status"] == "assigned"
    assert recs[(1, "pr06")]["judge"]["fp"] == 1 and recs[(1, "pr06")]["judge"]["candidates"] == []


def test_default_output_folder_is_a_timestamped_dir_under_evals_out(config, tmp_path, monkeypatch):
    import evals.run_eval as run_eval

    monkeypatch.setattr(run_eval, "OUT_ROOT", tmp_path / "out")
    provider = FakeProvider({"baseline": {"findings": []}, "judge": {"matches": []}})
    results = evaluate(arms=["baseline"], runs=1, pr_ids=["pr06"], provider=provider, config=config)

    [folder] = list((tmp_path / "out").iterdir())
    assert re.fullmatch(r"\d{8}-\d{6}", folder.name)
    assert (folder / "results.json").is_file() and (folder / "scorecard.html").is_file()
    assert results["out_dir"] == str(folder)


def test_the_cache_is_never_used_even_when_enabled_in_config(config, out_dir, tmp_path):
    config.cache.enabled = True
    config.cache.dir = tmp_path / "cache"

    results = run(two_run_provider(), config, out_dir)

    assert not (tmp_path / "cache").exists()
    assert all(r["stats"]["totals"]["cache_hits"] == 0 for r in results["records"])


class Probe:
    """Wraps a provider and records how many chat calls were in flight at once."""

    def __init__(self, inner):
        self.inner, self.active, self.peak, self.lock = inner, 0, 0, threading.Lock()

    def chat_json(self, **kw):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.15)
            return self.inner.chat_json(**kw)
        finally:
            with self.lock:
                self.active -= 1

    def embed(self, **kw):
        return self.inner.embed(**kw)


@pytest.mark.parametrize("workers, expect_parallel", [(1, False), (4, True)])
def test_pr_runs_execute_in_parallel_up_to_the_configured_worker_count(config, out_dir, workers, expect_parallel):
    config.pipeline.max_workers = workers
    probe = Probe(FakeProvider({"baseline": {"findings": []}, "judge": {"matches": []}}))

    run(probe, config, out_dir, runs=2)

    assert probe.peak <= workers
    assert (probe.peak >= 2) is expect_parallel


def test_a_failed_review_is_recorded_and_left_out_of_the_metrics_not_scored_as_clean(config, out_dir):
    def flaky(call):
        if PR06.title in call.user:
            return RuntimeError("provider down")
        return {"findings": [HIT]}

    provider = FakeProvider({"baseline": flaky, "judge": judge_script})
    results = run(provider, config, out_dir, runs=1)

    [bad] = [r for r in results["records"] if r["pr"] == "pr06"]
    assert bad["status"] == "error" and "provider down" in bad["error"] and "judge" not in bad
    s = results["summary"]["baseline"]
    assert s["errors"] == {"review": 1, "judge": 0}
    assert s["clean_pr"]["n"] == 0 and s["clean_pr"]["any_finding_rate"] is None  # nothing to say about clean PRs
    assert s["recall"]["mean"] == 1.0


def test_a_failed_judge_is_recorded_and_the_review_is_kept(config, out_dir):
    provider = FakeProvider({"baseline": {"findings": [HIT]}, "judge": RuntimeError("judge down")})
    results = run(provider, config, out_dir, runs=1, pr_ids=["pr09"])

    [rec] = results["records"]
    assert rec["status"] == "judge_error" and "judge down" in rec["error"] and len(rec["findings"]) == 1
    assert results["summary"]["baseline"]["errors"] == {"review": 0, "judge": 1}
    assert results["summary"]["baseline"]["precision"] is None


def test_the_pipeline_plugs_in_as_an_arm_and_only_triaged_findings_are_scored(config, out_dir):
    provider = FakeProvider({"correctness": {"findings": [HIT]}, "judge": judge_script})

    def stub_pipeline_with_a_dropped_finding(review_input, *, provider, config):
        result = review(review_input, provider=provider, config=config)
        extra = VerifiedFinding(**Finding.from_draft(FindingDraft(**NOISE), "correctness").model_dump(),
                                verdict="drop", triage="dropped")
        return ReviewResult(findings=[*result.findings, extra], stats=result.stats)

    arms = {"pipeline": Arm("pipeline", review), "pipeline_stub": Arm("pipeline_stub", stub_pipeline_with_a_dropped_finding)}
    results = run(provider, config, out_dir, arms=["pipeline", "pipeline_stub"], runs=1, pr_ids=["pr09"],
                  arm_registry=arms)

    assert set(results["summary"]) == {"pipeline", "pipeline_stub"}
    stub = next(r for r in results["records"] if r["arm"] == "pipeline_stub")
    assert len(stub["findings"]) == 2 and stub["findings"][-1]["triage"] == "dropped"  # kept for debugging
    assert stub["judge"]["tp"] == 1 and stub["judge"]["fp"] == 0                       # but not scored
    assert len(stub["scored_finding_ids"]) == 1
    assert results["summary"]["pipeline"]["precision"]["mean"] == 1.0


def test_every_arm_gets_the_base_ref_so_rules_come_from_the_base_like_in_the_cli(config, out_dir):
    seen = []

    def spy(review_input, *, provider, config):
        seen.append(review_input.base_ref)
        return ReviewResult(findings=[], stats={})

    run(FakeProvider({"judge": {"matches": []}}), config, out_dir, arms=["spy"], runs=1, pr_ids=["pr06"],
        arm_registry={"spy": Arm("spy", spy)})

    assert seen == ["main"]


def test_unknown_arms_are_rejected_before_any_work(config, out_dir):
    provider = FakeProvider({})
    with pytest.raises(UnknownArm, match="nope.*baseline"):
        run(provider, config, out_dir, arms=["baseline", "nope"])
    assert provider.calls == [] and not out_dir.exists()


def test_scorecard_rebuilds_from_the_saved_file_with_no_provider_calls(config, out_dir):
    provider = two_run_provider()
    run(provider, config, out_dir)
    calls_before = len(provider.calls)
    original = (out_dir / "scorecard.html").read_text()
    (out_dir / "scorecard.html").unlink()

    path = rebuild(out_dir)

    assert path == out_dir / "scorecard.html" and len(provider.calls) == calls_before
    html = path.read_text()
    assert html == original
    assert "<table" in html and "baseline" in html and "pr09" in html and "pr06" in html
    assert "0.25" in html and "0.50" in html  # precision and recall means, read back from the file

    # it is built from the file alone: edit the saved results and the page follows
    saved = json.loads((out_dir / "results.json").read_text())
    saved["dataset"][1]["title"] = "A title only the edited file has"
    (out_dir / "results.json").write_text(json.dumps(saved))
    assert "A title only the edited file has" in rebuild(out_dir).read_text()
