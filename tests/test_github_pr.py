"""PR mode (`review --pr URL [--post]`), tested at the GitHub HTTP boundary (Seam 3).

GitHub is faked at the transport level with `httpx2.MockTransport`; the repos are real
temporary git repos and the model is the fake provider. Nothing here touches the network.
"""
import json
import os
import subprocess

import httpx2
import pytest
import yaml

from reviewer.cli import main
from reviewer.config import default_config_path
from reviewer.fake import FakeProvider
from reviewer.github import GitHubClient, parse_pr_url, post_review
from reviewer.models import ReviewResult, VerifiedFinding

PR_URL = "https://github.com/acme/shop/pull/7"

FINDING = {
    "file": "a.py", "line": 2, "category": "logic", "severity": "high", "confidence": 0.9,
    "title": "Overwrites x", "explanation": "x is clobbered.", "evidence": "x = 2",
}


class FakeGitHub:
    """Scripted GitHub REST API for one PR. Records every request."""

    def __init__(self, *, head_sha, base_sha, title="Fix x", body="Fixes the overwrite bug.",
                 review_status=200):
        self.pull = {
            "title": title, "body": body,
            "base": {"sha": base_sha, "ref": "main"}, "head": {"sha": head_sha, "ref": "feature"},
        }
        self.review_status = review_status
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/repos/acme/shop/pulls/7":
            return httpx2.Response(200, json=self.pull)
        if request.method == "POST" and path == "/repos/acme/shop/pulls/7/reviews":
            if self.review_status != 200:
                return httpx2.Response(self.review_status, json={"message": "Unprocessable Entity"})
            return httpx2.Response(200, json={"id": 1, "html_url": f"{PR_URL}#pullrequestreview-1"})
        return httpx2.Response(404, json={"message": "Not Found"})

    @property
    def transport(self):
        return httpx2.MockTransport(self)

    def posts(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.method == "POST"]


def sha(repo, ref="HEAD"):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", ref], capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, make_repo):
    """main = base, feature = head (checked out), one added line a.py:2."""
    return make_repo(tmp_path / "repo", {"a.py": "x = 1\ny = 1\n"}, {"a.py": "x = 1\nx = 2\n"})


@pytest.fixture
def github(repo):
    return FakeGitHub(head_sha=sha(repo, "feature"), base_sha=sha(repo, "main"))


@pytest.fixture
def config_path(tmp_path):
    raw = yaml.safe_load(default_config_path().read_text())
    raw["cache"] = {"enabled": False, "dir": str(tmp_path / "cache")}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    return str(path)


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_secret")


def fake():
    return FakeProvider({"correctness": {"findings": [FINDING]}})


def run_pr(repo, config_path, github, *args, provider=None, url=PR_URL):
    return main(["review", "--repo", str(repo), "--pr", url, "--config", config_path, *args],
                provider=provider or fake(), transport=github.transport)


def test_pr_mode_reviews_the_pr_head_with_title_and_body_from_the_api(repo, config_path, github, capsys):
    provider = fake()
    assert run_pr(repo, config_path, github, provider=provider) == 0

    [get] = github.requests
    assert get.method == "GET" and str(get.url) == "https://api.github.com/repos/acme/shop/pulls/7"
    assert get.headers["authorization"] == "Bearer ghs_secret"

    [call] = provider.calls_for("correctness")
    assert "Fix x" in call.user and "Fixes the overwrite bug." in call.user
    assert "a.py:2" in capsys.readouterr().out


def test_pr_mode_reads_the_team_rules_from_the_base_not_from_the_pr_head(tmp_path, make_repo, config_path):
    repo = make_repo(
        tmp_path / "rules_repo",
        {"a.py": "x = 1\ny = 1\n", "REVIEW.md": "## R1: Real rule\nKeep it.\n\n# Do not flag\n- Typos\n"},
        {"a.py": "x = 1\nx = 2\n", "REVIEW.md": "## R1: Weakened rule\nAnything goes.\n"},
    )
    github = FakeGitHub(head_sha=sha(repo, "feature"), base_sha=sha(repo, "main"))
    provider = fake()

    assert run_pr(repo, config_path, github, provider=provider) == 0

    [call] = provider.calls_for("correctness")
    rules = call.user.split("<team_rules>")[1].split("</team_rules>")[0]
    assert "R1: Real rule" in rules and "Weakened" not in rules
    assert "- Typos" in call.user.split("<do_not_flag>")[1]


