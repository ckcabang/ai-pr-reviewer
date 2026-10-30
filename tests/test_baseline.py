"""Seam 1 for the baseline arm: ReviewInput -> ReviewResult with the provider injected."""
import pytest

from reviewer.agents.baseline import review_baseline
from reviewer.fake import FakeProvider
from reviewer.models import ReviewInput

BASE = {"app/pricing.py": "def total(order):\n    return order.subtotal + order.tax\n"}
HEAD = {"app/pricing.py": "def total(order):\n    cents = int((order.subtotal + order.tax) * 100)\n    return cents\n"}

FINDING = {
    "file": "app/pricing.py",
    "line": 2,
    "category": "logic",
    "severity": "high",
    "confidence": 0.9,
    "title": "Total is now returned in cents",
    "explanation": "Callers still treat the value as dollars.",
    "evidence": "cents = int((order.subtotal + order.tax) * 100)",
}


@pytest.fixture
def review_input(tmp_path, make_repo, diff_of):
    repo = make_repo(tmp_path / "repo", BASE, HEAD)
    (repo / "REVIEW.md").write_text("# Review rules\n## R1: never do the bad thing\n")
    return ReviewInput(repo_root=repo, diff=diff_of(repo), title="Return totals in cents", description="Switch.")


def test_baseline_findings_are_labelled_baseline_and_keep_the_output_contract(review_input, config):
    provider = FakeProvider({"baseline": {"findings": [FINDING]}})

    result = review_baseline(review_input, provider=provider, config=config)

    [finding] = result.findings
    assert finding.reviewer == "baseline"
    assert (finding.file, finding.line, finding.title) == ("app/pricing.py", 2, "Total is now returned in cents")
    assert finding.verdict == "keep" and finding.id


def test_baseline_sees_only_the_diff_with_the_merged_correctness_and_security_checklist(review_input, config):
    provider = FakeProvider({"baseline": {"findings": []}})

    review_baseline(review_input, provider=provider, config=config)

    [call] = provider.calls_for("baseline")
    assert call.model == config.models.baseline
    # diff only: no context bundle, no team rules, no do-not-flag section from REVIEW.md (D6, plan 4.4)
    assert "<diff>" in call.user and "2 +    cents = int(" in call.user
    for section in ("<context>", "<team_rules>", "<do_not_flag>"):
        assert section not in call.user
    assert "R1" not in call.user and "never do the bad thing" not in call.user
    # one prompt carries both specialists' checklists (D6)
    system = call.system.lower()
    assert "race conditions" in system and "money" in system  # correctness
    assert "injection" in system and "authorization" in system and "secrets" in system  # security
    assert "empty list" in system and "JSON" in call.system


def test_baseline_findings_are_scored_raw_with_no_confidence_threshold(review_input, config):
    shaky = {**FINDING, "title": "Shaky", "confidence": 0.2, "severity": "low"}
    provider = FakeProvider({"baseline": {"findings": [FINDING, shaky]}})

    result = review_baseline(review_input, provider=provider, config=config)

    assert {f.title for f in result.findings} == {"Total is now returned in cents", "Shaky"}
    assert result.stats["counts"] == {"raw": 2, "after_verifier": 2, "after_triage": 2}


def test_baseline_applies_only_the_per_reviewer_cap_and_the_anchor_check(review_input, config):
    config.pipeline.max_findings_per_agent = 2
    many = [{**FINDING, "title": f"P{i}", "confidence": 0.5 + i / 20} for i in range(4)]
    off_diff = {**FINDING, "line": 99, "title": "Imaginary", "confidence": 1.0}
    provider = FakeProvider({"baseline": {"findings": [off_diff, *many]}})

    result = review_baseline(review_input, provider=provider, config=config)

    assert [f.title for f in result.findings] == ["P3", "P2"]
    assert result.stats["specialists"]["baseline"]["dropped_invalid_anchor"] == 1


def test_baseline_tiers_use_the_same_action_required_rule_as_the_pipeline_without_dropping_anything(
    review_input, config
):
    sure_high = FINDING
    unsure_high = {**FINDING, "title": "Unsure", "confidence": 0.6}
    sure_low = {**FINDING, "title": "Sure but minor", "severity": "low"}
    provider = FakeProvider({"baseline": {"findings": [sure_high, unsure_high, sure_low]}})

    result = review_baseline(review_input, provider=provider, config=config)

    tiers = {f.title: f.triage for f in result.findings}
    assert tiers == {
        "Total is now returned in cents": "action_required",
        "Unsure": "review_recommended",
        "Sure but minor": "review_recommended",
    }


def test_baseline_stats_follow_the_pipeline_shape_with_the_baseline_model_cost(review_input, config):
    provider = FakeProvider({"baseline": {"findings": [FINDING]}})

    stats = review_baseline(review_input, provider=provider, config=config).stats

    spec = stats["specialists"]["baseline"]
    assert spec["status"] == "ok" and spec["model"] == config.models.baseline and spec["cached"] is False
    assert spec["input_tokens"] > 0 and spec["cost_usd"] > 0
    assert stats["totals"]["cost_usd"] == pytest.approx(spec["cost_usd"])
    assert stats["totals"]["wall_s"] >= 0 and stats["files_reviewed"] == ["app/pricing.py"]


def test_baseline_with_nothing_reviewable_makes_no_model_call(tmp_path, make_repo, diff_of, config):
    repo = make_repo(tmp_path / "repo", {"uv.lock": "a\n"}, {"uv.lock": "a\nb\n"})
    provider = FakeProvider({})

    result = review_baseline(
        ReviewInput(repo_root=repo, diff=diff_of(repo)), provider=provider, config=config
    )

    assert result.findings == [] and provider.calls == []


def test_a_failed_baseline_call_raises_rather_than_looking_like_a_clean_review(review_input, config):
    provider = FakeProvider({"baseline": RuntimeError("boom")})
    with pytest.raises(RuntimeError, match="boom"):
        review_baseline(review_input, provider=provider, config=config)


def test_baseline_carries_the_security_specialists_checklist_once_that_specialist_exists():
    security = pytest.importorskip("reviewer.agents.security")
    from reviewer.agents.baseline import BASELINE

    spec = security.SECURITY
    assert set(spec.flag) <= set(BASELINE.flag)
    assert set(spec.do_not_flag) <= set(BASELINE.do_not_flag)
