"""The demo-repo seed script, offline: git construction and what is (never) pushed (D12)."""
import subprocess

import yaml

from evals.dataset import load_pr, materialize
from evals.seed_github import DEFAULT_PRS, WORKFLOW_PATH, build_seed_repo, workflow_text


def _git(repo, *args):
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _files(repo, ref):
    return _git(repo, "ls-tree", "-r", "--name-only", ref).split()


def test_main_is_the_demo_base_plus_workflow_and_rules_at_the_root(tmp_path):
    seed = build_seed_repo(tmp_path / "demo")
    files = _files(seed.repo, "main")
    assert "REVIEW.md" in files and "app/services/pricing.py" in files
    assert WORKFLOW_PATH in files
    assert _git(seed.repo, "status", "--porcelain").strip() == ""


def test_default_selection_is_pr09_and_pr06_each_one_commit_ahead_of_main(tmp_path):
    assert DEFAULT_PRS == ("pr09", "pr06")
    seed = build_seed_repo(tmp_path / "demo")
    assert set(seed.branches) == {"pr09", "pr06"}
    for pr_id in DEFAULT_PRS:
        assert _git(seed.repo, "rev-list", "--count", f"main..{pr_id}").strip() == "1"
        assert _git(seed.repo, "merge-base", "main", pr_id).strip() == seed.main_sha
        subject = _git(seed.repo, "log", "-1", "--format=%s", pr_id).strip()
        assert subject == load_pr(pr_id).title


def test_branch_content_is_the_dataset_head_plus_the_workflow(tmp_path):
    seed = build_seed_repo(tmp_path / "demo", ["pr09"])
    head = materialize("pr09", tmp_path / "head")
    expected = set(_files(head.repo, "HEAD")) | {WORKFLOW_PATH}
    assert set(_files(seed.repo, "pr09")) == expected
    for path in _files(head.repo, "HEAD"):
        assert _git(seed.repo, "show", f"pr09:{path}") == (head.repo / path).read_text()
    changed = set(_git(seed.repo, "diff", "--name-only", "main", "pr09").split())
    assert "app/services/pricing.py" in changed and WORKFLOW_PATH not in changed


def test_expected_issues_and_labels_are_never_in_any_branch_or_message(tmp_path):
    seed = build_seed_repo(tmp_path / "demo")
    forbidden_files = {"labels.yaml", "meta.yaml", "change.patch"}
    secrets = []
    for pr_id in DEFAULT_PRS:
        for issue in load_pr(pr_id).expected_issues:
            secrets += [issue.description, issue.id]
    assert secrets
    for ref in ["main", *DEFAULT_PRS]:
        names = _files(seed.repo, ref)
        assert not forbidden_files & {n.rsplit("/", 1)[-1] for n in names}
    everything = _git(seed.repo, "log", "--all", "-p", "--format=%B")
    for text in secrets:
        assert text not in everything
    # no other refs (tags, extra branches) that could carry anything else
    refs = _git(seed.repo, "for-each-ref", "--format=%(refname:short)").split()
    assert set(refs) == {"main", *DEFAULT_PRS}


def test_workflow_uses_the_overridable_action_ref(tmp_path):
    text = workflow_text("poc-v1")
    assert "ckcabang/ai-pr-reviewer@poc-v1" in text and "ckcabang/ai-pr-reviewer@main" not in text
    assert "pull_request_target" not in yaml.dump(yaml.safe_load(text))
    seed = build_seed_repo(tmp_path / "demo", ["pr06"], action_ref="poc-v1")
    assert "ai-pr-reviewer@poc-v1" in _git(seed.repo, "show", f"main:{WORKFLOW_PATH}")
    assert "ai-pr-reviewer@main" in workflow_text()
