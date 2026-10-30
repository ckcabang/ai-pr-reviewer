"""Seam 1 for the three specialists: `review()` with the provider injected, covering the
parallel run, partial failure, the standards rules, anchoring (D2/D25) and the stats footer."""
import threading
import time

import pytest

from reviewer.fake import FakeProvider
from reviewer.llm import CachedProvider
from reviewer.models import ReviewInput
from reviewer.pipeline import AllSpecialistsFailed, review
from reviewer.render import render_markdown

RULES = """\
# Review rules

## R2: Parameterized SQL only
Use parameterized SQL only, via `db.execute(sql, params)`. No f-strings, `%` or `+` in SQL.

## R3: Money is Decimal
Money is `Decimal` (dollars) in services; never `float`.

# Do not flag
- Naming, formatting, import order, docstrings, missing type hints
"""

BASE = {
    "REVIEW.md": RULES,
    "app/orders.py": "def find(db, email):\n    return db.execute('SELECT * FROM orders WHERE email = ?', (email,))\n",
}
HEAD = {
    "app/orders.py": (
        "def find(db, email):\n"
        "    price = float(email)\n"
        "    return db.execute(f\"SELECT * FROM orders WHERE email = '{email}'\")\n"
    ),
}


def draft(role, **over):
    base = {
        "file": "app/orders.py", "line": 3, "category": "logic", "severity": "high", "confidence": 0.9,
        "title": f"{role} problem", "explanation": "Concrete harm.", "evidence": "db.execute(f\"SELECT",
    }
    return {**base, **over}


CORRECT = draft("correctness", line=2, title="float parsed from an email", evidence="price = float(email)")
SECURE = draft("security", title="SQL injection through email", category="injection", evidence="f\"SELECT")
STANDARD = draft(
    "standards", title="SQL built with an f-string", category="rule", rule_id="R2", severity="medium",
    evidence="R2: Use parameterized SQL only, via `db.execute(sql, params)`. | db.execute(f\"SELECT",
)


@pytest.fixture
def review_input(tmp_path, make_repo, diff_of):
    repo = make_repo(tmp_path / "repo", BASE, HEAD)
    return ReviewInput(repo_root=repo, diff=diff_of(repo), title="Search orders by email",
                       description="Adds an email lookup.", base_ref="main")


def provider_for(keep_all, *, correctness=None, security=None, standards=None, **extra):
    return FakeProvider({
        "correctness": {"findings": [CORRECT] if correctness is None else correctness},
        "security": {"findings": [SECURE] if security is None else security},
        "standards": {"findings": [STANDARD] if standards is None else standards},
        "verifier": keep_all,
        **extra,
    })


def test_all_three_specialists_run_and_their_findings_are_labelled_by_reviewer(review_input, config, keep_all):
    provider = provider_for(keep_all)

    result = review(review_input, provider=provider, config=config)

    assert {f.reviewer for f in result.findings} == {"correctness", "security", "standards"}
    assert {(f.reviewer, f.title) for f in result.findings} == {
        ("correctness", "float parsed from an email"),
        ("security", "SQL injection through email"),
        ("standards", "SQL built with an f-string"),
    }
    for role in ("correctness", "security", "standards"):
        [call] = provider.calls_for(role)
        assert call.model == config.models.specialist
        assert "app/orders.py" in call.user and "<diff>" in call.user
    assert set(result.stats["specialists"]) == {"correctness", "security", "standards"}
    assert result.stats["counts"]["raw"] == 3


def test_each_specialist_gets_its_own_checklist(review_input, config, keep_all):
    provider = provider_for(keep_all, correctness=[], security=[], standards=[])

    review(review_input, provider=provider, config=config)

    system = {r: provider.calls_for(r)[0].system.lower() for r in ("correctness", "security", "standards")}
    assert "race conditions" in system["correctness"] and "path traversal" not in system["correctness"]
    assert "path traversal" in system["security"] and "constant-time" in system["security"]
    assert "race conditions" not in system["security"]
    assert "team_rules" in system["standards"]
    assert "race conditions" not in system["standards"] and "path traversal" not in system["standards"]


