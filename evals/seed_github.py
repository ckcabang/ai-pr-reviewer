"""Seed the private demo repo with dataset PRs (D12).

    uv run python -m evals.seed_github                          # ckcabang/ai-review-demo, pr09 + pr06
    uv run python -m evals.seed_github --action-ref poc-v1      # point the workflow at another ref
    uv run python -m evals.seed_github --local-only DIR         # build the git repo only, no network

`main` is the base state of `demo_service/` (REVIEW.md at the root) plus the example adopter workflow;
each selected dataset PR becomes a branch named after its id, one commit ahead of `main`, and a PR.
Only the patch's effect is pushed: labels (expected issues), `meta.yaml` and `change.patch` never are.
Needs `git` and an authenticated `gh`. Adding the OPENAI_API_KEY secret to the demo repo is a manual
owner step. Re-running is safe: SHAs are deterministic, an existing PR is left alone; pass `--force` to
overwrite branches after changing e.g. `--action-ref`.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from evals.dataset import DatasetError, load_pr, materialize_base
from evals.dataset.materialize import _git  # fixed author/date, so commits are deterministic

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_WORKFLOW = REPO_ROOT / "examples" / "ai-review.yml"
WORKFLOW_PATH = ".github/workflows/ai-review.yml"
DEFAULT_PRS = ("pr09", "pr06")
DEFAULT_REPO = "ckcabang/ai-review-demo"
ACTION_REPO = "ckcabang/ai-pr-reviewer"


@dataclass(frozen=True)
class SeedRepo:
    repo: Path  # local working tree, `main` checked out
    main_sha: str
    branches: dict[str, str]  # dataset PR id -> head SHA of its branch


def workflow_text(action_ref: str = "main") -> str:
    """The example adopter workflow, with the reviewer action pinned to `action_ref`."""
    text = EXAMPLE_WORKFLOW.read_text()
    out, n = re.subn(rf"(uses:\s*{re.escape(ACTION_REPO)})@\S+", rf"\1@{action_ref}", text)
    if n != 1:
        raise DatasetError(f"{EXAMPLE_WORKFLOW} must reference {ACTION_REPO}@<ref> exactly once")
    return out


def build_seed_repo(dest: Path | str, pr_ids=DEFAULT_PRS, action_ref: str = "main") -> SeedRepo:
    """Build the local git repo (no network): `main` plus one branch per dataset PR id."""
    dest = Path(dest)
    materialize_base(dest)
    workflow = dest / WORKFLOW_PATH
    workflow.parent.mkdir(parents=True)
    workflow.write_text(workflow_text(action_ref))
    _git(dest, "add", WORKFLOW_PATH)
    _git(dest, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Add the AI review workflow")
    main_sha = _git(dest, "rev-parse", "HEAD").strip()
    branches = {}
    for pr_id in pr_ids:
        pr = load_pr(pr_id)
        _git(dest, "checkout", "-q", "-b", pr.id, "main")
        proc = subprocess.run(
            ["git", "-C", str(dest), "apply", "--whitespace=nowarn", "-"],
            input=pr.patch, capture_output=True, text=True,
        )
        if proc.returncode:
            raise DatasetError(f"{pr.id}: change.patch does not apply: {proc.stderr.strip()}")
        _git(dest, "add", "-A")
        _git(dest, "-c", "commit.gpgsign=false", "commit", "-q", "-m", f"{pr.title}\n\n{pr.description}\n")
        branches[pr.id] = _git(dest, "rev-parse", "HEAD").strip()
        _git(dest, "checkout", "-q", "main")
    return SeedRepo(dest, main_sha, branches)


def _run(*cmd: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"error: {' '.join(cmd[:4])} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def publish(seed: SeedRepo, repo: str, *, force: bool = False, push: bool = True) -> list[str]:
    """Create the private repo if missing, push main + PR branches, open any missing PRs."""
    exists = subprocess.run(["gh", "repo", "view", repo], capture_output=True, text=True).returncode == 0
    if not exists:
        _run("gh", "repo", "create", repo, "--private", "--description", "Live demo for ai-pr-reviewer")
    ssh_url = f"git@github.com:{repo}.git"
    remotes = _git(seed.repo, "remote").split()
    _git(seed.repo, *(["remote", "set-url"] if "origin" in remotes else ["remote", "add"]), "origin", ssh_url)
    if push:
        _run("git", "push", *(["--force"] if force else []), "origin", "main", *seed.branches, cwd=seed.repo)
    urls = []
    for pr_id in seed.branches:
        pr = load_pr(pr_id)
        found = _run("gh", "pr", "list", "-R", repo, "--head", pr_id, "--state", "open",
                     "--json", "url", "--jq", ".[0].url").strip()
        urls.append(found or _run("gh", "pr", "create", "-R", repo, "--base", "main", "--head", pr_id,
                                  "--title", pr.title, "--body", pr.description).strip())
    return urls


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals.seed_github", description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", default=DEFAULT_REPO, help=f"OWNER/NAME of the demo repo (default {DEFAULT_REPO}).")
    p.add_argument("--prs", nargs="+", default=list(DEFAULT_PRS), metavar="ID", help="Dataset PR ids (default pr09 pr06).")
    p.add_argument("--action-ref", default="main",
                   help=f"Ref of {ACTION_REPO} the workflow uses (default main; use poc-v1 until main is updated).")
    p.add_argument("--local-only", metavar="DIR", help="Only build the git repo in DIR (must be empty); no network.")
    p.add_argument("--force", action="store_true", help="Force-push main and the PR branches.")
    p.add_argument("--skip-push", action="store_true",
                   help="Do not push (the branches must already be on the remote); only create missing PRs.")
    p.add_argument("--workdir", metavar="DIR", help="Build the local repo in DIR (empty) and keep it, instead of a temp dir.")
    args = p.parse_args(argv)
    try:
        if args.local_only:
            seed = build_seed_repo(args.local_only, args.prs, args.action_ref)
            print(f"built {seed.repo}: main={seed.main_sha[:10]} " + " ".join(f"{k}={v[:10]}" for k, v in seed.branches.items()))
            return 0
        with tempfile.TemporaryDirectory(prefix="ai-review-demo-") as tmp:
            seed = build_seed_repo(args.workdir or Path(tmp) / "repo", args.prs, args.action_ref)
            for url in publish(seed, args.repo, force=args.force, push=not args.skip_push):
                print(url)
    except DatasetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
