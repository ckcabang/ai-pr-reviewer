"""Turn a dataset PR into a real git repo: demo service as the base commit, the patch as the head commit.

The repo root is the contents of `demo_service/` (so `REVIEW.md`, `app/`, `tests/` at the top level),
`main` is the base, branch `<pr id>` is the head, and the head is checked out. Commits use a fixed
author and date, so SHAs are deterministic: the base SHA is the same for every PR.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .loader import DatasetError, DatasetPR, load_pr

DEMO_SERVICE = Path(__file__).resolve().parents[2] / "demo_service"
BASE_BRANCH = "main"
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.db", ".venv", ".git")
_IDENT = {
    "GIT_AUTHOR_NAME": "Demo Dev", "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "Demo Dev", "GIT_COMMITTER_EMAIL": "dev@example.com",
    "GIT_AUTHOR_DATE": "2026-01-05T12:00:00+0000", "GIT_COMMITTER_DATE": "2026-01-05T12:00:00+0000",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


@dataclass(frozen=True)
class Materialized:
    repo: Path  # working tree, checked out at head_ref
    base_sha: str
    head_sha: str
    base_ref: str  # branch at the base commit ("main")
    head_ref: str  # branch at the head commit (the PR id, e.g. "pr09")
    pr: DatasetPR


def _git(repo: Path, *args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True,
        env={**os.environ, **_IDENT},
    )
    if proc.returncode:
        raise DatasetError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout


def _prepare_dest(dest: Path | str | None, prefix: str) -> Path:
    if dest is None:
        return Path(tempfile.mkdtemp(prefix=f"{prefix}-"))
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise DatasetError(f"{dest} is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def materialize_base(dest: Path | str | None = None) -> str:
    """Create a repo at `dest` holding only the base commit on `main`; return the base SHA.

    Handy for authoring a new dataset PR: edit files there, then `git diff` is the patch.
    """
    dest = _prepare_dest(dest, "demo-base")
    shutil.copytree(DEMO_SERVICE, dest, ignore=_IGNORE, dirs_exist_ok=True)
    _git(dest, "init", "-q", "-b", BASE_BRANCH)
    _git(dest, "add", "-A")
    _git(dest, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Demo service base state")
    return _git(dest, "rev-parse", "HEAD").strip()


def materialize(pr: DatasetPR | str, dest: Path | str | None = None) -> Materialized:
    """Build the base + head repo for a dataset PR (an object or an id like "pr09").

    `dest` must be missing or empty; with `dest=None` a fresh temp dir is created (caller cleans up).
    Raises DatasetError if the patch does not apply.
    """
    if isinstance(pr, str):
        pr = load_pr(pr)
    repo = _prepare_dest(dest, pr.id)
    base_sha = materialize_base(repo)
    check = subprocess.run(
        ["git", "-C", str(repo), "apply", "--whitespace=nowarn", "-"],
        input=pr.patch, capture_output=True, text=True, env={**os.environ, **_IDENT},
    )
    if check.returncode:
        raise DatasetError(f"{pr.id}: change.patch does not apply to the demo service: {check.stderr.strip()}")
    _git(repo, "checkout", "-q", "-b", pr.id)
    _git(repo, "add", "-A")
    message = f"{pr.title}\n\n{pr.description}\n"
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
    head_sha = _git(repo, "rev-parse", "HEAD").strip()
    return Materialized(repo, base_sha, head_sha, BASE_BRANCH, pr.id, pr)
