"""Command line interface: `python -m reviewer ...` / `reviewer ...`.

Exit codes: 0 normally, 1 when `--fail-on action_required` and the review has
action-required findings, 2 on errors.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from reviewer.config import load_config
from reviewer.llm import Provider, build_provider
from reviewer.models import ReviewInput, ReviewResult
from reviewer.pipeline import review
from reviewer.render import render_json, render_markdown


class CLIError(Exception):
    """A user-facing problem (bad path, bad ref, ...). Reported as `error: ...`, exit 2."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reviewer", description="AI pull request reviewer.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("review", help="Review the changes in a repository against a base ref.")
    p.add_argument("--repo", required=True, metavar="PATH", help="Git repository to review (any directory).")
    p.add_argument("--base", required=True, metavar="REF", help="Base ref; reviews the diff BASE...HEAD.")
    p.add_argument("--title", default="", help="PR title (intent, treated as untrusted data).")
    p.add_argument("--description-file", metavar="FILE", help="File with the PR description.")
    p.add_argument("--format", choices=["md", "json", "both"], default="md", help="Output format (default: md).")
    p.add_argument("--out", metavar="PATH",
                   help="Write here instead of stdout. With --format both, PATH is a prefix and "
                        "PATH.md and PATH.json are written.")
    p.add_argument("--fail-on", choices=["action_required", "never"], default="never",
                   help="Exit 1 if action-required findings exist (default: never).")
    p.add_argument("--no-cache", action="store_true", help="Do not read or write the disk cache.")
    p.add_argument("--verbose", action="store_true", help="Log progress to stderr.")
    p.add_argument("--config", metavar="FILE", help="Config file (default: config.yaml of the reviewer project).")
    return parser


def main(argv: list[str] | None = None, *, provider: Provider | None = None) -> int:
    """Run the CLI and return the exit code. `provider` replaces the real model provider
    (tests inject a fake); the disk cache still applies on top of it."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.format == "both" and not args.out:
        parser.error("--format both requires --out")

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(message)s", force=True)
    logging.getLogger("reviewer").setLevel(logging.INFO if args.verbose else logging.WARNING)
    try:
        return _review_command(args, provider)
    except Exception as e:  # every failure is exit code 2 with a message, never a traceback
        if args.verbose:
            logging.getLogger("reviewer").exception("review failed")
        print(f"error: {e}", file=sys.stderr)
        return 2


def exit_code_for(result: ReviewResult, fail_on: str) -> int:
    if fail_on == "action_required" and result.in_tier("action_required"):
        return 1
    return 0


def _review_command(args: argparse.Namespace, inner: Provider | None) -> int:
    config = load_config(args.config)
    repo_root = _repo_root(args.repo)
    description = ""
    if args.description_file:
        try:
            description = Path(args.description_file).read_text()
        except OSError as e:
            raise CLIError(f"cannot read description file: {e}") from e

    review_input = ReviewInput(
        repo_root=repo_root,
        diff=_git_diff(repo_root, args.base),
        title=args.title,
        description=description,
    )
    provider = build_provider(config, use_cache=not args.no_cache, inner=inner)
    result = review(review_input, provider=provider, config=config)

    _write_output(result, args.format, args.out)
    return exit_code_for(result, args.fail_on)


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


def _git(repo: Path | str, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise CLIError("git is not installed or not on PATH") from e
    if out.returncode != 0:
        raise CLIError(out.stderr.strip() or f"git {' '.join(args)} failed")
    return out.stdout


def _repo_root(path: str) -> Path:
    if not Path(path).is_dir():
        raise CLIError(f"not a directory: {path}")
    return Path(_git(path, "rev-parse", "--show-toplevel").strip())


def _git_diff(repo_root: Path, base: str) -> str:
    # Explicit prefixes/flags so the user's git config (diff.noprefix, quotepath, external
    # diff tools, rename detection) cannot change what the parser sees.
    return _git(repo_root, "-c", "core.quotepath=false", "diff", "--no-color", "--no-ext-diff",
                "--src-prefix=a/", "--dst-prefix=b/", "-M", f"{base}...HEAD")


if __name__ == "__main__":
    sys.exit(main())
