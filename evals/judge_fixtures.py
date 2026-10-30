"""Judge agreement check: six hand-labeled (expected issue, finding, should_match) cases.

They are run through the same code as real scoring (`judge_findings`: the judge model plus the
location rule), one case at a time, and the share of cases where the verdict equals the label is
stored with the results. The cases are invented for this check; none comes from the dataset.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from evals.dataset import ExpectedIssue, Location
from evals.judge import judge_findings
from reviewer.config import Config
from reviewer.llm import Provider
from reviewer.models import Finding, FindingDraft


@dataclass(frozen=True)
class JudgeCase:
    id: str
    expected: ExpectedIssue
    finding: FindingDraft
    should_match: bool
    note: str


def _issue(id, file, line, description, related=()):
    return ExpectedIssue(id=id, file=file, line_hint=line, area="correctness", severity="high",
                         requires="diff", description=description, related=tuple(related))


def _finding(file, line, title, explanation, evidence="x = 1"):
    return FindingDraft(file=file, line=line, category="logic", severity="high", confidence=0.9,
                        title=title, explanation=explanation, evidence=evidence)


CASES: tuple[JudgeCase, ...] = (
    JudgeCase(
        "same-problem-same-line",
        _issue("c1", "app/routes/users.py", 42,
               "The new PUT /users/{id}/address endpoint has no authentication or ownership check, so any "
               "caller can change any user's address."),
        _finding("app/routes/users.py", 42, "Address update endpoint is unauthenticated",
                 "update_address has no get_current_user dependency and never checks the caller owns the "
                 "record, so anyone can overwrite another user's address."),
        True, "same root cause, same line, different words",
    ),
    JudgeCase(
        "same-problem-nearby-line",
        _issue("c2", "app/services/orders.py", 31,
               "The order search interpolates the user-supplied email into its SQL with an f-string, which "
               "allows SQL injection."),
        _finding("app/services/orders.py", 35, "User input formatted into the query",
                 "The email argument is formatted straight into the SELECT statement, so a crafted email "
                 "can change the query."),
        True, "same problem four lines from the hint",
    ),
    JudgeCase(
        "same-problem-at-related-location",
        _issue("c3", "app/services/pricing.py", 54,
               "get_order_total now returns integer cents, but the unchanged invoice code still treats the "
               "value as dollars, so invoices come out 100x too high.",
               related=[Location("app/services/invoices.py", 13)]),
        _finding("app/services/invoices.py", 14, "Invoice amount is 100x too large",
                 "build_invoice uses get_order_total as if it were dollars, but the function now returns "
                 "cents, so amount_due is wrong."),
        True, "cross-file breakage reported at the related location (D2)",
    ),
    JudgeCase(
        "different-problem-same-place",
        _issue("c4", "app/routes/exports.py", 20,
               "The export endpoint has no authentication check, so anyone can download any customer's orders."),
        _finding("app/routes/exports.py", 20, "Export filename has no length limit",
                 "The filename query parameter is used as given with no maximum length, which could produce "
                 "very long file names."),
        False, "a different problem at exactly the expected line must not match",
    ),
    JudgeCase(
        "same-kind-of-problem-elsewhere",
        _issue("c5", "app/services/pricing.py", 25,
               "apply_discount computes the discounted price with float arithmetic instead of Decimal."),
        _finding("app/services/pricing.py", 140, "Shipping fee uses float",
                 "calculate_shipping multiplies by a float rate, which can drop cents on large orders."),
        False, "the same kind of mistake in another function, far from the expected place",
    ),
    JudgeCase(
        "generic-remark-at-the-right-line",
        _issue("c6", "app/services/pricing.py", 54,
               "get_order_total returns integer cents while its callers assume Decimal dollars."),
        _finding("app/services/pricing.py", 55, "Consider adding tests for this function",
                 "This function has no direct unit test for the new return value; adding one would help."),
        False, "a vague suggestion about the right line is not the same problem",
    ),
)


def check_judge_agreement(provider: Provider, config: Config, *, cases: tuple[JudgeCase, ...] = CASES) -> dict:
    """Run every case through the judge; returns n, agree, agreement and per-case verdicts."""

    def one(case: JudgeCase) -> dict:
        finding = Finding.from_draft(case.finding, "baseline")
        try:
            out = judge_findings([case.expected], [finding], provider=provider, config=config)
        except Exception as e:  # a judge failure counts as disagreement, loudly
            return {"id": case.id, "should_match": case.should_match, "matched": None, "score": None,
                    "agrees": False, "note": case.note, "error": f"{type(e).__name__}: {e}", "cost_usd": 0.0}
        matched = out.tp == 1
        scores = [c["score"] for c in out.candidates]
        return {"id": case.id, "should_match": case.should_match, "matched": matched,
                "score": max(scores) if scores else None, "agrees": matched == case.should_match,
                "note": case.note, "candidate_status": [c["status"] for c in out.candidates],
                "cost_usd": out.cost_usd}

    with ThreadPoolExecutor(max_workers=max(1, config.pipeline.max_workers)) as pool:
        results = list(pool.map(one, cases))
    agree = sum(r["agrees"] for r in results)
    return {
        "n": len(results), "agree": agree, "agreement": agree / len(results) if results else None,
        "judge_model": config.models.judge, "cost_usd": sum(r["cost_usd"] for r in results),
        "cases": results,
    }
