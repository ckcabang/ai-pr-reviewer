"""The one place the reviewer shells out to git (for the CLI, the GitHub boundary and the eval)."""
from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    """A git command failed (or git is not installed). The message is git's own stderr."""


def git(repo: Path | str, *args: str) -> str:
    """Run `git -C repo ARGS` and return stdout; raise `GitError` on a non-zero exit."""
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise GitError("git is not installed or not on PATH") from e
    if out.returncode != 0:
        raise GitError(out.stderr.strip() or f"git {' '.join(args)} failed")
    return out.stdout


def git_diff(repo_root: Path, base: str, head: str = "HEAD") -> str:
    """The unified diff `base...head`, in the form the diff parser expects."""
    # Explicit prefixes/flags so the user's git config (diff.noprefix, quotepath, external
    # diff tools, rename detection) cannot change what the parser sees.
    return git(repo_root, "-c", "core.quotepath=false", "diff", "--no-color", "--no-ext-diff",
               "--src-prefix=a/", "--dst-prefix=b/", "-M", f"{base}...{head}")
