"""`REVIEW.md` parser: numbered team rules plus the don't-flag list.

Format (the demo service's `REVIEW.md` is the reference):

    # Review rules
    ## R1: Authenticated ownership
    Every endpoint ... (any number of lines)

    # Do not flag
    - Naming, formatting
    - "Consider adding tests" suggestions

A rule is a heading whose text starts with an id (`R1`, `R12`, or a bare number) followed by
`:` / `.` / `)` / `-` and a title. The don't-flag list is the bullet list under a heading
containing "do not flag" / "don't flag" (a bullet's wrapped lines are joined to it).
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("reviewer")

DEFAULT_RULES_FILE = "REVIEW.md"

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE_TITLE = re.compile(r"^(R?\d+)\s*[:.)\-–—]\s*(.+)$")
_DONT_FLAG = re.compile(r"do(?:\s+not|n['’]t)\s+flag", re.I)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")


class RulesError(Exception):
    """The rules file was asked for explicitly but cannot be read."""


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    text: str


@dataclass(frozen=True)
class Rules:
    rules: tuple[Rule, ...] = ()
    do_not_flag: tuple[str, ...] = ()

    def render_rules(self) -> str:
        """The `<team_rules>` body."""
        return "\n\n".join(f"{r.id}: {r.title}\n{r.text}".rstrip() for r in self.rules)

    def render_do_not_flag(self) -> str:
        """The `<do_not_flag>` body."""
        return "\n".join(f"- {item}" for item in self.do_not_flag)


def parse_rules(text: str) -> Rules:
    rules: list[Rule] = []
    dont: list[str] = []
    mode = "none"          # none | rule | dont
    current: tuple[str, str] | None = None
    body: list[str] = []

    def close_rule() -> None:
        nonlocal current, body
        if current:
            rules.append(Rule(current[0], current[1], "\n".join(body).strip()))
        current, body = None, []

    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            title = heading.group(2)
            rule = _RULE_TITLE.match(title)
            if rule:
                close_rule()
                current, mode = (rule.group(1), rule.group(2).strip()), "rule"
                continue
            if _DONT_FLAG.search(title):
                close_rule()
                mode = "dont"
                continue
            close_rule()
            mode = "none"
            continue
        if mode == "rule":
            body.append(line)
        elif mode == "dont" and line.strip():
            bullet = _BULLET.match(line)
            if bullet:
                dont.append(bullet.group(1).strip())
            elif dont:
                dont[-1] += " " + line.strip()
    close_rule()
    return Rules(tuple(rules), tuple(dont))


def load_rules(repo_root: Path, explicit: Path | None, base_ref: str | None = None) -> Rules:
    """The team rules, from the first of:

    1. `explicit` (an error if unreadable);
    2. `REVIEW.md` at `base_ref` (`git show`), so the change under review cannot edit the rules
       it is judged by (D33);
    3. `<repo_root>/REVIEW.md`, when the base has none (or there is no base);
    4. nothing.
    """
    if explicit is not None:
        try:
            return parse_rules(Path(explicit).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            raise RulesError(f"cannot read rules file {explicit}: {e}") from e
    if base_ref:
        at_base = _git_show(repo_root, base_ref, DEFAULT_RULES_FILE)
        if at_base is not None:
            return parse_rules(at_base)
        log.info("no %s at %s, using the checkout's", DEFAULT_RULES_FILE, base_ref)
    path = Path(repo_root) / DEFAULT_RULES_FILE
    try:
        return parse_rules(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return Rules()


def _git_show(repo_root: Path, ref: str, path: str) -> str | None:
    """`path` as it is at `ref`, or None when git cannot produce it (no such file or ref, not a repo)."""
    if ref.startswith("-"):
        return None
    try:
        out = subprocess.run(["git", "-C", str(repo_root), "show", f"{ref}:{path}"],
                             capture_output=True, check=False)
        return out.stdout.decode("utf-8") if out.returncode == 0 else None
    except (OSError, UnicodeDecodeError):
        return None
