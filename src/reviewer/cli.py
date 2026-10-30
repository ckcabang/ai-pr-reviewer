"""Command line interface: `python -m reviewer ...` / `reviewer ...`."""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reviewer", description="AI pull request reviewer.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("review", help="Review a change.")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
