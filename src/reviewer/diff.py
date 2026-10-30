"""Unified diff parsing.

`parse_diff` turns the text of `git diff` into `FileDiff` objects with everything the
rest of the pipeline needs: the new path, the kind of change, which new-side lines were
added, where lines were deleted, and which new-side lines are *commentable* (inside a
hunk, so GitHub accepts an inline comment on them).

All line numbers are NEW-side (the file as it is at HEAD of the change). The diff is
expected in git's standard `a/` / `b/` prefix format.

Lock files, binaries and generated files are skipped entirely (not returned).
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Literal

ChangeType = Literal["added", "modified", "deleted", "renamed"]

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_LOCK_NAMES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "uv.lock", "Cargo.lock", "composer.lock", "Gemfile.lock", "go.sum", "bun.lockb",
}
_GENERATED_MARKER = "@generated"
_GENERATED_HEADER_LINES = 5  # the marker must sit in the first lines of the file
_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", '"': '"', "\\": "\\", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


@dataclass
class Hunk:
    header: str
    lines: list[tuple[str, int | None, str]] = field(default_factory=list)
    """(marker, new_line, text) in diff order. marker is ' ', '+' or '-'.
    new_line is None for deleted lines."""


@dataclass
class FileDiff:
    path: str
    """New-side path (the old path for deleted files)."""
    change_type: ChangeType = "modified"
    old_path: str | None = None
    hunks: list[Hunk] = field(default_factory=list)
    added_lines: list[int] = field(default_factory=list)
    """New-side numbers of added lines."""
    deleted_positions: list[int] = field(default_factory=list)
    """For each run of deleted lines, the new-side number of the line that now occupies
    the spot (the first line after the run). Empty for deleted files."""
    commentable_lines: list[int] = field(default_factory=list)
    """Every new-side line inside a hunk, added or context."""


@dataclass
class _Header:
    git_line: str = ""
    new_file: bool = False
    deleted_file: bool = False
    binary: bool = False
    rename_from: str | None = None
    rename_to: str | None = None
    minus: str | None = None
    plus: str | None = None


def parse_diff(text: str) -> list[FileDiff]:
    """Parse a unified diff into files. Lock, binary and generated files are not returned."""
    files: list[FileDiff] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if lines[i].startswith("diff --git "):
            i = _parse_file(lines, i, files)
        else:
            i += 1
    return files


def _parse_file(lines: list[str], i: int, out: list[FileDiff]) -> int:
    header = _Header(git_line=lines[i])
    i += 1
    # Extended header lines, up to the first hunk or the next file.
    while i < len(lines) and not lines[i].startswith(("@@", "diff --git ")):
        line = lines[i]
        if line.startswith("new file mode"):
            header.new_file = True
        elif line.startswith("deleted file mode"):
            header.deleted_file = True
        elif line.startswith("rename from "):
            header.rename_from = _unquote(line[len("rename from "):])
        elif line.startswith("rename to "):
            header.rename_to = _unquote(line[len("rename to "):])
        elif line.startswith(("Binary files ", "GIT binary patch")):
            header.binary = True
        elif line.startswith("--- "):
            header.minus = _diff_path(line[4:])
        elif line.startswith("+++ "):
            header.plus = _diff_path(line[4:])
        i += 1

    f = _file_from_header(header)
    while i < len(lines) and lines[i].startswith("@@"):
        m = _HUNK_RE.match(lines[i])
        if not m:
            i += 1
            continue
        i = _parse_hunk(lines, i, m, f)

    if f.path and not header.binary and not _looks_generated(f) and not _is_lock_file(f.path):
        out.append(f)
    return i


def _file_from_header(h: _Header) -> FileDiff:
    if h.new_file:
        change: ChangeType = "added"
    elif h.deleted_file:
        change = "deleted"
    elif h.rename_to is not None:
        change = "renamed"
    else:
        change = "modified"
    path = h.plus or h.rename_to or h.minus or _path_from_git_line(h.git_line)
    old_path = h.rename_from if change == "renamed" else None
    return FileDiff(path=path or "", change_type=change, old_path=old_path)


def _parse_hunk(lines: list[str], i: int, m: re.Match, f: FileDiff) -> int:
    old_left = int(m.group(2) if m.group(2) is not None else 1)
    new_left = int(m.group(4) if m.group(4) is not None else 1)
    new_line = int(m.group(3))
    track_new_side = f.change_type != "deleted"
    hunk = Hunk(header=lines[i])
    f.hunks.append(hunk)
    i += 1
    in_deletion_run = False
    while i < len(lines) and (old_left > 0 or new_left > 0):
        raw = lines[i]
        marker, body = raw[:1], raw[1:]
        if marker == "\\":  # "\ No newline at end of file"
            i += 1
            continue
        if marker == "+":
            hunk.lines.append(("+", new_line, body))
            f.added_lines.append(new_line)
            f.commentable_lines.append(new_line)
            new_line += 1
            new_left -= 1
            in_deletion_run = False
        elif marker == "-":
            hunk.lines.append(("-", None, body))
            if track_new_side and not in_deletion_run:
                f.deleted_positions.append(new_line)
            in_deletion_run = True
            old_left -= 1
        else:  # context line (a blank context line may arrive as an empty string)
            hunk.lines.append((" ", new_line, body))
            f.commentable_lines.append(new_line)
            new_line += 1
            old_left -= 1
            new_left -= 1
            in_deletion_run = False
        i += 1
    return i


def _is_lock_file(path: str) -> bool:
    name = posixpath.basename(path)
    return name in _LOCK_NAMES or name.endswith((".lock", ".min.js", ".min.css"))


def _looks_generated(f: FileDiff) -> bool:
    return any(
        _GENERATED_MARKER in text
        for hunk in f.hunks
        for _, n, text in hunk.lines
        if n is not None and n <= _GENERATED_HEADER_LINES
    )


def _diff_path(raw: str) -> str | None:
    """Path from a `--- ` / `+++ ` line, without the a/ b/ prefix; None for /dev/null."""
    raw = raw.rstrip("\t")  # git appends a tab after paths that contain spaces
    if raw == "/dev/null":
        return None
    path = _unquote(raw)
    return path[2:] if path[:2] in ("a/", "b/") else path


def _path_from_git_line(git_line: str) -> str:
    """Fallback for headers with no ---/+++ lines (mode changes): `diff --git a/X b/X`."""
    s = git_line[len("diff --git "):]
    n = (len(s) - 5) // 2
    if n > 0 and s == f"a/{s[2:2 + n]} b/{s[2:2 + n]}":
        return s[2:2 + n]
    return ""


def _unquote(raw: str) -> str:
    """Undo git's C-style quoting of unusual paths."""
    if not (len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"'):
        return raw
    body, out, i = raw[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "01234567":
                j = i + 1
                while j < len(body) and j < i + 4 and body[j] in "01234567":
                    j += 1
                out.append(int(body[i + 1:j], 8))
                i = j
                continue
            out += _ESCAPES.get(nxt, nxt).encode()
            i += 2
            continue
        out += ch.encode()
        i += 1
    return out.decode("utf-8", errors="replace")


def render_numbered_diff(files: list[FileDiff]) -> str:
    """Render files as a diff with a new-file line-number gutter, so a reviewer can cite
    real line numbers. Added and context lines carry their new-side number; deleted lines
    have a blank gutter."""
    out: list[str] = []
    for f in files:
        label = f"{f.path} ({f.change_type}" + (f" from {f.old_path})" if f.old_path else ")")
        out.append(f"=== {label}")
        for hunk in f.hunks:
            out.append(f"      {hunk.header}")
            for marker, n, text in hunk.lines:
                gutter = f"{n:>5}" if n is not None else "     "
                out.append(f"{gutter} {marker}{text}")
    return "\n".join(out)
