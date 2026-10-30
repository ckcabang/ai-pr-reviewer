"""Build the context bundle a specialist receives next to the diff.

Priority order, highest first: changed functions, their callers, their callees, then
semantic matches (embeddings, optional). Blocks are added in that order until the character
budget runs out; the first block that does not fit is truncated and everything after it is
left out, so the lowest priority always goes first. The team rules and the don't-flag list are
small and always included in full (outside the budget).

Every code block is headed `path:start-end (reason)` and numbered with the real file line
numbers, so a specialist can cite exact lines.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from reviewer.context.callgraph import RepoIndex, is_test_file
from reviewer.context.chunker import Chunk, SourceFile
from reviewer.context.rules import Rules, load_rules
from reviewer.context.semantic import semantic_matches
from reviewer.diff import FileDiff
from reviewer.llm import CachedProvider

if TYPE_CHECKING:
    from reviewer.config import Config
    from reviewer.llm import Provider
    from reviewer.models import ReviewInput

log = logging.getLogger("reviewer")

# Lowest number = highest priority = kept first when the budget runs out.
PRIORITY = {"changed": 0, "owner": 1, "caller": 2, "callee": 3, "semantic": 4}
MIN_PARTIAL_CHARS = 400  # a block is truncated to fit only if at least this much budget is left

_REMOVED_DEF = re.compile(r"^\s*(?:async\s+def|def|class)\s+(\w+)")


@dataclass
class Block:
    kind: str
    """One of PRIORITY."""
    chunk: Chunk
    reasons: list[str]


@dataclass
class ContextBundle:
    code: str = ""
    """The `<context>` body: numbered code blocks."""
    rules: str = ""
    """The `<team_rules>` body."""
    do_not_flag: str = ""
    """The `<do_not_flag>` body."""
    rule_ids: tuple[str, ...] = ()
    """Ids of the rules in `rules` (R1, R2, ...), so a finding's `rule_id` can be checked."""
    stats: dict = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.code or self.rules or self.do_not_flag)

    def render(self) -> str:
        """The three sections as the specialists' prompt shows them (also the `context` command's output)."""
        parts = [f"<{tag}>\n{body}\n</{tag}>" for tag, body in
                 (("context", self.code), ("team_rules", self.rules), ("do_not_flag", self.do_not_flag)) if body]
        return "\n\n".join(parts) if parts else "(empty context bundle)"


def build_context(review_input: "ReviewInput", files: list[FileDiff], config: "Config",
                  provider: "Provider | None" = None) -> ContextBundle:
    """The context bundle for a change. Disabled by `context.enabled: false` (the no-context
    ablation). `provider` is used only for embeddings (semantic search); without one, or with
    `context.semantic.enabled: false`, no embedding call is made."""
    cfg = config.context
    if not cfg.enabled:
        return ContextBundle(stats={"enabled": False})
    rules = load_rules(review_input.repo_root, review_input.review_rules_path, review_input.base_ref)
    stats: dict = {"enabled": True, "rules": len(rules.rules), "do_not_flag": len(rules.do_not_flag)}
    try:
        query = f"{review_input.title}\n{review_input.description}\n{review_input.diff}"  # what semantic search matches
        blocks, counts = select_blocks(review_input.repo_root, files, config, provider=provider, query=query)
    except Exception as e:  # a bug here must not cost the review: fall back to rules only
        log.warning("context engine failed, continuing without code context: %s", e)
        blocks, counts, stats["error"] = [], {}, f"{type(e).__name__}: {e}"
    code, included, omitted = assemble(blocks, cfg.max_context_chars)
    stats.update(counts)
    stats.update(blocks=len(included), chars=len(code), budget=cfg.max_context_chars,
                 truncated_blocks=sum(1 for b in included if b[1]), omitted_blocks=len(omitted))
    return _bundle(code, rules, stats)


def _bundle(code: str, rules: Rules, stats: dict) -> ContextBundle:
    return ContextBundle(code=code, rules=rules.render_rules(), do_not_flag=rules.render_do_not_flag(),
                         rule_ids=tuple(r.id for r in rules.rules), stats=stats)


# -- selection --------------------------------------------------------------------------

