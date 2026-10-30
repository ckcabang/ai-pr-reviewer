"""Fake provider for offline tests: answers by ROLE with scripted responses (never by
prompt hash, so rewording a prompt does not break fixtures)."""
from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import dataclass
from typing import Any

from reviewer.llm import Usage, parse_json_text


@dataclass
class Call:
    role: str
    model: str
    system: str
    user: str
    temperature: float


class FakeProvider:
    """`scripts` maps a role (e.g. "correctness", "verifier", "judge", "baseline") to what
    that role answers:

    - a dict: returned on every call;
    - a list: consumed one item per call (running out is an error);
    - an Exception: raised on every call;
    - a callable `(Call) -> dict | Exception`.

    A list item may also be an Exception (raised) or a str (raw text, parsed like a real
    reply, so a non-JSON string raises `InvalidJSONError`). A role with no script raises
    unless `default` is given. Every call is recorded in `calls`.
    """

    def __init__(self, scripts: dict[str, Any] | None = None, *, default: dict | None = None,
                 embedding_dim: int = 16):
        self.scripts = dict(scripts or {})
        self.default = default
        self.embedding_dim = embedding_dim
        self.calls: list[Call] = []
        self.embed_calls: list[list[str]] = []
        self._lock = threading.Lock()

    def calls_for(self, role: str) -> list[Call]:
        return [c for c in self.calls if c.role == role]

    def chat_json(self, *, role, model, system, user, temperature=0.0):
        call = Call(role, model, system, user, temperature)
        with self._lock:
            self.calls.append(call)
            reply = self._next_reply(call)
        if isinstance(reply, Exception):
            raise reply
        data = parse_json_text(reply) if isinstance(reply, str) else reply
        usage = Usage(
            input_tokens=math.ceil((len(system) + len(user)) / 4),
            output_tokens=math.ceil(len(json.dumps(data)) / 4) if isinstance(data, dict) else 1,
            calls=1,
        )
        return data, usage

    def embed(self, *, model, texts):
        with self._lock:
            self.embed_calls.append(list(texts))
        return [self._vector(t) for t in texts]

    def _next_reply(self, call: Call):
        script = self.scripts.get(call.role, self.default)
        if script is None:
            raise AssertionError(f"FakeProvider has no script for role {call.role!r}")
        if callable(script) and not isinstance(script, (dict, list)):
            return script(call)
        if isinstance(script, list):
            idx = sum(1 for c in self.calls if c.role == call.role) - 1
            if idx >= len(script):
                raise AssertionError(f"FakeProvider script for {call.role!r} ran out at call {idx + 1}")
            return script[idx]
        return script

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [(digest[i % len(digest)] - 127.5) / 127.5 for i in range(self.embedding_dim)]
