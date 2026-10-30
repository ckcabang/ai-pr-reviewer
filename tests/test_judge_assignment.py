"""Direct unit tests for the judge's one-to-one greedy assignment and location rule (pure code)."""
import random

import pytest

from evals.judge import Candidate, FunctionLocator, assign, count_outcomes, location_ok
from evals.dataset import ExpectedIssue, Location


def cand(expected_id, finding, score, reason=""):
    return Candidate(expected_id=expected_id, finding=finding, score=score, reason=reason)


def pairs(assigned):
    return sorted((c.expected_id, c.finding) for c in assigned)


def test_highest_score_wins_and_neither_side_is_reused():
    candidates = [cand("A", 0, 0.9), cand("A", 1, 0.8), cand("B", 0, 0.7)]

    assigned = assign(candidates, expected_ids=["A", "B"], n_findings=2)

    assert pairs(assigned) == [("A", 0)]  # B only liked finding 0, which A took; finding 1 is an FP


def test_pairs_below_the_threshold_are_not_assigned_but_exactly_at_it_is():
    candidates = [cand("A", 0, 0.49), cand("B", 1, 0.5)]
    assert pairs(assign(candidates, expected_ids=["A", "B"], n_findings=2)) == [("B", 1)]


def test_hallucinated_ids_and_out_of_range_findings_are_ignored():
    candidates = [cand("ZZ", 0, 1.0), cand("A", 7, 1.0), cand("A", -1, 1.0), cand("A", 0, 0.8)]
    assert pairs(assign(candidates, expected_ids=["A"], n_findings=1)) == [("A", 0)]


def test_a_repeated_pair_counts_once_at_its_best_score():
    candidates = [cand("A", 0, 0.6), cand("A", 0, 0.95), cand("A", 0, 0.7)]
    [only] = assign(candidates, expected_ids=["A"], n_findings=1)
    assert only.score == 0.95


def test_equal_scores_resolve_to_the_assignment_that_keeps_more_issues_matched():
    # A could take finding 0 or 1 (both 1.0); B can only take finding 0. Position order alone
    # would give 0 to A and strand B; preferring the scarcer option matches both.
    candidates = [cand("A", 0, 1.0), cand("A", 1, 1.0), cand("B", 0, 1.0)]
    assert pairs(assign(candidates, expected_ids=["A", "B"], n_findings=2)) == [("A", 1), ("B", 0)]


def test_assignment_does_not_depend_on_candidate_order():
    candidates = [cand("A", 0, 0.8), cand("A", 1, 0.8), cand("B", 1, 0.8), cand("C", 2, 0.6)]
    expected = pairs(assign(candidates, expected_ids=["A", "B", "C"], n_findings=3))
    for seed in range(10):
        shuffled = candidates[:]
        random.Random(seed).shuffle(shuffled)
        assert pairs(assign(shuffled, expected_ids=["A", "B", "C"], n_findings=3)) == expected


def test_no_candidates_means_everything_is_unassigned():
    assert assign([], expected_ids=["A"], n_findings=3) == []


def test_counts_are_tp_fp_fn_and_fp_is_never_negative():
    assigned = assign([cand("A", 0, 0.9)], expected_ids=["A", "B"], n_findings=3)
    assert count_outcomes(assigned, n_findings=3, n_expected=2) == {"tp": 1, "fp": 2, "fn": 1}
    # no findings at all: every expected issue is a false negative, no false positives
    assert count_outcomes([], n_findings=0, n_expected=2) == {"tp": 0, "fp": 0, "fn": 2}


@pytest.mark.parametrize("seed", range(25))
def test_random_candidate_sets_always_give_a_valid_one_to_one_assignment(seed):
    rng = random.Random(seed)
    n_findings, n_expected = rng.randint(0, 6), rng.randint(0, 6)
    expected_ids = [f"E{i}" for i in range(n_expected)]
    candidates = [
        cand(rng.choice(expected_ids + ["bogus"]), rng.randint(-1, n_findings), round(rng.random(), 2))
        for _ in range(rng.randint(0, 25))
    ] if expected_ids else []

    assigned = assign(candidates, expected_ids=expected_ids, n_findings=n_findings)
    counts = count_outcomes(assigned, n_findings=n_findings, n_expected=n_expected)

    assert len({c.expected_id for c in assigned}) == len(assigned)  # no expected issue reused
    assert len({c.finding for c in assigned}) == len(assigned)  # no finding reused
    assert all(c.score >= 0.5 for c in assigned)
    assert counts["tp"] == len(assigned)
    assert counts["fp"] >= 0 and counts["fn"] >= 0
    assert counts["tp"] + counts["fp"] == n_findings and counts["tp"] + counts["fn"] == n_expected


# --- the location rule: same underlying problem is the judge's call, WHERE is checked in code ---

ISSUE = ExpectedIssue(
    id="pr9-1", file="app/pricing.py", line_hint=54, area="correctness", severity="high",
    requires="cross_file", description="d",
    related=(Location("app/invoices.py", 13),),
)


@pytest.mark.parametrize(
    "file, line, ok",
    [
        ("app/pricing.py", 54, True),
        ("app/pricing.py", 44, True),   # exactly 10 lines away
        ("app/pricing.py", 64, True),
        ("app/pricing.py", 65, False),
        ("app/pricing.py", 43, False),
        ("app/invoices.py", 13, True),  # related location (D2)
        ("app/invoices.py", 20, True),
        ("app/invoices.py", 30, False),
        ("app/orders.py", 54, False),   # right line, wrong file
    ],
)
def test_a_finding_must_sit_at_the_anchor_or_a_related_location(file, line, ok):
    assert location_ok(ISSUE, file, line) is ok


def test_same_enclosing_function_counts_even_when_far_from_the_line_hint(tmp_path):
    (tmp_path / "app").mkdir()
    body = "".join(f"    x{i} = {i}\n" for i in range(40))
    (tmp_path / "app" / "pricing.py").write_text(
        "def other():\n    return 1\n\n\ndef get_total(order):\n" + body + "    return x0\n\n\n"
        "def after():\n    return 2\n"
    )
    locator = FunctionLocator(tmp_path)
    issue = ExpectedIssue(id="p-1", file="app/pricing.py", line_hint=30, area="correctness",
                          severity="high", requires="diff", description="d")

    assert location_ok(issue, "app/pricing.py", 46, locator) is True   # 16 lines away, same function
    assert location_ok(issue, "app/pricing.py", 2, locator) is False   # a different function, far away
    assert location_ok(issue, "app/pricing.py", 46) is False           # without source, only +-10 applies
    assert locator.enclosing("app/pricing.py", 40) == "get_total"
    assert locator.enclosing("app/pricing.py", 4) is None              # between functions
    assert locator.enclosing("app/missing.py", 4) is None


def test_methods_are_qualified_by_their_class(tmp_path):
    (tmp_path / "m.py").write_text("class Svc:\n    def a(self):\n        return 1\n\n    def b(self):\n        return 2\n")
    locator = FunctionLocator(tmp_path)
    assert locator.enclosing("m.py", 3) == "Svc.a"
    assert locator.enclosing("m.py", 6) == "Svc.b"
