"""The hand-labeled judge agreement fixture and how its result is stored with the eval results."""
import json
import re

import pytest

from evals.judge_fixtures import CASES, check_judge_agreement
from evals.run_eval import evaluate
from reviewer.fake import FakeProvider

SHOULD = {c.expected.id: c.should_match for c in CASES}


def judge_that(decide):
    """A fake judge answering `decide(expected_id) -> bool` for the single pair in the prompt."""

    def reply(call):
        expected = json.loads(re.search(r"<expected_issues>\n(.*?)\n</expected_issues>", call.user, re.S).group(1))
        eid = expected[0]["id"]
        return {"matches": [{"expected_id": eid, "finding_id": "F1", "score": 0.95, "reason": "r"}]
                if decide(eid) else []}

    return reply


def test_the_fixture_is_about_six_hand_labeled_cases_with_both_outcomes():
    assert len(CASES) == 6
    assert len({c.id for c in CASES}) == 6 and len(SHOULD) == 6
    assert sum(c.should_match for c in CASES) == 3
    for c in CASES:
        assert c.expected.description and c.finding.explanation and c.note


def test_a_judge_that_agrees_with_every_label_scores_full_agreement(config):
    out = check_judge_agreement(FakeProvider({"judge": judge_that(lambda eid: SHOULD[eid])}), config)

    assert (out["n"], out["agree"], out["agreement"]) == (6, 6, 1.0)
    assert out["judge_model"] == config.models.judge
    assert [c["id"] for c in out["cases"]] == [c.id for c in CASES]


def test_a_judge_that_accepts_everything_is_caught_but_the_location_rule_still_holds(config):
    out = check_judge_agreement(FakeProvider({"judge": judge_that(lambda eid: True)}), config)

    wrong = {c["id"] for c in out["cases"] if not c["agrees"]}
    # "different problem, same place" and "generic remark, right line" fool this judge; the
    # same-kind-of-problem-elsewhere case is stopped by the location check in code
    assert wrong == {"different-problem-same-place", "generic-remark-at-the-right-line"}
    elsewhere = next(c for c in out["cases"] if c["id"] == "same-kind-of-problem-elsewhere")
    assert elsewhere["candidate_status"] == ["location_rejected"]
    assert out["agreement"] == pytest.approx(4 / 6)


def test_a_judge_that_accepts_nothing_misses_the_positive_cases(config):
    out = check_judge_agreement(FakeProvider({"judge": judge_that(lambda eid: False)}), config)
    assert out["agree"] == 3


def test_a_failing_judge_counts_as_disagreement(config):
    out = check_judge_agreement(FakeProvider({"judge": RuntimeError("down")}), config)
    assert out["agree"] == 0 and out["agreement"] == 0.0
    assert all("down" in c["error"] for c in out["cases"])
    assert all(c["agrees"] is False for c in out["cases"])


def test_agreement_is_stored_with_the_results_and_shown_in_the_html(config, tmp_path):
    provider = FakeProvider({
        "baseline": {"findings": []},
        "judge": judge_that(lambda eid: SHOULD[eid]),
    })

    results = evaluate(arms=["baseline"], runs=1, pr_ids=["pr06"], provider=provider, config=config,
                       out_dir=tmp_path / "out", check_judge=True)

    saved = json.loads((tmp_path / "out" / "results.json").read_text())
    assert saved["judge_agreement"]["agreement"] == 1.0 and saved["judge_agreement"]["n"] == 6
    assert results["totals"]["judge_cost_usd"] > 0
    html = (tmp_path / "out" / "scorecard.html").read_text()
    assert "6 of 6" in html and "same-problem-at-related-location" in html


def test_without_the_flag_the_fixture_is_not_run(config, tmp_path):
    provider = FakeProvider({"baseline": {"findings": []}})
    results = evaluate(arms=["baseline"], runs=1, pr_ids=["pr06"], provider=provider, config=config,
                       out_dir=tmp_path / "out")
    assert results["judge_agreement"] is None and provider.calls_for("judge") == []
