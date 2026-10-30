"""Seam: the dataset loader + materializer (evals.dataset)."""
import subprocess

import pytest
import yaml

from evals.dataset import DatasetError, load_all, load_pr, materialize, materialize_base
from evals.dataset.integrity import check_pr
from evals.dataset.materialize import DEMO_SERVICE

PATCH_TARGET = "REVIEW.md"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def make_dataset(tmp_path, *, pr_id="pr90", issues=None, patch=None, meta=None, edit=None):
    """Write a one-PR dataset under tmp_path/prs and return that root.

    The patch is `git diff` of `edit(scratch_repo)`; by default it appends two lines to REVIEW.md.
    """
    if patch is None:
        scratch = tmp_path / f"scratch_{pr_id}"
        materialize_base(scratch)
        if edit is None:
            review = scratch / PATCH_TARGET
            review.write_text(review.read_text() + "\nExtra line.\n")
        else:
            edit(scratch)
        patch = git(scratch, "diff")
    d = tmp_path / "prs" / f"{pr_id}_example"
    d.mkdir(parents=True)
    (d / "change.patch").write_text(patch)
    (d / "meta.yaml").write_text(yaml.safe_dump(meta or {"id": pr_id, "title": "Example change", "description": "Adds a line."}))
    (d / "labels.yaml").write_text(yaml.safe_dump({"issues": issues or []}))
    return tmp_path / "prs"


ISSUE = {
    "id": "pr90-1", "file": PATCH_TARGET, "line_hint": 3, "area": "correctness", "severity": "high",
    "requires": "diff", "description": "Something is wrong.",
}


def test_materialize_builds_base_and_head_commits(tmp_path):
    root = make_dataset(tmp_path)
    m = materialize(load_pr("pr90", root), tmp_path / "repo")
    assert git(m.repo, "rev-parse", m.base_ref).strip() == m.base_sha
    assert git(m.repo, "rev-parse", "HEAD").strip() == m.head_sha
    assert git(m.repo, "rev-parse", f"{m.head_sha}^").strip() == m.base_sha
    assert git(m.repo, "diff", "--name-only", m.base_sha, m.head_sha).split() == [PATCH_TARGET]
    assert git(m.repo, "status", "--porcelain") == ""
    assert (m.repo / "app" / "main.py").exists() and (m.repo / "REVIEW.md").exists()
    assert not list(m.repo.rglob("__pycache__"))


def test_materialize_is_deterministic_and_base_is_shared(tmp_path):
    root = make_dataset(tmp_path)
    pr = load_pr("pr90", root)
    a, b = materialize(pr, tmp_path / "a"), materialize(pr, tmp_path / "b")
    assert (a.base_sha, a.head_sha) == (b.base_sha, b.head_sha)
    assert materialize_base(tmp_path / "c") == a.base_sha


def test_materialize_without_dest_makes_a_temp_repo(tmp_path):
    m = materialize(load_pr("pr90", make_dataset(tmp_path)))
    assert (m.repo / ".git").exists()


def test_materialize_rejects_nonempty_dest(tmp_path):
    pr = load_pr("pr90", make_dataset(tmp_path))
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    with pytest.raises(DatasetError, match="not empty"):
        materialize(pr, tmp_path / "full")


def test_patch_that_does_not_apply_is_reported(tmp_path):
    bad = "--- a/REVIEW.md\n+++ b/REVIEW.md\n@@ -1,1 +1,1 @@\n-no such line\n+replacement\n"
    pr = load_pr("pr90", make_dataset(tmp_path, patch=bad))
    with pytest.raises(DatasetError, match="does not apply"):
        materialize(pr, tmp_path / "repo")


def test_load_parses_expected_issues_with_related(tmp_path):
    issue = dict(ISSUE, rule_id="R3", related=[{"file": "app/main.py", "line_hint": 5}])
    pr = load_pr("pr90_example", make_dataset(tmp_path, issues=[issue]))
    (e,) = pr.expected_issues
    assert (e.id, e.area, e.requires, e.rule_id) == ("pr90-1", "correctness", "diff", "R3")
    assert [(r.file, r.line_hint) for r in e.related] == [("app/main.py", 5)]
    assert pr.title == "Example change" and not pr.is_clean


def test_clean_pr_has_no_expected_issues(tmp_path):
    assert load_pr("pr90", make_dataset(tmp_path)).is_clean


