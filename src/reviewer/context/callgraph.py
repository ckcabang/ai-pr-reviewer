"""A name-based static call graph over the repository's Python files.

An edge exists when a chunk calls a name (`foo(...)` or `x.foo(...)`) and some chunk
defines that name. That is an approximation (D27): no type inference, no import
resolution beyond the cheap checks in `match_strength`. To keep the noise down, a match is

- strong (2) when the call is qualified like the target: `pricing.foo()` for a function in
  `pricing.py`, `from pricing import foo; foo()`, a bare call to a function in the same
  file, `self.foo()` inside the target's class, or `Class.foo()`;
- weak (1) otherwise, and a weak match is only used when the name is defined exactly once
  in the repo and is not one of the ubiquitous names in `COMMON_NAMES` (`get`, `execute`, ...).

Callers are ranked strong before weak, non-test before test code. Callees are ranked strong
before weak, then rarely-called helpers before ubiquitous utilities, then by call order.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path, PurePosixPath

from reviewer.context.chunker import Call, Chunk, SourceFile, iter_python_files, parse_file

# Method and function names too generic for a name-only (weak) match.
COMMON_NAMES = frozenset({
    "get", "set", "add", "update", "pop", "append", "extend", "remove", "clear", "copy", "items",
    "keys", "values", "read", "write", "close", "open", "join", "split", "strip", "format",
    "execute", "send", "run", "start", "stop", "one", "all", "first", "load", "dump", "dumps",
    "loads", "save", "delete", "create", "find", "count", "index", "sort", "insert", "encode",
    "decode", "main", "setup", "handle", "call", "post", "put", "patch", "head", "info", "debug",
    "warning", "error", "exception", "connect", "commit", "rollback", "reset", "validate",
})


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    return bool({"tests", "test"} & set(p.parts[:-1])) or p.name.startswith("test_") \
        or p.name.endswith("_test.py") or p.name == "conftest.py"


class RepoIndex:
    """All chunks of a repository plus the name -> definers and name -> callers maps."""

    def __init__(self, files: list[SourceFile]):
        self.files: dict[str, SourceFile] = {f.path: f for f in files}
        self.chunks: dict[tuple[str, str], Chunk] = {}
        self.defs: dict[str, list[Chunk]] = defaultdict(list)
        self.callers_of: dict[str, list[Chunk]] = defaultdict(list)
        for f in files:
            for c in f.chunks:
                if c.key in self.chunks:  # conditional redefinition: keep the first
                    continue
                self.chunks[c.key] = c
                self.defs[c.name].append(c)
                for name in {call.name for call in c.calls}:
                    self.callers_of[name].append(c)

    @classmethod
    def build(cls, root: Path) -> "RepoIndex":
        parsed = (parse_file(root, p) for p in iter_python_files(root))
        return cls([f for f in parsed if f is not None])

    # -- resolution ------------------------------------------------------------------

    def target_of(self, chunk: Chunk) -> Chunk:
        """What callers actually name: `Foo(...)` constructs through `Foo.__init__`."""
        if chunk.type == "method" and chunk.name == "__init__" and chunk.owner:
            return self.chunks.get((chunk.file, chunk.owner), chunk)
        return chunk

    def match_strength(self, call: Call, caller: Chunk, target: Chunk) -> int:
        """0 = cannot be this target, 1 = same name only, 2 = qualified like the target."""
        stem = PurePosixPath(target.file).stem
        imports = self.files[caller.file].imports if caller.file in self.files else {}
        imported = imports.get(call.receiver or call.name)
        if call.receiver is None:
            if target.type == "method":
                return 0
            if caller.file == target.file:
                return 2
            return 2 if imported and imported[0] == stem and imported[1] == target.name else 1
        if call.receiver == stem or (imported and imported[1] == stem):
            return 2
        if target.type == "method" and target.owner:
            cls = target.owner.rpartition(".")[2]
            if call.receiver in ("self", "cls") and caller.file == target.file and caller.owner == target.owner:
                return 2
            if call.receiver == cls or (imported and imported[1] == cls):
                return 2
        return 1

    def _weak_ok(self, name: str) -> bool:
        return (len(self.defs.get(name, ())) == 1 and name not in COMMON_NAMES
                and not (name.startswith("__") and name.endswith("__")))

    # -- queries ---------------------------------------------------------------------

    def callers(self, target: Chunk, *, exclude: set[tuple[str, str]], limit: int) -> list[Chunk]:
        """Up to `limit` chunks calling `target` (never one in `exclude`)."""
        target = self.target_of(target)
        found: list[tuple[int, Chunk]] = []
        for caller in self.callers_of.get(target.name, ()):
            if caller.key == target.key or caller.key in exclude:
                continue
            strength = max(self.match_strength(call, caller, target)
                           for call in caller.calls if call.name == target.name)
            if strength == 2 or (strength == 1 and self._weak_ok(target.name)):
                found.append((strength, caller))
        found.sort(key=lambda t: (-t[0], is_test_file(t[1].file), t[1].file, t[1].line_start))
        return [c for _, c in found[:limit]]

    def callers_by_name(self, name: str, *, exclude: set[tuple[str, str]], limit: int) -> list[Chunk]:
        """Chunks calling a name that no longer has a definition (a removed or renamed function)."""
        if name in COMMON_NAMES or (name.startswith("__") and name.endswith("__")):
            return []
        found = [c for c in self.callers_of.get(name, ()) if c.key not in exclude]
        found.sort(key=lambda c: (is_test_file(c.file), c.file, c.line_start))
        return found[:limit]

    def callees(self, chunk: Chunk, *, exclude: set[tuple[str, str]], limit: int) -> list[Chunk]:
        """Up to `limit` chunks that `chunk` calls (never one in `exclude`)."""
        ranked: list[tuple[tuple, Chunk]] = []
        for order, call in enumerate(chunk.calls):
            candidates = [(self.match_strength(call, chunk, d), d)
                          for d in self.defs.get(call.name, ()) if d.key != chunk.key]
            if not candidates:
                continue
            best = max(s for s, _ in candidates)
            if best == 0 or (best == 1 and not self._weak_ok(call.name)):
                continue
            fan_in = len(self.callers_of.get(call.name, ()))
            for s, d in candidates:
                if s == best and d.key not in exclude:
                    ranked.append(((-s, fan_in, order), d))
        ranked.sort(key=lambda t: t[0])
        out: dict[tuple[str, str], Chunk] = {}
        for _, d in ranked:
            out.setdefault(d.key, d)
        return list(out.values())[:limit]
