"""Provider for any OpenAI-compatible endpoint (OpenAI itself, or a corporate gateway).

Built on the `openai` SDK (3.x, which runs on `httpx2`). The SDK's own retries are turned
off; this module owns backoff so it is visible and testable.
"""
from __future__ import annotations

import logging
import ssl
import threading
import time
from pathlib import Path
from typing import Callable

import httpx2
import openai

from reviewer.config import Config, ConfigError, LLMConfig
from reviewer.llm import ProviderError, Usage, parse_json_text

log = logging.getLogger("reviewer")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
_BACKOFF_BASE_S = 1.0
_BACKOFF_MAX_S = 30.0


class _Rejected(ProviderError):
    """HTTP 400: the request itself was refused (not retried)."""

    def __init__(self, message: str, param: str | None):
        super().__init__(message)
        self.param = param

    def mentions(self, word: str) -> bool:
        return self.param == word or word in str(self).lower()


class OpenAIProvider:
    def __init__(
        self,
        llm: LLMConfig,
        *,
        api_key: str,
        http_client: httpx2.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.llm = llm
        self._sleep = sleep
        self._no_temperature: set[str] = set()  # models that rejected `temperature`
        self._lock = threading.Lock()
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=llm.base_url or DEFAULT_BASE_URL,  # never read OPENAI_BASE_URL (D13)
            timeout=llm.timeout_s,
            max_retries=0,
            http_client=http_client,
        )

    @classmethod
    def from_config(cls, config: Config) -> "OpenAIProvider":
        """Key from the env var named in config; optional corporate CA bundle."""
        llm = config.llm
        key = config.api_key()
        verify: ssl.SSLContext | bool = True
        if llm.ca_bundle:
            if not Path(llm.ca_bundle).is_file():
                raise ConfigError(f"CA bundle not found: {llm.ca_bundle} (llm.ca_bundle in the config)")
            verify = ssl.create_default_context(cafile=llm.ca_bundle)
        client = httpx2.Client(verify=verify, timeout=httpx2.Timeout(llm.timeout_s))
        return cls(llm, api_key=key, http_client=client)

    # -- Provider protocol -------------------------------------------------------------

    def chat_json(self, *, role, model, system, user, temperature=0.0):
        params: dict = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.llm.json_mode:
            params["response_format"] = {"type": "json_object"}
        send_temperature = model not in self._no_temperature
        if send_temperature:
            params["temperature"] = temperature
        try:
            resp = self._post(self._client.chat.completions.create, params)
        except _Rejected as e:
            if not (send_temperature and e.mentions("temperature")):
                raise
            log.info("model %s rejects temperature; retrying without it", model)
            with self._lock:
                self._no_temperature.add(model)
            params.pop("temperature")
            resp = self._post(self._client.chat.completions.create, params)

        usage = Usage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
            calls=1,
        )
        content = resp.choices[0].message.content if resp.choices else None
        return parse_json_text(content), usage

    def embed(self, *, model, texts):
        resp = self._post(self._client.embeddings.create, {"model": model, "input": texts,
                                                          "encoding_format": "float"})
        return [list(item.embedding) for item in sorted(resp.data, key=lambda d: d.index)]

    # -- internals ---------------------------------------------------------------------

    def _post(self, create: Callable, params: dict):
        """Call the SDK with exponential backoff on rate limits, server errors and
        connection problems; anything else becomes a ProviderError immediately."""
        attempt = 0
        while True:
            try:
                return create(**params)
            except (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError) as e:
                if attempt >= self.llm.max_retries:
                    raise ProviderError(f"gave up after {attempt + 1} attempts: {e}") from e
                delay = min(_BACKOFF_BASE_S * 2**attempt, _BACKOFF_MAX_S)
                log.info("provider error (%s); retrying in %.0fs", type(e).__name__, delay)
                self._sleep(delay)
                attempt += 1
            except openai.BadRequestError as e:
                raise _Rejected(str(e), getattr(e, "param", None)) from e
            except openai.OpenAIError as e:
                raise ProviderError(str(e)) from e
