"""Semantic search: chunks of the repository most similar to the change, found with embeddings.

Each chunk is embedded as `qualname + "\\n" + source`; the query is `title + description + diff`.
Cosine similarity is plain numpy (no vector database). Chunks already chosen for the bundle are
excluded before the top-k is taken, so k new chunks come back.

Embeddings are cached per file: one JSON file per (embedding model, file content hash) holding
the vectors of that file's chunks, so an unchanged file is never embedded twice, even when the
branch changes around it.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from reviewer.context.chunker import Chunk
from reviewer.llm import write_json_atomic

if TYPE_CHECKING:
    from reviewer.context.callgraph import RepoIndex
    from reviewer.llm import Provider

log = logging.getLogger("reviewer")

MAX_TEXT_CHARS = 8000       # per text; embedding models take ~8k tokens, code runs ~3 chars/token
MAX_BATCH_TEXTS = 128
MAX_BATCH_CHARS = 150_000   # per request, far below the API's per-request token limit
_CACHE_VERSION = "1"        # bump when the embedded text format changes


def semantic_matches(
    index: "RepoIndex", query: str, *, provider: "Provider", model: str, top_k: int,
    exclude: set[tuple[str, str]], root: Path, cache_dir: Path | None = None,
) -> list[tuple[Chunk, float]]:
    """The `top_k` chunks most similar to `query`, best first, as (chunk, cosine similarity).
    Chunks whose key is in `exclude` are not candidates. Raises whatever the provider raises."""
    candidates = [c for key, c in index.chunks.items() if key not in exclude]
    if top_k <= 0 or not candidates:
        return []
    vectors = _chunk_vectors(index, candidates, provider=provider, model=model, root=root, cache_dir=cache_dir)
    [query_vector] = provider.embed(model=model, texts=[query[:MAX_TEXT_CHARS]])
    scores = _unit(np.array(vectors, dtype=float)) @ _unit(np.array(query_vector, dtype=float))
    order = sorted(range(len(candidates)), key=lambda i: (-scores[i], candidates[i].file, candidates[i].line_start))
    return [(candidates[i], float(scores[i])) for i in order[:top_k]]


def chunk_text(chunk: Chunk) -> str:
    return f"{chunk.qualname}\n{chunk.source}"[:MAX_TEXT_CHARS]


def _unit(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(norms == 0, 1.0, norms)


# -- per-file cache -------------------------------------------------------------------------

def _chunk_vectors(index, candidates: list[Chunk], *, provider, model, root, cache_dir) -> list[list[float]]:
    """Vectors for `candidates`, in order. Per file: from the cache when the file's content
    hash is known, otherwise embedded (all files' misses together, in batches) and stored."""
    by_file: dict[str, list[Chunk]] = {}
    for c in index.chunks.values():  # every chunk of a file, so the cached entry is the whole file
        by_file.setdefault(c.file, []).append(c)
    vectors: dict[tuple[str, str], list[float]] = {}
    misses: list[tuple[str, Path | None, list[Chunk]]] = []
    for path in sorted({c.file for c in candidates}):
        chunks = by_file[path]
        entry = _entry_path(cache_dir, model, root / path)
        cached = _read_entry(entry, chunks)
        if cached is not None:
            vectors.update({(path, qual): vec for qual, vec in cached.items()})
        else:
            misses.append((path, entry, chunks))

    todo = [(path, c) for path, _, chunks in misses for c in chunks]
    fresh = _embed_batched(provider, model, [chunk_text(c) for _, c in todo])
    for (path, c), vec in zip(todo, fresh):
        vectors[(path, c.qualname)] = vec
    for path, entry, chunks in misses:
        if entry is not None:
            _write_entry(entry, {c.qualname: vectors[(path, c.qualname)] for c in chunks})
    return [vectors[c.key] for c in candidates]


def _embed_batched(provider, model: str, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    batch: list[str] = []
    size = 0
    for text in texts:
        if batch and (len(batch) >= MAX_BATCH_TEXTS or size + len(text) > MAX_BATCH_CHARS):
            out += provider.embed(model=model, texts=batch)
            batch, size = [], 0
        batch.append(text)
        size += len(text)
    if batch:
        out += provider.embed(model=model, texts=batch)
    if len(out) != len(texts):
        raise ValueError(f"embedding provider returned {len(out)} vectors for {len(texts)} texts")
    return out


def _entry_path(cache_dir: Path | None, model: str, file: Path) -> Path | None:
    if cache_dir is None:
        return None
    try:
        content = file.read_bytes()
    except OSError:
        return None
    key = hashlib.sha256(b"\0".join([_CACHE_VERSION.encode(), model.encode(), content])).hexdigest()
    return Path(cache_dir) / "semantic" / f"{key}.json"


def _read_entry(entry: Path | None, chunks: list[Chunk]) -> dict[str, list[float]] | None:
    """The cached vectors, only if they cover exactly this file's chunks (anything else is a miss)."""
    if entry is None:
        return None
    try:
        data = json.loads(entry.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or set(data) != {c.qualname for c in chunks}:
        return None
    return data


def _write_entry(entry: Path, vectors: dict[str, list[float]]) -> None:
    try:
        write_json_atomic(entry, vectors)
    except OSError as e:  # a cache that cannot be written is only a missed speed-up
        log.warning("could not write the embedding cache %s: %s", entry, e)