def select_blocks(root, files: list[FileDiff], config: "Config", *, provider: "Provider | None" = None,
                  query: str = "") -> tuple[list[Block], dict]:
    """Choose what to show, in priority order, before the budget is applied."""
    cfg = config.context
    index = RepoIndex.build(root)
    selection = _Selection()

    changed = changed_chunks(index, files)
    for c in changed:
        selection.add("changed", c, f"changed {_label(c)}")
    changed_keys = {c.key for c in changed}
    for c in changed:  # a changed method's class header shows the attributes `self.` refers to
        owner = index.chunks.get((c.file, c.owner)) if c.type == "method" and c.owner else None
        if owner:
            selection.add("owner", owner, f"class of changed {_label(c)}")

    if cfg.callgraph.enabled:
        # Changed tests are shown but not expanded: their helpers are noise next to production callers.
        expand = [c for c in changed if not is_test_file(c.file)]
        for name in removed_names(files, index):
            for caller in index.callers_by_name(name, exclude=changed_keys, limit=cfg.callgraph.max_callers):
                selection.add("caller", caller, f"unchanged code that still calls `{name}`, which this change removed")
        for c in expand:
            for caller in index.callers(c, exclude=changed_keys, limit=cfg.callgraph.max_callers):
                selection.add("caller", caller, f"unchanged caller of changed {_label(c)}")
        for c in expand:
            for callee in index.callees(c, exclude=changed_keys, limit=cfg.callgraph.max_callees):
                selection.add("callee", callee, f"called by changed {_label(c)}")

    errors: dict[str, str] = {}
    if cfg.semantic.enabled and provider is not None:
        try:  # embeddings are optional: a gateway without an endpoint must not cost the review its context
            for chunk, score in semantic_matches(
                index, query, provider=provider, model=config.models.embedding, top_k=cfg.semantic.top_k,
                exclude=selection.keys, root=Path(root), cache_dir=_embedding_cache_dir(provider),
            ):
                selection.add("semantic", chunk, f"semantic match, similarity {score:.2f}")
        except Exception as e:
            log.warning("semantic search failed, continuing without it (set context.semantic.enabled: false "
                        "if the endpoint has no embeddings): %s", e)
            errors["semantic_error"] = f"{type(e).__name__}: {e}"

    kinds = [b.kind for b in selection.blocks]
    counts = {"indexed_files": len(index.files), "changed_functions": len(changed),
              "callers": kinds.count("caller"), "callees": kinds.count("callee"),
              "semantic_matches": kinds.count("semantic"), **errors}
    return selection.ordered(), counts


def _embedding_cache_dir(provider: "Provider") -> Path | None:
    """Where per-file embeddings are cached: next to the provider's own cache, so `--no-cache`,
    `cache.enabled: false` and the eval harness (which always runs uncached) switch both off together."""
    return provider.directory if isinstance(provider, CachedProvider) else None


class _Selection:
    """Chosen blocks, one per chunk. A chunk chosen twice keeps its first (highest-priority)
    kind and collects every reason."""

    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self._by_key: dict[tuple[str, str], Block] = {}

    @property
    def keys(self) -> set[tuple[str, str]]:
        return set(self._by_key)

    def add(self, kind: str, chunk: Chunk, reason: str) -> None:
        existing = self._by_key.get(chunk.key)
        if existing:
            if reason not in existing.reasons:
                existing.reasons.append(reason)
            return
        block = Block(kind, chunk, [reason])
        self._by_key[chunk.key] = block
        self.blocks.append(block)

    def ordered(self) -> list[Block]:
        return sorted(self.blocks, key=lambda b: PRIORITY[b.kind])  # stable: insertion order within a kind


def _label(c: Chunk) -> str:
    return f"{'function' if c.type == 'function' else c.type} `{c.qualname}`"


def changed_chunks(index: RepoIndex, files: list[FileDiff]) -> list[Chunk]:
    """Chunks overlapping an added line, or the new-side position of a deleted run (D3)."""
    out: list[Chunk] = []
    for f in files:
        sf = index.files.get(f.path)
        if sf is None or f.change_type == "deleted":
            continue
        touched = set(f.added_lines) | set(f.deleted_positions) | _deleted_inside(f, sf)
        out += sorted((c for c in sf.chunks if any(c.contains(n) for n in touched)), key=lambda c: c.line_start)
    return out


