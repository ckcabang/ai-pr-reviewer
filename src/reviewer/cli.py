"""Command line interface: `python -m reviewer ...` / `reviewer ...`.

Exit codes: 0 normally, 1 when `--fail-on action_required` and the review has
action-required findings, 2 on errors.
"""
from __future__ import annotations

import argparse
import logging
import sys
from functools import partial
from pathlib import Path

import httpx2

from reviewer.config import load_config
from reviewer.context import build_context
from reviewer.diff import parse_diff
from reviewer.gitutil import git, git_diff
from reviewer.github import GitHubClient, parse_pr_url, post_review, pull_head_checkout, token_from_env
from reviewer.llm import Provider, build_provider
from reviewer.models import ReviewInput, ReviewResult
from reviewer.pipeline import review
from reviewer.render import render_json, render_markdown


class CLIError(Exception):
    """A user-facing problem (bad path, bad ref, ...). Reported as `error: ...`, exit 2."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reviewer", description="AI pull request reviewer.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("review", help="Review a change: a diff against a base ref, or a GitHub pull request.")
    p.add_argument("--repo", required=True, metavar="PATH", help="Git repository to review (any directory).")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base", metavar="REF", help="Base ref; reviews the diff BASE...HEAD.")
    mode.add_argument("--pr", metavar="URL",
                      help="GitHub pull request URL (https://github.com/OWNER/REPO/pull/N). Title, body and "
                           "commits come from GitHub; needs GITHUB_TOKEN in the environment.")
    p.add_argument("--post", action="store_true",
                   help="With --pr: post the result as one PR review (COMMENT event, inline comments on "
                        "changed lines, the rest in the summary).")
    p.add_argument("--title", default="", help="PR title (intent, treated as untrusted data).")
    p.add_argument("--description-file", metavar="FILE", help="File with the PR description.")
    p.add_argument("--rules", metavar="FILE", help="Team rules file (default: REVIEW.md at the repo root).")
    p.add_argument("--format", choices=["md", "json", "both"], default="md", help="Output format (default: md).")
    p.add_argument("--out", metavar="PATH",
                   help="Write here instead of stdout. With --format both, PATH is a prefix and "
                        "PATH.md and PATH.json are written.")
    p.add_argument("--fail-on", choices=["action_required", "never"], default="never",
                   help="Exit 1 if action-required findings exist (default: never).")
    p.add_argument("--no-cache", action="store_true", help="Do not read or write the disk cache.")
    p.add_argument("--verbose", action="store_true", help="Log progress to stderr.")
    p.add_argument("--config", metavar="FILE", help="Config file (default: config.yaml of the reviewer project).")

    c = sub.add_parser("context", help="Print the context bundle the specialists would receive (debugging).")
    c.add_argument("--repo", required=True, metavar="PATH", help="Git repository to read (any directory).")
    c.add_argument("--base", required=True, metavar="REF", help="Base ref; uses the diff BASE...HEAD.")
    c.add_argument("--rules", metavar="FILE", help="Team rules file (default: REVIEW.md at the repo root).")
    c.add_argument("--config", metavar="FILE", help="Config file (default: config.yaml of the reviewer project).")
    return parser


def main(argv: list[str] | None = None, *, provider: Provider | None = None,
         transport: httpx2.BaseTransport | None = None) -> int:
    """Run the CLI and return the exit code. `provider` replaces the real model provider
    and `transport` the HTTP transport used for GitHub (tests inject fakes); the disk cache
    still applies on top of the provider."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "review":
        if args.format == "both" and not args.out:
            parser.error("--format both requires --out")
        if args.post and args.pr is None:
            parser.error("--post requires --pr")
        if args.pr is not None and (args.title or args.description_file):
            parser.error("--title and --description-file cannot be combined with --pr "
                         "(the title and body come from GitHub)")

    verbose = getattr(args, "verbose", False)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(message)s", force=True)
    logging.getLogger("reviewer").setLevel(logging.INFO if verbose else logging.WARNING)
    try:
        if args.command == "context":
            return _context_command(args)
        return _review_command(args, provider, transport)
    except Exception as e:  # every failure is exit code 2 with a message, never a traceback
        if verbose:
            logging.getLogger("reviewer").exception("review failed")
        print(f"error: {e}", file=sys.stderr)
        return 2


