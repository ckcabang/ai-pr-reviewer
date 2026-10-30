"""Triage is deterministic code: direct unit tests for each rule."""
from reviewer.config import PipelineConfig
from reviewer.models import VerifiedFinding
from reviewer.triage import triage


def vf(id="f1", *, file="a.py", line=10, category="logic", severity="high", confidence=0.9,
       verdict="keep", duplicate_of=None, **kw):
    return VerifiedFinding(
        id=id, reviewer="correctness", file=file, line=line, category=category, severity=severity,
        confidence=confidence, title=f"title {id}", explanation="e", evidence="x",
        verdict=verdict, duplicate_of=duplicate_of, **kw)


def run(findings, **kw):
    return triage(findings, PipelineConfig(**kw))


def tiers(result):
    return {f.id: f.triage for f in result.findings}


def test_verifier_drops_are_dropped_with_a_reason():
    r = run([vf("a", verdict="drop", verifier_reason="handled in caller"), vf("b")])
    assert tiers(r) == {"a": "dropped", "b": "action_required"}
    dropped = next(f for f in r.findings if f.id == "a")
    assert "verifier" in dropped.triage_reason and "handled in caller" in dropped.triage_reason


def test_below_confidence_threshold_is_dropped_and_threshold_itself_is_kept():
    r = run([vf("a", confidence=0.59, line=1), vf("b", confidence=0.6, line=40)], confidence_threshold=0.6)
    assert tiers(r) == {"a": "dropped", "b": "review_recommended"}


def test_findings_flagged_as_duplicates_are_dropped():
    r = run([vf("a", duplicate_of="b", line=50), vf("b")])
    assert tiers(r) == {"a": "dropped", "b": "action_required"}


def test_near_duplicates_collapse_to_the_highest_confidence():
    r = run([
        vf("a", line=10, confidence=0.8),
        vf("b", line=13, confidence=0.95),   # within +-3 of a, same file and category
        vf("c", line=14, confidence=0.9),    # 1 away from the kept b -> collapses into b
    ])
    assert tiers(r) == {"b": "action_required", "a": "dropped", "c": "dropped"}


def test_lines_more_than_three_apart_or_different_file_or_category_are_not_collapsed():
    r = run([
        vf("a", line=10), vf("b", line=14), vf("c", line=10, file="b.py"),
        vf("d", line=10, category="auth"),
    ])
    assert all(t == "action_required" for t in tiers(r).values())


def test_action_required_needs_configured_severity_and_minimum_confidence():
    r = run([
        vf("a", severity="critical", confidence=0.75, line=1),
        vf("b", severity="high", confidence=0.74, line=20),
        vf("c", severity="medium", confidence=0.99, line=40),
    ])
    assert tiers(r) == {"a": "action_required", "b": "review_recommended", "c": "review_recommended"}


def test_action_required_severities_are_configurable():
    r = run([vf("a", severity="medium", line=1)],
            action_required={"severities": ["medium"], "min_confidence": 0.5})
    assert tiers(r) == {"a": "action_required"}


def test_low_severity_is_dropped_unless_confidence_is_at_least_0_9():
    r = run([vf("a", severity="low", confidence=0.89, line=1), vf("b", severity="low", confidence=0.9, line=40)])
    assert tiers(r) == {"a": "dropped", "b": "review_recommended"}


def test_sorted_by_tier_then_severity_then_confidence():
    r = run([
        vf("rec-high", severity="high", confidence=0.7, line=1),
        vf("act-high-lo", severity="high", confidence=0.8, line=20),
        vf("act-crit", severity="critical", confidence=0.8, line=40),
        vf("act-high-hi", severity="high", confidence=0.95, line=60),
        vf("rec-med", severity="medium", confidence=0.99, line=80),
        vf("dropped", verdict="drop", line=100),
    ])
    assert [f.id for f in r.findings] == [
        "act-crit", "act-high-hi", "act-high-lo", "rec-high", "rec-med", "dropped"]


def test_cap_keeps_the_best_and_records_how_many_it_removed():
    fs = [vf(f"f{i}", line=i * 10, confidence=0.9 - i / 100) for i in range(5)]
    r = run(fs, max_findings_total=3)
    assert [f.id for f in r.findings if f.triage != "dropped"] == ["f0", "f1", "f2"]
    assert r.cap_removed == 2
    capped = next(f for f in r.findings if f.id == "f4")
    assert capped.triage == "dropped" and "cap" in capped.triage_reason


def test_all_findings_including_dropped_are_returned_and_reasons_counted():
    r = run([vf("a", verdict="drop", line=1), vf("b", confidence=0.1, line=40), vf("c", line=80)])
    assert len(r.findings) == 3
    assert r.dropped_by_reason == {"verifier": 1, "below_threshold": 1}
    assert r.cap_removed == 0
