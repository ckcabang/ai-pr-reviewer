"""GitHub boundary: PR metadata, reviewing the PR head, posting one review.

Everything that talks to GitHub lives here, over `httpx2` so tests can fake it with
`httpx2.MockTransport` (D22). PR title and body are only ever read from the REST API
into Python strings; they never pass through a shell (D15).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx2

from reviewer.diff import parse_diff
from reviewer.gitutil import GitError, git
from reviewer.models import ReviewResult
from reviewer.render import render_inline_comment, render_review_body

log = logging.getLogger("reviewer")

TOKEN_ENV = "GITHUB_TOKEN"
_PR_PATH = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)/?")


class GitHubError(Exception):
    """A problem talking to GitHub, fetching the PR's commits, or with the PR reference.
    Reported as `error: ...`. `status` is the HTTP status for API failures."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class PullRef:
    host: str
    owner: str
    repo: str
    number: int

    @property
    def api_url(self) -> str:
        """github.com uses api.github.com; GitHub Enterprise serves the API under /api/v3."""
        return "https://api.github.com" if self.host == "github.com" else f"https://{self.host}/api/v3"

    @property
    def pull_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}/pulls/{self.number}"


@dataclass(frozen=True)
class PullRequest:
    title: str
    body: str
    base_sha: str
    head_sha: str


def parse_pr_url(url: str) -> PullRef:
    parsed = urlparse(url.strip())
    match = _PR_PATH.match(parsed.path)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or not match:
        raise GitHubError(f"not a pull request URL (expected https://HOST/OWNER/REPO/pull/N): {url}")
    return PullRef(parsed.hostname, match.group(1), match.group(2), int(match.group(3)))


def token_from_env(env: dict[str, str] | None = None) -> str:
    token = (os.environ if env is None else env).get(TOKEN_ENV, "").strip()
    if not token:
        raise GitHubError(f"{TOKEN_ENV} is not set (needed to read and review pull requests)")
    return token


class GitHubClient:
    """The two REST calls PR mode needs, for one pull request."""

    def __init__(self, ref: PullRef, token: str, *, transport: httpx2.BaseTransport | None = None,
                 timeout_s: float = 30.0):
        self.ref = ref
        self._http = httpx2.Client(
            base_url=ref.api_url,
            transport=transport,
            timeout=httpx2.Timeout(timeout_s),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-pr-reviewer",
            },
        )

    def get_pull(self) -> PullRequest:
        data = self._request("GET", self.ref.pull_path).json()
        try:
            return PullRequest(
                title=data["title"] or "",
                body=data.get("body") or "",
                base_sha=data["base"]["sha"],
                head_sha=data["head"]["sha"],
            )
        except (KeyError, TypeError) as e:
            raise GitHubError(f"unexpected pull request response from GitHub (missing {e})") from e

    def create_review(self, payload: dict) -> str:
        """Submit a PR review; returns its web URL."""
        response = self._request("POST", f"{self.ref.pull_path}/reviews", json=payload)
        try:
            return response.json().get("html_url", "")
        except ValueError:
            return ""

    def _request(self, method: str, path: str, **kwargs) -> httpx2.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx2.HTTPError as e:
            raise GitHubError(f"cannot reach GitHub ({self.ref.api_url}): {e}") from e
        if response.status_code >= 400:
            raise GitHubError(_describe_failure(method, path, response), status=response.status_code)
        return response


def _describe_failure(method: str, path: str, response: httpx2.Response) -> str:
    try:
        message = response.json().get("message", "")
    except (ValueError, AttributeError):
        message = ""
    hint = ""
    if response.status_code in (401, 403, 404):
        hint = f" (check that {TOKEN_ENV} is valid and can access the repository)"
    return f"GitHub {method} {path} failed: HTTP {response.status_code} {message}".rstrip() + hint


# --- posting the review -------------------------------------------------------------------

def build_review_payload(result: ReviewResult, diff: str, head_sha: str) -> dict:
    """The body of `POST .../pulls/N/reviews`: one `COMMENT` review on the head commit.

    Findings that survived triage go inline (RIGHT side) when their line is commentable in
    `diff`, and are spelled out in the summary otherwise. Never REQUEST_CHANGES or APPROVE."""
    commentable = {f.path: set(f.commentable_lines) for f in parse_diff(diff)}
    posted = result.in_tier("action_required") + result.in_tier("review_recommended")
    inline = [f for f in posted if f.line in commentable.get(f.file, ())]
    return {
        "commit_id": head_sha,
        "body": render_review_body(result, {f.id for f in inline}),
        "event": "COMMENT",
        "comments": [
            {"path": f.file, "line": f.line, "side": "RIGHT", "body": render_inline_comment(f)}
            for f in inline
        ],
    }


def post_review(client: GitHubClient, result: ReviewResult, diff: str, head_sha: str) -> str:
    """Post `result` as one PR review and return its URL.

    If GitHub rejects the review (HTTP 422, e.g. its idea of the diff differs from ours), it is
    submitted once more with every finding in the summary and no inline comments."""
    payload = build_review_payload(result, diff, head_sha)
    try:
        return client.create_review(payload)
    except GitHubError as e:
        if e.status != 422 or not payload["comments"]:
            raise
        log.warning("GitHub rejected the inline comments (%s); posting the findings in the summary", e)
    summary_only = build_review_payload(result, "", head_sha)  # an empty diff has no commentable lines
    return client.create_review(summary_only)


# --- reviewing the PR head ----------------------------------------------------------------

def _has_commit(repo: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode == 0


def _ensure_commits(repo: Path, ref: PullRef, pull: PullRequest) -> None:
    """Make sure the PR's head and base commits exist locally, fetching them if not."""
    if not _has_commit(repo, pull.head_sha):
        spec = f"pull/{ref.number}/head"
        try:
            git(repo, "fetch", "--no-tags", "origin", spec)
        except GitError as e:
            raise GitHubError(f"PR head {pull.head_sha[:12]} is not in the repository and "
                              f"`git fetch origin {spec}` failed: {e}") from e
        if not _has_commit(repo, pull.head_sha):
            raise GitHubError(f"PR head {pull.head_sha[:12]} not found after `git fetch origin {spec}` "
                              "(did the PR get new commits?)")
    if not _has_commit(repo, pull.base_sha):
        try:
            git(repo, "fetch", "--no-tags", "origin", pull.base_sha)
        except GitError as e:
            raise GitHubError(f"PR base {pull.base_sha[:12]} is not in the repository and cannot be "
                              f"fetched (use a full-history checkout): {e}") from e


@contextmanager
def pull_head_checkout(repo_root: Path, ref: PullRef, pull: PullRequest) -> Iterator[Path]:
    """Yield a directory with the PR head checked out (D15).

    `repo_root` itself when its HEAD is already the head SHA; otherwise a temporary git
    worktree at the head SHA, removed on exit even if the review raised."""
    _ensure_commits(repo_root, ref, pull)
    if git(repo_root, "rev-parse", "HEAD").strip() == pull.head_sha:
        yield repo_root
        return
    tmp = Path(tempfile.mkdtemp(prefix="reviewer-pr-"))
    worktree = tmp / "worktree"
    try:
        git(repo_root, "worktree", "add", "--detach", str(worktree), pull.head_sha)
        yield worktree
    finally:
        subprocess.run(["git", "-C", str(repo_root), "worktree", "remove", "--force", str(worktree)],
                       capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run(["git", "-C", str(repo_root), "worktree", "prune"], capture_output=True)