def test_all_specialists_see_all_the_team_rules_and_the_dont_flag_list(review_input, config, keep_all):
    provider = provider_for(keep_all, correctness=[], security=[], standards=[])

    review(review_input, provider=provider, config=config)

    for role in ("correctness", "security", "standards"):
        user = provider.calls_for(role)[0].user
        assert "<team_rules>" in user and "R3: Money is Decimal" in user
        assert "<do_not_flag>" in user and "missing type hints" in user


# -- standards -------------------------------------------------------------------------

def test_standards_prompt_requires_a_rule_id_and_the_quoted_rule_text(review_input, config, keep_all):
    provider = provider_for(keep_all, correctness=[], security=[], standards=[])

    review(review_input, provider=provider, config=config)

    system = provider.calls_for("standards")[0].system
    assert "rule_id" in system and "quote" in system.lower()
    assert "specific" in system.lower() and "team_rules" in system


def test_standards_prompt_applies_rules_to_application_code_not_to_test_code(review_input, config, keep_all):
    provider = provider_for(keep_all, correctness=[], security=[], standards=[])

    review(review_input, provider=provider, config=config)

    system = provider.calls_for("standards")[0].system.lower()
    assert "application" in system and "production" in system
    assert "test code" in system and "fixture" in system
    # the guidance is general: no dataset rule topics, file names or PR ids in the prompt text
    assert "sql" not in system and "pr0" not in system and "pr1" not in system


def test_a_standards_finding_must_cite_a_rule_that_exists(review_input, config, keep_all):
    no_rule = {**STANDARD, "title": "No rule cited", "rule_id": None}
    made_up = {**STANDARD, "title": "Invented rule", "rule_id": "R42"}
    provider = provider_for(keep_all, standards=[no_rule, made_up, STANDARD])

    result = review(review_input, provider=provider, config=config)

    standards = [f for f in result.findings if f.reviewer == "standards"]
    assert [(f.title, f.rule_id) for f in standards] == [("SQL built with an f-string", "R2")]
    stats = result.stats["specialists"]["standards"]
    assert (stats["raw_findings"], stats["kept_findings"], stats["dropped_invalid_rule"]) == (3, 1, 2)


def test_security_and_correctness_findings_need_no_rule_id(review_input, config, keep_all):
    result = review(review_input, provider=provider_for(keep_all), config=config)

    by_reviewer = {f.reviewer: f for f in result.findings}
    assert by_reviewer["security"].rule_id is None and by_reviewer["correctness"].rule_id is None


def test_without_team_rules_the_standards_specialist_is_skipped_not_asked(tmp_path, make_repo, diff_of, config, keep_all):
    repo = make_repo(tmp_path / "repo", {k: v for k, v in BASE.items() if k != "REVIEW.md"}, HEAD)
    provider = provider_for(keep_all)

    result = review(ReviewInput(repo_root=repo, diff=diff_of(repo), base_ref="main"), provider=provider, config=config)

    assert provider.calls_for("standards") == []
    assert result.stats["specialists"]["standards"]["status"] == "skipped"
    assert result.stats["specialist_failures"] == {}
    assert "Specialist standards: skipped (no team rules)" in render_markdown(result)


def test_the_no_context_ablation_has_no_rules_so_standards_is_skipped(review_input, config, keep_all):
    config.context.enabled = False
    provider = provider_for(keep_all)

    result = review(review_input, provider=provider, config=config)

    assert provider.calls_for("standards") == []
    assert result.stats["specialists"]["standards"] == {"status": "skipped", "reason": "no team rules"}
    assert {f.reviewer for f in result.findings} == {"correctness", "security"}


def test_a_rule_id_is_matched_forgivingly_and_stored_as_the_rules_own_spelling(review_input, config, keep_all):
    sloppy = {**STANDARD, "rule_id": " r2: "}
    provider = provider_for(keep_all, standards=[sloppy])

    result = review(review_input, provider=provider, config=config)

    [standards] = [f for f in result.findings if f.reviewer == "standards"]
    assert standards.rule_id == "R2"


def test_skipped_standards_do_not_hide_that_every_other_specialist_failed(tmp_path, make_repo, diff_of, config):
    repo = make_repo(tmp_path / "repo", {k: v for k, v in BASE.items() if k != "REVIEW.md"}, HEAD)
    provider = FakeProvider({"correctness": RuntimeError("boom"), "security": RuntimeError("bang")})

    with pytest.raises(AllSpecialistsFailed, match="boom"):
        review(ReviewInput(repo_root=repo, diff=diff_of(repo), base_ref="main"), provider=provider, config=config)


