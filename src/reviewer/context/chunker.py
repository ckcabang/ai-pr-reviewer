"""Python source -> chunks (top-level functions, classes and methods), parsed with `ast`.

A class chunk holds the class header and class-level statements only; each method is its
own chunk (qualname `Class.method`), so a method's body is never stored twice. Nested
functions belong to their enclosing function. Files that fail to parse are skipped.
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Iterator, Literal, NamedTuple

# Directories never indexed. A directory holding `pyvenv.cfg` is a virtualenv whatever its name.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", "site-packages",
    "vendor", "third_party", "build", "dist", ".tox", ".nox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".eggs", ".idea", ".vscode", ".cache",
})
MAX_FILE_BYTES = 1_000_000

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


class Call(NamedTuple):
    name: str
    """The called name: `foo` in both `foo()` and `x.foo()`."""
    receiver: str | None
    """For `x.foo()`, the name just left of the dot (`x`); None for a bare `foo()`."""


@dataclass(frozen=True)
class Chunk:
    type: Literal["function", "method", "class"]
    name: str
    qualname: str
    file: str
    """Repo-relative, forward slashes."""
    line_start: int
    """First line, including decorators."""
    line_end: int
    lines: tuple[tuple[int, str], ...] = field(repr=False)
    """The chunk's own source as (real file line number, text). For a class this leaves out
    the methods, so the numbers have gaps."""
    calls: tuple[Call, ...] = field(default=(), compare=False, repr=False)
    """Names this chunk calls, in source order, without duplicates."""

    @property
    def key(self) -> tuple[str, str]:
        return (self.file, self.qualname)

    @property
    def source(self) -> str:
        return "\n".join(text for _, text in self.lines)

    @property
    def owner(self) -> str | None:
        """Qualified name of the enclosing class, for methods and nested classes."""
        return self.qualname.rpartition(".")[0] or None

    @cached_property
    def _own_lines(self) -> frozenset[int]:
        return frozenset(n for n, _ in self.lines)

    def contains(self, line: int) -> bool:
        """Is `line` part of this chunk's own source (a class does not own its methods' lines)?"""
        return line in self._own_lines


@dataclass(frozen=True)
class SourceFile:
    path: str
    chunks: tuple[Chunk, ...]
    imports: dict[str, tuple[str | None, str]] = field(default_factory=dict, compare=False)
    """local name -> (stem of the module it was imported from or None, original name)."""


def iter_python_files(root: Path) -> Iterator[Path]:
    """Every `.py` file under `root`, in sorted order, skipping VCS, virtualenv and vendor dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        if "pyvenv.cfg" in filenames:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath) / name


def parse_file(root: Path, path: Path) -> SourceFile | None:
    """Chunk one file; None when it cannot be read or parsed."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError, RecursionError):
        return None
    rel = path.relative_to(root).as_posix()
    return SourceFile(path=rel, chunks=tuple(_chunks(tree, rel, text.splitlines())), imports=_imports(tree))


def _chunks(tree: ast.Module, rel: str, source_lines: list[str]) -> Iterator[Chunk]:
    def walk(body: list[ast.stmt], prefix: str, in_class: bool) -> Iterator[Chunk]:
        for node in body:
            if isinstance(node, _DEFS):
                start, end = _span(node)
                yield Chunk(
                    type="method" if in_class else "function", name=node.name,
                    qualname=prefix + node.name, file=rel, line_start=start, line_end=end,
                    lines=_numbered(source_lines, [(start, end)]),
                    calls=_calls([*node.body, *node.args.defaults, *(d for d in node.args.kw_defaults if d)]),
                )
            elif isinstance(node, ast.ClassDef):
                start, end = _span(node)
                own = _subtract((start, end), [_span(n) for n in _defs_in(node.body)])
                qual = prefix + node.name
                yield Chunk(
                    type="class", name=node.name, qualname=qual, file=rel, line_start=start, line_end=end,
                    lines=_numbered(source_lines, own),
                    calls=_calls([n for n in node.body if not isinstance(n, (*_DEFS, ast.ClassDef))]),
                )
                yield from walk(node.body, qual + ".", True)
            else:  # defs under `if TYPE_CHECKING:`, `try:` and the like
                for inner in _nested_bodies(node):
                    yield from walk(inner, prefix, in_class)

    yield from walk(tree.body, "", False)


def _defs_in(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Function and class definitions in a class body (also under `if`/`try`)."""
    for node in body:
        if isinstance(node, (*_DEFS, ast.ClassDef)):
            yield node
        else:
            for inner in _nested_bodies(node):
                yield from _defs_in(inner)


def _nested_bodies(node: ast.stmt) -> Iterator[list[ast.stmt]]:
    for attr in ("body", "orelse", "finalbody"):
        inner = getattr(node, attr, None)
        if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
            yield inner
    for handler in getattr(node, "handlers", []):
        yield handler.body


def _span(node: ast.stmt) -> tuple[int, int]:
    start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    return start, node.end_lineno or node.lineno


def _subtract(whole: tuple[int, int], holes: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out, cursor = [], whole[0]
    for start, end in sorted(holes):
        if start > cursor:
            out.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= whole[1]:
        out.append((cursor, whole[1]))
    return out


def _numbered(source_lines: list[str], spans: list[tuple[int, int]]) -> tuple[tuple[int, str], ...]:
    return tuple((n, source_lines[n - 1]) for start, end in spans for n in range(start, end + 1)
                 if 0 < n <= len(source_lines))


def _calls(roots: list[ast.AST]) -> tuple[Call, ...]:
    """Called names under `roots`. A function passed to `Depends(...)` counts as called
    (FastAPI routes reach `get_current_user` that way)."""
    seen: dict[Call, None] = {}
    for root in roots:
        for node in ast.walk(root):
            if not isinstance(node, ast.Call):
                continue
            call = _call_of(node.func)
            if call:
                seen.setdefault(call)
                if call.name == "Depends" and node.args:
                    ref = _call_of(node.args[0])
                    if ref:
                        seen.setdefault(ref)
    return tuple(seen)


def _call_of(func: ast.expr) -> Call | None:
    if isinstance(func, ast.Name):
        return Call(func.id, None)
    if isinstance(func, ast.Attribute):
        return Call(func.attr, _receiver_name(func.value))
    return None


def _receiver_name(value: ast.expr) -> str | None:
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return None


def _imports(tree: ast.Module) -> dict[str, tuple[str | None, str]]:
    out: dict[str, tuple[str | None, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            stem = node.module.rpartition(".")[2] if node.module else None
            for a in node.names:
                out[a.asname or a.name] = (stem, a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                local = a.asname or a.name.partition(".")[0]
                out[local] = (None, a.name.rpartition(".")[2] if a.asname else local)
    return out