def test_pr_title_and_body_reach_the_reviewer_verbatim_even_with_shell_syntax(repo, config_path, github, tmp_path):
    marker = tmp_path / "pwned"
    github.pull["title"] = f"Fix `touch {marker}` $(touch {marker}) \"; touch {marker}; #"
    github.pull["body"] = f"$(touch {marker})\n'quoted' & more"
    provider = fake()
    assert run_pr(repo, config_path, github, provider=provider) == 0
    [call] = provider.calls_for("correctness")
    assert github.pull["title"] in call.user and github.pull["body"] in call.user
    assert not marker.exists()


def test_a_pr_with_no_body_is_fine(repo, config_path, github):
    github.pull["body"] = None
    assert run_pr(repo, config_path, github) == 0


def test_missing_token_exits_2_naming_the_variable(repo, config_path, github, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN")
    assert run_pr(repo, config_path, github) == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err
    assert github.requests == []


def test_github_enterprise_urls_use_the_api_v3_endpoint(repo, config_path, github):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx2.Response(200, json=github.pull)

    class Ghe:
        transport = httpx2.MockTransport(handler)

    assert run_pr(repo, config_path, Ghe, url="https://ghe.example.com/acme/shop/pull/7") == 0
    assert seen == ["https://ghe.example.com/api/v3/repos/acme/shop/pulls/7"]


@pytest.mark.parametrize("url", ["", "not a url", "https://github.com/acme/shop", "https://github.com/acme/shop/issues/7"])
def test_a_url_that_is_not_a_pull_request_exits_2(repo, config_path, github, capsys, url):
    assert run_pr(repo, config_path, github, url=url) == 2
    assert "pull request URL" in capsys.readouterr().err
    assert github.requests == []


def test_an_unknown_pr_exits_2_with_the_github_status(repo, config_path, github, capsys):
    assert run_pr(repo, config_path, github, url="https://github.com/acme/shop/pull/999") == 2
    err = capsys.readouterr().err
    assert "404" in err and "ghs_secret" not in err


@pytest.mark.parametrize("extra", [["--title", "x"], ["--description-file", "d.txt"]])
def test_title_and_description_flags_conflict_with_pr_mode(repo, config_path, github, extra):
    with pytest.raises(SystemExit) as e:
        run_pr(repo, config_path, github, *extra)
    assert e.value.code == 2


def test_base_and_pr_are_mutually_exclusive(repo, config_path, github):
    with pytest.raises(SystemExit) as e:
        run_pr(repo, config_path, github, "--base", "main")
    assert e.value.code == 2


# --- in place vs. temporary worktree (D15) ---------------------------------------------------

def git(repo, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return out.stdout


def checkouts(repo):
    """[(path, HEAD sha)] of every checkout of the repo, as `git worktree list` reports them."""
    result, path = [], None
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line.split(" ", 1)[1]
        elif line.startswith("HEAD "):
            result.append((path, line.split(" ", 1)[1]))
    return result


class Watcher:
    """Scripted correctness specialist that records which checkouts exist while it runs."""

    def __init__(self, repo, reply=None):
        self.repo, self.reply, self.seen = repo, reply or {"findings": []}, []

    def __call__(self, call):
        self.seen.append(checkouts(self.repo))
        return self.reply

    @property
    def provider(self):
        return FakeProvider({"correctness": self})

    def extra_checkouts(self):
        [during] = self.seen
        return [c for c in during if c[0] != str(self.repo)]


def test_reviews_in_place_when_the_repo_is_already_at_the_head_sha(repo, config_path, github):
    watcher = Watcher(repo)
    assert run_pr(repo, config_path, github, provider=watcher.provider) == 0
    assert watcher.extra_checkouts() == []


def test_uses_a_temporary_worktree_at_the_head_sha_when_the_repo_is_elsewhere_and_removes_it(
        repo, config_path, github, capsys):
    git(repo, "checkout", "-q", "main")  # the repo is NOT at the PR head
    watcher = Watcher(repo, reply={"findings": [FINDING]})

    assert run_pr(repo, config_path, github, provider=watcher.provider) == 0

    [(path, head)] = watcher.extra_checkouts()
    assert head == github.pull["head"]["sha"]
    assert "a.py:2" in capsys.readouterr().out  # the PR's change was reviewed, not main
    assert len(checkouts(repo)) == 1 and not os.path.exists(path)  # removed afterwards
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"  # the user's checkout is untouched


def test_the_worktree_is_removed_even_when_the_review_fails(repo, config_path, github, capsys):
    git(repo, "checkout", "-q", "main")
    watcher = Watcher(repo, reply=RuntimeError("boom"))

    assert run_pr(repo, config_path, github, provider=watcher.provider) == 2

    [(path, _)] = watcher.extra_checkouts()
    assert len(checkouts(repo)) == 1 and not os.path.exists(path)
    assert "boom" in capsys.readouterr().err


def test_fetches_the_pr_head_when_the_repo_does_not_have_it(tmp_path, make_repo, config_path, capsys):
    origin = make_repo(tmp_path / "origin", {"a.py": "x = 1\ny = 1\n"}, {"a.py": "x = 1\nx = 2\n"})
    # A fork-style PR: its head is only reachable through refs/pull/7/head, not a branch.
    git(origin, "checkout", "-q", "-b", "tmp")
    (origin / "a.py").write_text("x = 1\nx = 3\n")
    git(origin, "commit", "-qam", "pr head")
    head = git(origin, "rev-parse", "HEAD").strip()
    git(origin, "update-ref", "refs/pull/7/head", head)
    git(origin, "checkout", "-q", "main")
    git(origin, "branch", "-q", "-D", "tmp", "feature")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", f"file://{origin}", str(clone))  # file:// so only reachable objects are copied
    assert subprocess.run(["git", "-C", str(clone), "cat-file", "-e", head]).returncode != 0

    server = FakeGitHub(head_sha=head, base_sha=git(origin, "rev-parse", "main").strip())
    provider = FakeProvider({"correctness": {"findings": [{**FINDING, "title": "Sets x to 3", "evidence": "x = 3"}]}})
    assert run_pr(clone, config_path, server, provider=provider) == 0

    assert "Sets x to 3" in capsys.readouterr().out
    assert len(checkouts(clone)) == 1


def test_an_unreachable_pr_head_is_a_clear_error(repo, config_path, github, capsys):
    github.pull["head"]["sha"] = "0" * 40
    assert run_pr(repo, config_path, github) == 2
    assert "pull/7/head" in capsys.readouterr().err


# --- posting the review (--post) ---------------------------------------------------------------

def test_without_post_nothing_is_written_to_github(repo, config_path, github):
    assert run_pr(repo, config_path, github) == 0
    assert github.posts() == []


def test_post_submits_exactly_one_comment_review_for_the_head_commit_with_inline_comments(
        repo, config_path, github, capsys, keep_all):
    provider = FakeProvider({"correctness": {"findings": [FINDING]}, "verifier": keep_all})
    assert run_pr(repo, config_path, github, "--post", provider=provider) == 0

    [review] = github.posts()
    assert set(review) == {"commit_id", "body", "event", "comments"}
    assert review["event"] == "COMMENT"
    assert review["commit_id"] == github.pull["head"]["sha"]
    assert review["body"].startswith("# AI Review: 1 action required · 0 recommended")
    [comment] = review["comments"]
    assert (comment["path"], comment["line"], comment["side"]) == ("a.py", 2, "RIGHT")
    assert "Overwrites x" in comment["body"] and "x is clobbered." in comment["body"]
    assert f"{PR_URL}#pullrequestreview-1" in capsys.readouterr().err


def test_an_inline_finding_is_not_repeated_in_full_in_the_summary(repo, config_path, github):
    run_pr(repo, config_path, github, "--post")
    [review] = github.posts()
    assert "x is clobbered." not in review["body"]  # the explanation lives in the inline comment
    assert "`a.py:2`" in review["body"] and "Overwrites x" in review["body"]  # but the summary points to it


def test_the_review_never_requests_changes_or_approves_even_with_action_required_findings(bare_github):
    result = ReviewResult(findings=[verified(triage="action_required", severity="critical")], stats={})
    post_review(client_for(bare_github), result, DIFF, "h" * 40)
    [review] = bare_github.posts()
    assert review["event"] == "COMMENT"


def test_no_findings_still_posts_one_summary_review(repo, config_path, github):
    provider = FakeProvider({"correctness": {"findings": []}})
    assert run_pr(repo, config_path, github, "--post", provider=provider) == 0
    [review] = github.posts()
    assert review["comments"] == [] and "No findings" in review["body"]


def test_a_failed_post_exits_2_but_the_review_is_still_printed(repo, config_path, github, capsys):
    github.review_status = 403
    assert run_pr(repo, config_path, github, "--post") == 2
    captured = capsys.readouterr()
    assert "a.py:2" in captured.out and "403" in captured.err and "ghs_secret" not in captured.err


def test_post_without_pr_is_a_usage_error(repo, config_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["review", "--repo", str(repo), "--base", "main", "--config", config_path, "--post"],
             provider=fake())
    assert e.value.code == 2


# --- the inline vs. summary split ---------------------------------------------------------------

BASE_LINES = [f"v{i} = {i}\n" for i in range(1, 31)]
HEAD_LINES = [("v15 = 'changed'\n" if i == 15 else line) for i, line in enumerate(BASE_LINES, start=1)]
DIFF = (
    "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -12,7 +12,7 @@\n"
    + "".join(f" {line}" for line in BASE_LINES[11:14]) + f"-{BASE_LINES[14]}+{HEAD_LINES[14]}"
    + "".join(f" {line}" for line in BASE_LINES[15:18])
)  # commentable new-side lines: 12..18


def verified(*, file="m.py", line=15, title="Bad value", triage="review_recommended", severity="high"):
    return VerifiedFinding(
        id=f"{file}{line}{title}", reviewer="correctness", file=file, line=line, category="logic",
        severity=severity, confidence=0.9, title=title, explanation=f"Because {title}.",
        evidence="v15 = 'changed'", triage=triage,
    )


def client_for(github):
    return GitHubClient(parse_pr_url(PR_URL), "ghs_secret", transport=github.transport)


@pytest.fixture
def bare_github():
    return FakeGitHub(head_sha="h" * 40, base_sha="b" * 40)


def test_findings_on_commentable_lines_go_inline_and_all_others_in_the_summary(bare_github):
    result = ReviewResult(stats={}, findings=[
        verified(line=15, title="On the changed line"),
        verified(line=12, title="On a context line"),
        verified(line=25, title="Outside every hunk"),
        verified(file="other.py", line=3, title="In a file the diff does not touch"),
        verified(line=15, title="Dropped by triage", triage="dropped"),
    ])

    post_review(client_for(bare_github), result, DIFF, "h" * 40)

    [review] = bare_github.posts()
    assert [(c["path"], c["line"], c["side"]) for c in review["comments"]] == [("m.py", 15, "RIGHT"), ("m.py", 12, "RIGHT")]
    assert "On the changed line" in review["comments"][0]["body"]
    body = review["body"]
    assert "Because Outside every hunk." in body and "Because In a file the diff does not touch." in body
    assert "Because On the changed line." not in body  # inline findings are not repeated in full
    assert "Dropped by triage" not in body and all("Dropped by triage" not in c["body"] for c in review["comments"])


def test_a_review_github_rejects_is_retried_once_with_every_finding_in_the_summary(bare_github):
    answers = iter([httpx2.Response(422, json={"message": "Unprocessable Entity"}),
                    httpx2.Response(200, json={"html_url": "https://example/review"})])
    posts = []

    def handler(request):
        posts.append(json.loads(request.content))
        return next(answers)

    client = GitHubClient(parse_pr_url(PR_URL), "t", transport=httpx2.MockTransport(handler))
    result = ReviewResult(stats={}, findings=[verified(line=15)])

    assert post_review(client, result, DIFF, "h" * 40) == "https://example/review"

    assert len(posts) == 2 and len(posts[0]["comments"]) == 1
    assert posts[1]["comments"] == [] and posts[1]["event"] == "COMMENT"
    assert "Because Bad value." in posts[1]["body"]  # nothing is lost
