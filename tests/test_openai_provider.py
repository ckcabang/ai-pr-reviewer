"""OpenAI-compatible provider, tested at its HTTP boundary with a mock transport.

(`httpx2` is what the openai 3.x SDK is built on; plain `httpx` is not installed.)
"""
import json

import httpx2
import pytest
from pydantic import BaseModel

from reviewer.config import ConfigError, LLMConfig, load_config
from reviewer.llm import InvalidJSONError, ProviderError, ResponseValidationError, chat_validated
from reviewer.openai_provider import OpenAIProvider


def chat_reply(content, prompt_tokens=11, completion_tokens=7):
    return httpx2.Response(200, json={
        "id": "c1", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    })


def error_reply(status, message, param=None, code=None):
    return httpx2.Response(status, json={"error": {"message": message, "type": "x", "param": param, "code": code}})


TEMPERATURE_REJECTED = error_reply(
    400, "Unsupported value: 'temperature' does not support 0 with this model.", "temperature", "unsupported_value"
)


class Server:
    """Scripted HTTP endpoint: returns the next scripted response per request and records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses[min(len(self.requests), len(self.responses)) - 1]

    def body(self, i: int) -> dict:
        return json.loads(self.requests[i].content)


def make(server, *, json_mode=True, max_retries=3, sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    client = httpx2.Client(transport=httpx2.MockTransport(server))
    llm = LLMConfig(base_url="https://gateway.example/v1", json_mode=json_mode, max_retries=max_retries)
    return OpenAIProvider(llm, api_key="sk-test", http_client=client, sleep=sleeps.append)


def chat(provider, **kw):
    return provider.chat_json(role="correctness", model="m", system="sys JSON", user="usr", **kw)


def test_json_round_trip_sends_json_mode_and_temperature_and_returns_usage():
    server = Server(chat_reply('{"findings": []}'))
    data, usage = chat(make(server))

    assert data == {"findings": []}
    assert (usage.input_tokens, usage.output_tokens, usage.calls, usage.cache_hits) == (11, 7, 1, 0)
    req = server.requests[0]
    assert str(req.url) == "https://gateway.example/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-test"
    body = server.body(0)
    assert body["model"] == "m"
    assert body["messages"] == [{"role": "system", "content": "sys JSON"}, {"role": "user", "content": "usr"}]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0


def test_json_mode_can_be_switched_off_and_fenced_json_is_still_parsed():
    server = Server(chat_reply('Here you go:\n```json\n{"a": 1}\n```'))
    data, _ = chat(make(server, json_mode=False))
    assert data == {"a": 1}
    assert "response_format" not in server.body(0)


def test_non_json_reply_raises_invalid_json_error_with_the_raw_text():
    with pytest.raises(InvalidJSONError) as e:
        chat(make(Server(chat_reply("sorry, I cannot"))))
    assert e.value.raw == "sorry, I cannot"


def test_rate_limits_and_server_errors_are_retried_with_growing_backoff():
    server = Server(error_reply(429, "slow down"), error_reply(503, "overloaded"), chat_reply("{}"))
    sleeps: list[float] = []
    data, usage = chat(make(server, sleeps=sleeps))

    assert data == {} and len(server.requests) == 3
    assert len(sleeps) == 2 and 0 < sleeps[0] < sleeps[1]


def test_gives_up_after_max_retries():
    server = Server(error_reply(500, "down"))
    with pytest.raises(ProviderError):
        chat(make(server, max_retries=2))
    assert len(server.requests) == 3  # first try + 2 retries


def test_client_errors_are_not_retried():
    server = Server(error_reply(401, "bad key"), chat_reply("{}"))
    with pytest.raises(ProviderError, match="bad key"):
        chat(make(server))
    assert len(server.requests) == 1


def test_model_rejecting_temperature_is_retried_once_without_it_and_remembered():
    server = Server(TEMPERATURE_REJECTED, chat_reply("{}"), chat_reply("{}"))
    provider = make(server)

    chat(provider)
    assert "temperature" in server.body(0) and "temperature" not in server.body(1)
    assert len(server.requests) == 2

    chat(provider)  # remembered: no wasted rejected call
    assert len(server.requests) == 3 and "temperature" not in server.body(2)


class Answer(BaseModel):
    value: int


def test_validation_failure_gets_one_repair_call_that_includes_the_error():
    server = Server(chat_reply('{"value": "not a number"}'), chat_reply('{"value": 3}'))
    result, usage = chat_validated(make(server), role="r", model="m", system="s", user="task", schema=Answer)

    assert result.value == 3
    assert usage.calls == 2 and usage.input_tokens == 22
    repair_prompt = server.body(1)["messages"][1]["content"]
    assert repair_prompt.startswith("task") and "value" in repair_prompt and "not a number" in repair_prompt


def test_still_invalid_after_repair_raises_validation_error():
    server = Server(chat_reply("not json"), chat_reply('{"value": "x"}'))
    with pytest.raises(ResponseValidationError):
        chat_validated(make(server), role="r", model="m", system="s", user="task", schema=Answer)
    assert len(server.requests) == 2


def test_embeddings_return_vectors_in_input_order():
    server = Server(httpx2.Response(200, json={
        "object": "list", "model": "e",
        "data": [{"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
                 {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }))
    vectors = make(server).embed(model="e", texts=["a", "b"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert server.requests[0].url.path.endswith("/embeddings")
    assert server.body(0)["input"] == ["a", "b"] and server.body(0)["encoding_format"] == "float"


def test_from_config_reads_the_key_only_from_the_named_env_var(monkeypatch):
    cfg = load_config()
    cfg.llm.api_key_env = "MY_GATEWAY_KEY"
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)
    with pytest.raises(ConfigError, match="MY_GATEWAY_KEY"):
        OpenAIProvider.from_config(cfg)
    monkeypatch.setenv("MY_GATEWAY_KEY", "sk-x")
    assert OpenAIProvider.from_config(cfg) is not None


def test_missing_ca_bundle_is_a_clear_config_error(monkeypatch, tmp_path):
    cfg = load_config()
    cfg.llm.ca_bundle = str(tmp_path / "no-such-ca.pem")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    with pytest.raises(ConfigError, match="CA bundle"):
        OpenAIProvider.from_config(cfg)
