"""Real-API smoke test (costs a few cents). Run from the project root:

    uv run --env-file .env python scripts/smoke_api.py

For every configured chat model it probes whether JSON mode and `temperature` are
accepted, then does one full JSON round trip through `OpenAIProvider` + `chat_validated`
(the production path), and finally one embedding call. Prints a table; paste the
JSON-mode / temperature columns into DECISIONS.md. Never prints the API key.
"""
from __future__ import annotations

import sys

import openai
from pydantic import BaseModel

from reviewer.config import load_config
from reviewer.llm import chat_validated
from reviewer.openai_provider import OpenAIProvider

SYSTEM = 'You answer with a single JSON object and nothing else, e.g. {"ok": true, "n": 2}.'
USER = 'Return the JSON object {"ok": true, "n": 3}.'


class Answer(BaseModel):
    ok: bool
    n: int


def probe(client: openai.OpenAI, model: str, *, json_mode: bool, temperature: bool) -> str:
    params: dict = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                                                 {"role": "user", "content": USER}]}
    if json_mode:
        params["response_format"] = {"type": "json_object"}
    if temperature:
        params["temperature"] = 0
    try:
        client.chat.completions.create(**params)
        return "ok"
    except openai.OpenAIError as e:
        return "REJECTED: " + " ".join(str(e).split())[:170]


def main() -> int:
    cfg = load_config()
    provider = OpenAIProvider.from_config(cfg)
    client = provider._client
    roles = {"specialist": cfg.models.specialist, "baseline": cfg.models.baseline,
             "verifier": cfg.models.verifier, "judge": cfg.models.judge}
    failures = 0
    seen: dict[str, str] = {}
    for role, model in roles.items():
        if model in seen:
            print(f"[{role}] {model}: same as {seen[model]}")
            continue
        seen[model] = role
        print(f"[{role}] {model}")
        for label, jm, temp in (("json_object + temperature=0", True, True),
                                ("json_object only", True, False),
                                ("temperature=0 only (no json mode)", False, True),
                                ("plain (neither)", False, False)):
            print(f"    {label:36} {probe(client, model, json_mode=jm, temperature=temp)}")
        try:
            answer, usage = chat_validated(provider, role=role, model=model, system=SYSTEM, user=USER,
                                           schema=Answer)
            cost = cfg.cost_usd(model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
            print(f"    provider round trip                  ok -> {answer} "
                  f"({usage.input_tokens} in / {usage.output_tokens} out, ${cost:.5f}, calls={usage.calls})")
        except Exception as e:  # noqa: BLE001 - report and continue
            failures += 1
            print(f"    provider round trip                  FAILED: {type(e).__name__}: {e}")
    try:
        vectors = provider.embed(model=cfg.models.embedding, texts=["def total(order): return order.subtotal"])
        print(f"[embedding] {cfg.models.embedding}: ok, {len(vectors)} vector(s) of {len(vectors[0])} dims")
    except Exception as e:  # noqa: BLE001
        failures += 1
        print(f"[embedding] {cfg.models.embedding}: FAILED: {type(e).__name__}: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