def exit_code_for(result: ReviewResult, fail_on: str) -> int:
    if fail_on == "action_required" and result.in_tier("action_required"):
        return 1
    return 0


def _review_command(args: argparse.Namespace, inner: Provider | None,
                    transport: httpx2.BaseTransport | None) -> int:
    config = load_config(args.config)
    repo_root = _repo_root(args.repo)
    rules_path = Path(args.rules) if args.rules else None
    provider = build_provider(config, use_cache=not args.no_cache, inner=inner)

    post = None  # set in PR mode with --post: posts the result, returns the review URL
    if args.pr is not None:
        ref = parse_pr_url(args.pr)
        client = GitHubClient(ref, token_from_env(), transport=transport)
        pull = client.get_pull()
        with pull_head_checkout(repo_root, ref, pull) as checkout:
            review_input = ReviewInput(
                repo_root=checkout,
                diff=git_diff(checkout, pull.base_sha, pull.head_sha),
                title=pull.title,
                description=pull.body,
                base_ref=pull.base_sha,  # rules come from the base, not from the PR's own checkout (D36)
                review_rules_path=rules_path,
            )
            result = review(review_input, provider=provider, config=config)
        if args.post:
            post = partial(post_review, client, result, review_input.diff, pull.head_sha)
    else:
        description = ""
        if args.description_file:
            try:
                description = Path(args.description_file).read_text()
            except OSError as e:
                raise CLIError(f"cannot read description file: {e}") from e
        review_input = ReviewInput(
            repo_root=repo_root,
            diff=git_diff(repo_root, args.base),
            title=args.title,
            description=description,
            base_ref=args.base,
            review_rules_path=rules_path,
        )
        result = review(review_input, provider=provider, config=config)

    _write_output(result, args.format, args.out)
    if post:  # after the output, so a failed post still leaves the review on stdout / in the files
        print(f"posted review: {post()}", file=sys.stderr)
    return exit_code_for(result, args.fail_on)


def _context_command(args: argparse.Namespace) -> int:
    """Print the context bundle for BASE...HEAD exactly as the specialists get it. No model calls."""
    config = load_config(args.config)
    repo_root = _repo_root(args.repo)
    review_input = ReviewInput(
        repo_root=repo_root,
        diff=git_diff(repo_root, args.base),
        base_ref=args.base,
        review_rules_path=Path(args.rules) if args.rules else None,
    )
    bundle = build_context(review_input, parse_diff(review_input.diff), config)
    print(bundle.render())
    s = bundle.stats
    if s.get("enabled"):
        print(f"context: {s.get('changed_functions', 0)} changed, {s.get('callers', 0)} caller(s), "
              f"{s.get('callees', 0)} callee(s), {s['rules']} rule(s), {s['do_not_flag']} don't-flag item(s); "
              f"{s.get('chars', 0)}/{s['budget']} chars", file=sys.stderr)
    return 0


def _write_output(result: ReviewResult, fmt: str, out: str | None) -> None:
    if fmt == "both":
        base = Path(out)
        if base.suffix in (".md", ".json"):
            base = base.with_suffix("")
        _write_file(base.parent / f"{base.name}.md", render_markdown(result))
        _write_file(base.parent / f"{base.name}.json", render_json(result))
        return
    text = render_markdown(result) if fmt == "md" else render_json(result) + "\n"
    if out:
        _write_file(Path(out), text)
    else:
        sys.stdout.write(text)


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path}", file=sys.stderr)


def _repo_root(path: str) -> Path:
    if not Path(path).is_dir():
        raise CLIError(f"not a directory: {path}")
    return Path(git(path, "rev-parse", "--show-toplevel").strip())


if __name__ == "__main__":
    sys.exit(main())
