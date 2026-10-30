"""Shared test helpers: real temporary git repos, the committed config."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from reviewer.config import Config, load_config

_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=_GIT_ENV)
    assert out.returncode == 0, out.stderr
    return out.stdout


def make_repo(root: Path, base: dict[str, str], head: dict[str, str | None]) -> Path:
    """Create a git repo with `base` committed on `main`, then `head` applied and committed
    (a None value deletes the file). Returns the repo path; diff it with `main...HEAD`."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    _write(root, base)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "checkout", "-q", "-b", "feature")
    _write(root, head)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change")
    return root


def _write(root: Path, files: dict[str, str | None]) -> None:
    for name, content in files.items():
        path = root / name
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def diff_of(repo: Path, base: str = "main") -> str:
    return git(repo, "diff", "--no-color", "--src-prefix=a/", "--dst-prefix=b/", "-M", f"{base}...HEAD")


@pytest.fixture
def config() -> Config:
    cfg = load_config()
    cfg.cache.enabled = False
    return cfg