# -- failures --------------------------------------------------------------------------

def test_one_failing_specialist_is_recorded_and_the_others_findings_are_kept(review_input, config, keep_all):
    provider = provider_for(keep_all, security=None)
    provider.scripts["security"] = RuntimeError("gateway timeout")

    result = review(review_input, provider=provider, config=config)

    assert {f.reviewer for f in result.findings} == {"correctness", "standards"}
    sec = result.stats["specialists"]["security"]
    assert sec["status"] == "failed" and "gateway timeout" in sec["error"]
    assert result.stats["specialist_failures"] == {"security": "gateway timeout"}
    assert result.stats["specialists"]["correctness"]["status"] == "ok"
    md = render_markdown(result)
    assert "Specialist security: FAILED (gateway timeout)" in md
    assert md.startswith("# AI Review: ")  # the review is still produced


def test_a_specialist_that_returns_garbage_twice_fails_alone(review_input, config, keep_all):
    provider = provider_for(keep_all)
    provider.scripts["standards"] = {"findings": "nope"}

    result = review(review_input, provider=provider, config=config)

    assert result.stats["specialists"]["standards"]["status"] == "failed"
    assert {f.reviewer for f in result.findings} == {"correctness", "security"}


def test_every_specialist_failing_is_an_error_naming_each_failure(review_input, config):
    provider = FakeProvider({r: RuntimeError(f"{r} down") for r in ("correctness", "security", "standards")})

    with pytest.raises(AllSpecialistsFailed) as err:
        review(review_input, provider=provider, config=config)

    for role in ("correctness", "security", "standards"):
        assert f"{role}: {role} down" in str(err.value)


# -- parallelism -----------------------------------------------------------------------

class Probe:
    """Wraps a provider, holding each chat call for a moment and recording the peak concurrency."""

    def __init__(self, inner):
        self.inner, self.active, self.peak, self.lock = inner, 0, 0, threading.Lock()

    def chat_json(self, **kw):
        specialist = kw["role"] in ("correctness", "security", "standards")
        if specialist:
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.15)
        try:
            return self.inner.chat_json(**kw)
        finally:
            if specialist:
                with self.lock:
                    self.active -= 1

    def embed(self, **kw):
        return self.inner.embed(**kw)


@pytest.mark.parametrize("workers, peak", [(1, 1), (2, 2), (4, 3)])
def test_specialists_run_in_parallel_up_to_the_configured_worker_count(review_input, config, keep_all, workers, peak):
    config.pipeline.max_workers = workers
    probe = Probe(provider_for(keep_all))

    result = review(review_input, provider=probe, config=config)

    assert probe.peak == peak
    assert len(result.findings) == 3


# -- anchoring (D2, D25) ---------------------------------------------------------------

@pytest.mark.parametrize("role", ["correctness", "security", "standards"])
def test_every_specialist_prompt_states_the_anchoring_rule(review_input, config, keep_all, role):
    provider = provider_for(keep_all, correctness=[], security=[], standards=[])

    review(review_input, provider=provider, config=config)

    system = provider.calls_for(role)[0].system.lower()
    assert "new-side" in system or "new version" in system  # the number gutter is for the new side
    assert "inside the diff" in system
    assert "caller" in system and "explanation" in system  # cross-file breakage goes in the text
    assert "never" in system and "outside the diff" in system


@pytest.mark.parametrize("role, key", [("correctness", CORRECT), ("security", SECURE), ("standards", STANDARD)])
def test_a_finding_anchored_outside_the_diff_is_dropped_and_counted_for_every_specialist(
    review_input, config, keep_all, role, key
):
    unchanged_caller = {**key, "file": "app/invoices.py", "line": 54, "title": "Broken caller"}
    past_the_hunk = {**key, "line": 99, "title": "Past the end"}
    provider = provider_for(keep_all, **{role: [unchanged_caller, past_the_hunk, key]})

    result = review(review_input, provider=provider, config=config)

    assert [f.title for f in result.findings if f.reviewer == role] == [key["title"]]
    assert result.stats["specialists"][role]["dropped_invalid_anchor"] == 2
    assert "(dropped: 2 anchored outside the diff)" in render_markdown(result)


