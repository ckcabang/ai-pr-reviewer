"""Expected-issue isolation: no reviewer code may read the labels (plan 7.2).

Proven two ways: a scan of the reviewer package's source, and a Seam-1 review run in which
opening anything inside the dataset's PR folders (labels.yaml above all) is recorded and fails
the test.
"""
import ast
import builtins
import io
import os
import re
from pathlib import Path

import pytest

import reviewer
from evals.dataset import DATASET_ROOT, load_pr, materialize
from reviewer.agents.baseline import review_baseline
from reviewer.fake import FakeProvider
from reviewer.models import ReviewInput
from reviewer.pipeline import review

REVIEWER_SRC = Path(reviewer.__file__).parent
FORBIDDEN_TEXT = re.compile(r"labels\.ya?ml|expected_issues?|ExpectedIssue|line_hint|evals/dataset|DATASET_ROOT")


def test_no_module_in_the_reviewer_package_mentions_or_imports_expected_issues():
    sources = sorted(REVIEWER_SRC.rglob("*.py"))
    assert len(sources) > 10

    for path in sources:
        text = path.read_text()
        hit = FORBIDDEN_TEXT.search(text)
        assert hit is None, f"{path.relative_to(REVIEWER_SRC)} mentions {hit.group(0)!r}"
        for node in ast.walk(ast.parse(text)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any(n == "evals" or n.startswith("evals.") for n in names), (
                f"{path.relative_to(REVIEWER_SRC)} imports the eval package")


class OpenGuard:
    """Records every attempt to open something inside the dataset folder."""

    def __init__(self, monkeypatch):
        self.root = DATASET_ROOT.resolve()
        self.violations: list[str] = []
        for owner, name in [(builtins, "open"), (io, "open"), (os, "open"), (Path, "open"),
                            (Path, "read_text"), (Path, "read_bytes")]:
            monkeypatch.setattr(owner, name, self._wrap(getattr(owner, name), method=owner is Path))

    def _wrap(self, real, method):
        def guarded(*args, **kwargs):
            target = args[0]
            if target is not None and isinstance(target, (str, bytes, os.PathLike)):
                try:
                    resolved = Path(os.fsdecode(target)).resolve()
                except (OSError, ValueError):
                    resolved = None
                if resolved is not None and (resolved == self.root or self.root in resolved.parents):
                    self.violations.append(str(resolved))
                    raise AssertionError(f"reviewer code opened a dataset file: {resolved}")
            return real(*args, **kwargs)

        return guarded


def test_the_guard_itself_catches_an_open_of_a_labels_file(monkeypatch):
    guard = OpenGuard(monkeypatch)
    labels = next(DATASET_ROOT.glob("*/labels.yaml"))

    for opener in (lambda: open(labels), lambda: labels.read_text(), lambda: io.open(labels),
                   lambda: labels.open(), lambda: os.open(labels, os.O_RDONLY)):
        with pytest.raises(AssertionError, match="dataset file"):
            opener()
    assert len(guard.violations) == 5


@pytest.mark.parametrize("pr_id", ["pr09", "pr06"])
def test_reviewing_a_dataset_pr_never_opens_the_labels_nor_shows_them_to_a_model(pr_id, config, tmp_path, monkeypatch):
    pr = load_pr(pr_id)
    m = materialize(pr, tmp_path / "repo")
    from reviewer.cli import _git_diff

    review_input = ReviewInput(repo_root=m.repo, diff=_git_diff(m.repo, m.base_ref), title=pr.title,
                               description=pr.description)
    empty = {"findings": []}
    provider = FakeProvider({"correctness": empty, "security": empty, "standards": empty, "baseline": empty})

    guard = OpenGuard(monkeypatch)  # armed only after loading and materializing, i.e. for the review itself
    review(review_input, provider=provider, config=config)
    review_baseline(review_input, provider=provider, config=config)
    monkeypatch.undo()

    assert guard.violations == []
    assert len(provider.calls) == 4  # three specialists (the dataset repo has REVIEW.md) + the baseline

    # nothing about the expected issues is in the repo under review or in any prompt
    secrets = [i.description[:40] for i in pr.expected_issues] + [i.id for i in pr.expected_issues]
    assert not list(m.repo.rglob("labels.yaml"))
    repo_text = "\n".join(p.read_text(errors="ignore") for p in m.repo.rglob("*")
                          if p.is_file() and ".git" not in p.parts)
    prompts = "\n".join(c.system + c.user for c in provider.calls)
    for secret in secrets:
        assert secret not in repo_text and secret not in prompts