@pytest.mark.parametrize(
    "change, message",
    [
        ({"requires": "vibes"}, "requires"),
        ({"area": "category"}, "area"),
        ({"severity": "urgent"}, "severity"),
        ({"id": "other-1"}, "id"),
        ({"line_hint": 0}, "line_hint"),
        ({"rule_id": "rule-3"}, "rule_id"),
    ],
)
def test_invalid_labels_are_rejected(tmp_path, change, message):
    root = make_dataset(tmp_path, issues=[dict(ISSUE, **change)])
    with pytest.raises(DatasetError, match=message):
        load_pr("pr90", root)


def test_duplicate_issue_ids_are_rejected(tmp_path):
    root = make_dataset(tmp_path, issues=[ISSUE, ISSUE])
    with pytest.raises(DatasetError, match="duplicate"):
        load_pr("pr90", root)


def test_meta_id_must_match_directory(tmp_path):
    root = make_dataset(tmp_path, meta={"id": "pr91", "title": "t", "description": "d"})
    with pytest.raises(DatasetError, match="id"):
        load_pr("pr90", root)


def test_unknown_pr_id(tmp_path):
    with pytest.raises(DatasetError, match="no dataset PR"):
        load_pr("pr99", make_dataset(tmp_path))


def test_load_all_is_sorted_by_id(tmp_path):
    root = make_dataset(tmp_path, pr_id="pr90")
    other = make_dataset(tmp_path / "x", pr_id="pr80")
    (other / "pr80_example").rename(root / "pr80_example")
    assert [p.id for p in load_all(root)] == ["pr80", "pr90"]


# --- integrity check -------------------------------------------------------------------------

LAST_LINE = len((DEMO_SERVICE / PATCH_TARGET).read_text().splitlines()) + 2  # the two appended lines
GOOD = dict(ISSUE, line_hint=LAST_LINE)


def problems(tmp_path, **kwargs):
    return check_pr(load_pr("pr90", make_dataset(tmp_path, **kwargs)))


def test_well_formed_pr_has_no_problems(tmp_path):
    assert problems(tmp_path, issues=[GOOD]) == []


def test_anchor_must_be_inside_the_diff(tmp_path):
    (p,) = problems(tmp_path, issues=[dict(GOOD, line_hint=1)])
    assert "pr90-1" in p and "not inside the diff" in p


def test_anchor_file_must_be_changed_by_the_patch(tmp_path):
    (p,) = problems(tmp_path, issues=[dict(GOOD, file="app/main.py", line_hint=5)])
    assert "pr90-1" in p and "not changed by the patch" in p


def test_related_location_must_exist(tmp_path):
    issue = dict(GOOD, related=[{"file": "app/nope.py", "line_hint": 3}, {"file": "app/main.py", "line_hint": 9999}])
    assert len(problems(tmp_path, issues=[issue])) == 2


def test_unapplicable_patch_is_a_problem(tmp_path):
    bad = "--- a/REVIEW.md\n+++ b/REVIEW.md\n@@ -1,1 +1,1 @@\n-no such line\n+replacement\n"
    (p,) = problems(tmp_path, patch=bad)
    assert "does not apply" in p


def test_patched_app_must_import(tmp_path):
    def edit(repo):
        f = repo / "app/services/pricing.py"
        f.write_text(f.read_text() + "\nimport module_that_does_not_exist\n")

    (p,) = problems(tmp_path, edit=edit)
    assert "import" in p and "module_that_does_not_exist" in p


def test_description_must_not_hint(tmp_path):
    meta = {"id": "pr90", "title": "Example", "description": "Fixes a bug that leaks data."}
    (p,) = problems(tmp_path, meta=meta)
    assert "hint" in p


def test_clean_pr_must_keep_service_tests_green(tmp_path):
    def edit(repo):
        f = repo / "app/services/pricing.py"
        f.write_text(f.read_text().replace('HUNDRED = Decimal("100")', 'HUNDRED = Decimal("10")'))

    (p,) = problems(tmp_path, edit=edit)
    assert "service tests fail" in p


# --- the real dataset ------------------------------------------------------------------------

REAL = load_all()


def test_dataset_contains_the_demo_prs():
    by_id = {p.id: p for p in REAL}
    assert len(by_id["pr09"].expected_issues) == 1 and by_id["pr09"].expected_issues[0].requires == "cross_file"
    assert by_id["pr06"].is_clean


@pytest.mark.parametrize("pr", REAL, ids=[p.id for p in REAL])
def test_dataset_pr_is_sound(pr):
    assert check_pr(pr) == []