# -- stats and footer ------------------------------------------------------------------

def test_per_stage_stats_cover_time_tokens_cost_and_counts(review_input, config, keep_all):
    result = review(review_input, provider=provider_for(keep_all), config=config)
    stats = result.stats

    for name in ("correctness", "security", "standards"):
        s = stats["specialists"][name]
        assert s["wall_s"] >= 0 and s["input_tokens"] > 0 and s["output_tokens"] > 0
        assert s["cost_usd"] == pytest.approx(config.cost_usd(
            s["model"], input_tokens=s["input_tokens"], output_tokens=s["output_tokens"]))
        assert (s["raw_findings"], s["kept_findings"]) == (1, 1)
    v = stats["stages"]["verifier"]
    assert v["input_tokens"] > 0 and v["cost_usd"] > 0 and v["findings_in"] == 3
    parts = [stats["specialists"][n] for n in ("correctness", "security", "standards")] + [v]
    totals = stats["totals"]
    assert totals["input_tokens"] == sum(p["input_tokens"] for p in parts)
    assert totals["output_tokens"] == sum(p["output_tokens"] for p in parts)
    assert totals["cost_usd"] == pytest.approx(sum(p["cost_usd"] for p in parts))
    assert stats["counts"] == {"raw": 3, "after_verifier": 3, "after_triage": 3}


def test_the_footer_is_a_collapsed_block_with_one_line_per_stage(review_input, config, keep_all):
    result = review(review_input, provider=provider_for(keep_all), config=config)

    md = render_markdown(result)

    footer = md[md.index("<details>"):]
    assert footer.rstrip().endswith("</details>") and "<summary>" in footer
    assert footer.count("<details>") == 1
    for role in ("correctness", "security", "standards"):
        line = next(ln for ln in footer.splitlines() if ln.startswith(f"- Specialist {role}:"))
        assert "1 raw → 1 kept" in line and " in / " in line and " out" in line and "$" in line and "s," in line
    verifier = next(ln for ln in footer.splitlines() if ln.startswith("- Verifier:"))
    assert "3 in → 3 kept" in verifier and " in / " in verifier and "$" in verifier
    assert "- Triage: 3 → 3 kept" in footer
    assert "- Findings: 3 raw → 3 kept" in footer
    assert "(cached)" not in footer
    # stages appear in pipeline order
    order = [footer.index(f"- Specialist {r}") for r in ("correctness", "security", "standards")]
    assert order == sorted(order) and order[-1] < footer.index("- Verifier:") < footer.index("- Triage:")


def test_cached_stages_are_marked_and_a_partly_cached_run_says_so(tmp_path, review_input, config, keep_all):
    config.cache.enabled = True

    def cached(inner):
        return CachedProvider(inner, tmp_path / "cache")

    # First run: security is down, so only the other stages' answers reach the cache.
    first_inner = provider_for(keep_all)
    first_inner.scripts["security"] = RuntimeError("down")
    first = review(review_input, provider=cached(first_inner), config=config)
    assert "(cached)" not in render_markdown(first)

    second = review(review_input, provider=cached(provider_for(keep_all)), config=config)
    stats = second.stats
    assert stats["specialists"]["correctness"]["cached"] is True
    assert stats["specialists"]["security"]["cached"] is False
    assert stats["totals"]["cached"] is False
    footer = render_markdown(second)
    correctness = next(ln for ln in footer.splitlines() if ln.startswith("- Specialist correctness:"))
    security = next(ln for ln in footer.splitlines() if ln.startswith("- Specialist security:"))
    assert correctness.endswith("(cached)") and "(cached)" not in security
    assert "partly cached" in footer

    third = review(review_input, provider=cached(provider_for(keep_all)), config=config)
    footer = render_markdown(third)
    assert third.stats["totals"]["cached"] is True
    assert "Latency:" in footer and "(cached)" in next(ln for ln in footer.splitlines() if "Latency:" in ln)
    for ln in footer.splitlines():
        if ln.startswith(("- Specialist", "- Verifier")):
            assert ln.endswith("(cached)"), ln