def _deleted_inside(f: FileDiff, sf: SourceFile) -> set[int]:
    """For a run of deleted lines, the line just before it, when the deleted code was indented
    deeper than the `def` of the function that line belongs to, i.e. the run was the tail of
    that function's body (the run's own position is already the line after the function)."""
    out: set[int] = set()
    for hunk in f.hunks:
        lines = hunk.lines
        for i, (marker, _, _) in enumerate(lines):
            if marker != "-" or i == 0 or lines[i - 1][0] == "-" or lines[i - 1][1] is None:
                continue
            j = i
            while j < len(lines) and lines[j][0] == "-":
                j += 1
            indents = [_indent(t) for _, _, t in lines[i:j] if t.strip()]
            prev = lines[i - 1][1]
            owner = next((c for c in sf.chunks if c.type != "class" and c.contains(prev)), None)
            if indents and owner and min(indents) > _indent(owner.lines[0][1]):
                out.add(prev)
    return out


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip())


def removed_names(files: list[FileDiff], index: RepoIndex) -> list[str]:
    """Functions and classes a deleted line defined that no longer exist anywhere (removed or renamed)."""
    names: dict[str, None] = {}
    for f in files:
        for hunk in f.hunks:
            for marker, _, text in hunk.lines:
                m = _REMOVED_DEF.match(text) if marker == "-" else None
                if m and m.group(1) not in index.defs:
                    names.setdefault(m.group(1))
    return list(names)


# -- rendering and budget --------------------------------------------------------------------

def render_block(block: Block, max_chars: int | None = None) -> str:
    """`=== path:start-end (reason)` then the numbered source. Gaps (a class's omitted method
    bodies) are marked; with `max_chars` the block is cut at a line boundary and says so."""
    c = block.chunk
    header = f"=== {c.file}:{c.line_start}-{c.line_end} ({'; '.join(block.reasons)})"
    out: list[str] = []
    blanks: list[str] = []
    prev_n: int | None = None
    for n, text in c.lines:
        line = f"{n:>5} | {text}".rstrip() if text.strip() else f"{n:>5} |"
        if prev_n is not None and n != prev_n + 1:
            blanks = []  # blank lines next to a gap carry no information
            out.append("      | ... (method bodies omitted)")
        if not text.strip():
            blanks.append(line)
        else:
            out.extend(blanks)
            blanks = []
            out.append(line)
        prev_n = n
    if max_chars is None or len(header) + 1 + sum(len(x) + 1 for x in out) <= max_chars:
        return "\n".join([header, *out])
    kept, used = [], len(header) + 1
    for line in out:
        if used + len(line) + 1 > max_chars - 60:  # leave room for the truncation note
            break
        kept.append(line)
        used += len(line) + 1
    note = f"      | ... (truncated: {len(out) - len(kept)} more line(s) not shown)"
    return "\n".join([header, *kept, note])


def assemble(blocks: list[Block], budget: int) -> tuple[str, list[tuple[Block, bool]], list[Block]]:
    """Fit blocks into `budget` characters in order. Returns the text, the included
    (block, was_truncated) pairs and the omitted blocks."""
    parts: list[str] = []
    included: list[tuple[Block, bool]] = []
    omitted: list[Block] = []
    used = 0
    exhausted = False
    for block in blocks:
        if exhausted:
            omitted.append(block)
            continue
        text = render_block(block)
        if used + len(text) <= budget:
            parts.append(text)
            included.append((block, False))
            used += len(text) + 2
        elif budget - used >= MIN_PARTIAL_CHARS:
            parts.append(render_block(block, budget - used))
            included.append((block, True))
            exhausted = True
        else:
            omitted.append(block)
            exhausted = True
    if omitted:
        where = ", ".join(f"{b.chunk.file}:{b.chunk.line_start}-{b.chunk.line_end}" for b in omitted[:5])
        more = f", and {len(omitted) - 5} more" if len(omitted) > 5 else ""
        parts.append(f"[context budget reached: {len(omitted)} lower-priority block(s) left out: {where}{more}]")
    return "\n\n".join(parts), included, omitted
