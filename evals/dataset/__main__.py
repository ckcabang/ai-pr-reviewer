"""CLI for dataset authors.

    uv run python -m evals.dataset check [pr_id ...]     integrity check (all PRs by default)
    uv run python -m evals.dataset materialize pr09 DIR  base + head repo for a PR
    uv run python -m evals.dataset base DIR              base-only repo, to edit and `git diff` into a new patch
"""
from __future__ import annotations

import sys

from . import DatasetError, load_all, load_pr, materialize, materialize_base
from .integrity import check_pr


def main(argv: list[str]) -> int:
    cmd, args = (argv[0] if argv else ""), argv[1:]
    try:
        if cmd == "check":
            prs = [load_pr(a) for a in args] or load_all()
            bad = 0
            for pr in prs:
                problems = check_pr(pr)
                print(f"{pr.id}: {'ok' if not problems else 'FAILED'}")
                for p in problems:
                    print(f"  - {p}")
                bad += bool(problems)
            return 1 if bad else 0
        if cmd == "materialize" and len(args) == 2:
            m = materialize(args[0], args[1])
            print(f"{m.repo}  base={m.base_sha[:10]} ({m.base_ref})  head={m.head_sha[:10]} ({m.head_ref})")
            return 0
        if cmd == "base" and len(args) == 1:
            print(f"{args[0]}  base={materialize_base(args[0])[:10]} (main)")
            return 0
    except DatasetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
