"""Integrity check for dataset PRs: does the patch apply, does the app import, do the labels point
at real changed lines, does the description stay hint-free, and (for clean PRs) do the service tests pass."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .loader import DATASET_ROOT, DatasetError, DatasetPR, load_all
from .materialize import materialize

HINT_WORDS = re.compile(
    r"\b(bugs?|vulnerab\w*|insecure|exploit\w*|planted|seeded|intentional\w*|forgot\w*|oversight|mistake|"
    r"unsafe|injection|leak\w*|regress\w*)\b",
    re.IGNORECASE,
)
_IMPORT_ALL = (
    "import importlib, pkgutil, app\n"
    "for m in pkgutil.walk_packages(app.__path__, 'app.'):\n"
    "    importlib.import_module(m.name)\n"
)


def changed_ranges(patch: str) -> dict[str, list[tuple[int, int]]]:
    """New-side line ranges (inclusive, context included) of each hunk, keyed by file."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    current = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            path = line[4:].split("\t")[0]
            current = None if path == "/dev/null" else path.removeprefix("b/")
        elif line.startswith("@@") and current:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start, count = int(m[1]), int(m[2] if m[2] is not None else 1)
                ranges.setdefault(current, []).append((start, start + max(count, 1) - 1))
    return ranges


def _run(repo: Path, *cmd: str) -> subprocess.CompletedProcess:
    scratch = tempfile.mkdtemp(prefix="dataset-check-")
    env = {
        **os.environ, "PYTHONPATH": str(repo), "PYTHONDONTWRITEBYTECODE": "1",
        "DATABASE_PATH": f"{scratch}/check.db", "EXPORT_DIR": f"{scratch}/exports",
    }
    try:
        return subprocess.run([sys.executable, *cmd], cwd=repo, env=env, capture_output=True, text=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _line_count(path: Path) -> int:
    return len(path.read_text().splitlines())


def check_pr(pr: DatasetPR) -> list[str]:
    """Return a list of human-readable problems with `pr` (empty means sound)."""
    problems: list[str] = []
    for text in (pr.title, pr.description):
        if m := HINT_WORDS.search(text):
            problems.append(f"{pr.id}: title/description may hint at a problem ({m[0]!r})")

    try:
        m = materialize(pr)
    except DatasetError as e:
        return problems + [str(e)]
    try:
        imported = _run(m.repo, "-c", _IMPORT_ALL)
        if imported.returncode:
            tail = imported.stderr.strip().splitlines()[-1:] or ["?"]
            problems.append(f"{pr.id}: patched app fails to import: {tail[0]}")

        ranges = changed_ranges(pr.patch)
        for issue in pr.expected_issues:
            if issue.file not in ranges:
                problems.append(f"{issue.id}: anchor file {issue.file} is not changed by the patch")
            elif not any(lo <= issue.line_hint <= hi for lo, hi in ranges[issue.file]):
                problems.append(f"{issue.id}: anchor {issue.file}:{issue.line_hint} is not inside the diff")
            for loc in issue.related:
                where = m.repo / loc.file
                if not where.is_file() or loc.line_hint > _line_count(where):
                    problems.append(f"{issue.id}: related location {loc.file}:{loc.line_hint} does not exist")

        if pr.is_clean and not imported.returncode:
            tests = _run(m.repo, "-m", "pytest", "tests", "-q", "-x", "-p", "no:cacheprovider", "--import-mode=importlib")
            if tests.returncode:
                tail = tests.stdout.strip().splitlines()[-1:] or ["?"]
                problems.append(f"{pr.id}: clean PR but service tests fail after the patch: {tail[0]}")
    finally:
        shutil.rmtree(m.repo, ignore_errors=True)
    return problems


def check_all(root: Path = DATASET_ROOT) -> dict[str, list[str]]:
    """Problems per dataset PR id (only PRs with problems are listed)."""
    results = {pr.id: check_pr(pr) for pr in load_all(root)}
    return {k: v for k, v in results.items() if v}
