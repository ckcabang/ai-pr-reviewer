"""Provider layer: the `Provider` protocol, usage accounting, validated chat calls and the
disk cache. The OpenAI-compatible implementation is in `openai_provider.py`; the fake
for tests is in `fake.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from reviewer.config import Config


class Usage(BaseModel):
    """Token usage of one or more provider calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    cache_hits: int = 0
    """How many of `calls` were answered from the disk cache (tokens are those of the
    original call, so cost estimates stay meaningful)."""

    @property
    def cached(self) -> bool:
        """True when every call was served from the cache (stats label these "(cached)")."""
        return self.calls > 0 and self.cache_hits == self.calls

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            calls=self.calls + other.calls,
            cache_hits=self.cache_hits + other.cache_hits,
        )


class Provider(Protocol):
    def chat_json(
        self, *, role: str, model: str, system: str, user: str, temperature: float = 0.0
    ) -> tuple[dict, Usage]:
        """One chat completion whose content is a JSON object. `role` names the caller
        (correctness, security, standards, verifier, judge, baseline); real providers
        ignore it, the fake answers by it. Raises `InvalidJSONError` if the reply is not
        a JSON object."""
        ...

    def embed(self, *, model: str, texts: list[str]) -> list[list[float]]: ...


class ProviderError(Exception):
    """A provider call failed for good (after retries)."""


class InvalidJSONError(ProviderError):
    """The model's reply was not a JSON object."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


class ResponseValidationError(ProviderError):
    """The model's reply failed validation even after the repair retry."""


def parse_json_text(text: str | None) -> dict:
    """Parse a model reply into a JSON object, tolerating ```json fences and surrounding
    prose (useful when JSON mode is off)."""
    raw = text or ""
    candidates = [raw.strip()]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fenced:
        candidates.append(fenced.group(1).strip())
    if "{" in raw and "}" in raw:
        candidates.append(raw[raw.index("{"): raw.rindex("}") + 1])
    for cand in candidates:
        try:
            value = json.loads(cand)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise InvalidJSONError("reply is not a JSON object", raw)


T = TypeVar("T", bound=BaseModel)


def chat_validated(
    provider: Provider,
    *,
    role: str,
    model: str,
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.0,
) -> tuple[T, Usage]:
    """Chat and validate the reply with pydantic. On invalid JSON or a validation error,
    make ONE repair call whose prompt includes the error; if that fails too, raise
    `ResponseValidationError`. Usage of all calls is summed."""
    usage = Usage()
    try:
        data, used = provider.chat_json(role=role, model=model, system=system, user=user, temperature=temperature)
        usage += used
        try:
            return schema.model_validate(data), usage
        except ValidationError as e:
            problem, previous = f"The JSON did not match the required format:\n{e}", json.dumps(data)
    except InvalidJSONError as e:
        problem, previous = f"The reply was not a valid JSON object ({e}).", e.raw

    repair_user = (
        f"{user}\n\n---\nYour previous reply was rejected.\n\n"
        f"Previous reply:\n{previous}\n\nProblem:\n{problem}\n\n"
        "Reply again with a single corrected JSON object that follows the output format exactly."
    )
    try:
        data, used = provider.chat_json(role=role, model=model, system=system, user=repair_user,
                                        temperature=temperature)
        usage += used
        return schema.model_validate(data), usage
    except (InvalidJSONError, ValidationError) as e:
        raise ResponseValidationError(f"invalid reply even after one repair retry: {e}") from e


class CachedProvider:
    """Disk cache in front of any provider. Chat is keyed by sha256(model, system, user,
    temperature); embeddings by sha256(model, text). Hits are flagged in the usage."""

    def __init__(self, inner: Provider, directory: Path):
        self.inner = inner
        self.directory = Path(directory)

    def chat_json(self, *, role, model, system, user, temperature=0.0):
        path = self._path("chat", [model, system, user, temperature])
        hit = self._read(path)
        if hit is not None:
            usage = Usage(**hit["usage"], calls=1, cache_hits=1)
            return hit["response"], usage
        data, usage = self.inner.chat_json(role=role, model=model, system=system, user=user,
                                           temperature=temperature)
        self._write(path, {"response": data,
                           "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}})
        return data, usage

    def embed(self, *, model, texts):
        paths = [self._path("embed", [model, t]) for t in texts]
        vectors: list[list[float] | None] = []
        for p in paths:
            hit = self._read(p)
            vectors.append(hit["vector"] if hit is not None else None)
        missing = [i for i, v in enumerate(vectors) if v is None]
        if missing:
            fresh = self.inner.embed(model=model, texts=[texts[i] for i in missing])
            for i, vec in zip(missing, fresh):
                vectors[i] = vec
                self._write(paths[i], {"vector": vec})
        return vectors  # type: ignore[return-value]

    def _path(self, kind: str, parts: list) -> Path:
        key = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
        return self.directory / kind / f"{key}.json"

    @staticmethod
    def _read(path: Path):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None  # missing or corrupt entries are plain misses

    @staticmethod
    def _write(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)  # atomic, so parallel workers never read half a file


def build_provider(config: Config, *, use_cache: bool = True, inner: Provider | None = None) -> Provider:
    """The provider the CLI uses: the real OpenAI-compatible one (reads the API key from
    the environment) unless `inner` is given, wrapped in the disk cache unless it is
    disabled in config or by `use_cache=False` (`--no-cache`)."""
    if inner is None:
        from reviewer.openai_provider import OpenAIProvider

        inner = OpenAIProvider.from_config(config)
    if use_cache and config.cache.enabled:
        return CachedProvider(inner, config.cache.dir)
    return inner
