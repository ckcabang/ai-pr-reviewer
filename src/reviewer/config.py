"""Configuration: one committed `config.yaml` (D13), validated with pydantic.

The only environment input is the API key, read from the variable named in
`llm.api_key_env`. Model names exist only in the config (no defaults here).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError


class ConfigError(Exception):
    """Bad or missing configuration (including a missing API key)."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMConfig(_Section):
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    ca_bundle: str | None = None
    timeout_s: float = 120
    max_retries: int = 3
    json_mode: bool = True


class ModelsConfig(_Section):
    specialist: str
    verifier: str
    baseline: str
    judge: str
    embedding: str


class Price(_Section):
    input: float = 0.0
    output: float = 0.0


class SemanticConfig(_Section):
    enabled: bool = True
    top_k: int = 8


class CallgraphConfig(_Section):
    enabled: bool = True
    max_callers: int = 5
    max_callees: int = 5


class ContextConfig(_Section):
    semantic: SemanticConfig = SemanticConfig()
    callgraph: CallgraphConfig = CallgraphConfig()
    max_context_chars: int = 40000


class ActionRequiredConfig(_Section):
    severities: list[Literal["critical", "high", "medium", "low"]] = ["critical", "high"]
    min_confidence: float = 0.75


class PipelineConfig(_Section):
    max_workers: int = 4
    max_findings_per_agent: int = 5
    confidence_threshold: float = 0.6
    action_required: ActionRequiredConfig = ActionRequiredConfig()
    max_findings_total: int = 10


class CacheConfig(_Section):
    enabled: bool = True
    dir: Path = Path(".cache")
    """Relative paths are resolved against the config file's directory by `load_config`."""


class Config(_Section):
    llm: LLMConfig = LLMConfig()
    models: ModelsConfig
    pricing_per_1m_tokens: dict[str, Price] = {}
    context: ContextConfig = ContextConfig()
    pipeline: PipelineConfig = PipelineConfig()
    cache: CacheConfig = CacheConfig()

    def api_key(self) -> str:
        """The API key, read from the environment variable named in `llm.api_key_env`."""
        name = self.llm.api_key_env
        value = os.environ.get(name)
        if not value:
            raise ConfigError(f"API key not set: export {name} (named by llm.api_key_env in the config)")
        return value

    def cost_usd(self, model: str, *, input_tokens: int, output_tokens: int) -> float:
        """Estimated cost from the configured per-1M-token prices (0.0 for unpriced models)."""
        price = self.pricing_per_1m_tokens.get(model)
        if price is None:
            return 0.0
        return (input_tokens * price.input + output_tokens * price.output) / 1_000_000


def default_config_path() -> Path:
    """`config.yaml` at the root of the reviewer's own project (editable install / checkout)."""
    return Path(__file__).resolve().parents[2] / "config.yaml"


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate a config file (default: the committed `config.yaml`)."""
    path = Path(path) if path is not None else default_config_path()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except OSError as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e
    try:
        cfg = Config.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"invalid config {path}:\n{e}") from e
    if not cfg.cache.dir.is_absolute():
        cfg.cache.dir = (path.resolve().parent / cfg.cache.dir)
    return cfg
