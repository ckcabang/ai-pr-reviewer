"""Config: the committed config.yaml loads every section; the key comes only from the environment."""
import pytest

from reviewer.config import ConfigError, default_config_path, load_config


def test_committed_config_loads_every_section():
    cfg = load_config()
    assert cfg.llm.api_key_env == "OPENAI_API_KEY"
    assert cfg.llm.json_mode is True
    assert cfg.llm.base_url is None and cfg.llm.ca_bundle is None
    assert (cfg.llm.timeout_s, cfg.llm.max_retries) == (120, 3)
    assert cfg.context.semantic.enabled and cfg.context.semantic.top_k == 8
    assert cfg.context.callgraph.max_callers == 5
    assert cfg.context.max_context_chars == 40000
    assert cfg.pipeline.max_findings_per_agent == 5
    assert cfg.pipeline.confidence_threshold == 0.6
    assert cfg.pipeline.action_required.severities == ["critical", "high"]
    assert cfg.pipeline.action_required.min_confidence == 0.75
    assert cfg.pipeline.max_findings_total == 10
    assert cfg.cache.enabled is True


def test_models_are_the_d10_choices_and_all_are_priced():
    cfg = load_config()
    assert cfg.models.specialist == "gpt-6.1-terra"
    assert cfg.models.baseline == cfg.models.specialist
    assert cfg.models.verifier == "gpt-6.1-sol"
    assert cfg.models.judge == "gpt-4.1"
    assert cfg.models.embedding == "text-embedding-3-small"
    for model in {cfg.models.specialist, cfg.models.verifier, cfg.models.judge, cfg.models.embedding}:
        assert model in cfg.pricing_per_1m_tokens, model
    assert cfg.pricing_per_1m_tokens["gpt-6.1-terra"].input == 1.0
    assert cfg.pricing_per_1m_tokens["gpt-6.1-terra"].output == 5.0


def test_cost_is_computed_from_configured_prices():
    cfg = load_config()
    # 2M in at $1/M + 1M out at $5/M
    assert cfg.cost_usd("gpt-6.1-terra", input_tokens=2_000_000, output_tokens=1_000_000) == pytest.approx(7.0)
    assert cfg.cost_usd("unpriced-model", input_tokens=10, output_tokens=10) == 0.0


def test_relative_cache_dir_is_resolved_against_the_reviewer_project_never_the_config_file(tmp_path):
    """A custom --config usually lives in the reviewed repo; its cache must not (D24)."""
    reviewed_repo = tmp_path / "reviewed-repo"
    reviewed_repo.mkdir()
    (reviewed_repo / "reviewer.yaml").write_text(
        "models: {specialist: a, verifier: b, baseline: a, judge: c, embedding: e}\n"
    )
    cfg = load_config(reviewed_repo / "reviewer.yaml")
    assert cfg.cache.dir == default_config_path().parent / ".cache"
    assert reviewed_repo not in cfg.cache.dir.parents
    assert load_config().cache.dir == cfg.cache.dir  # same place as for the default config
    assert cfg.llm.base_url is None  # sections fall back to defaults


def test_absolute_cache_dir_is_kept(tmp_path):
    (tmp_path / "c.yaml").write_text(
        f"models: {{specialist: a, verifier: b, baseline: a, judge: c, embedding: e}}\ncache: {{dir: {tmp_path}/mine}}\n"
    )
    assert load_config(tmp_path / "c.yaml").cache.dir == tmp_path / "mine"


def test_api_key_comes_only_from_the_environment_variable_named_in_config(monkeypatch):
    cfg = load_config()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert cfg.api_key() == "sk-test"
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        cfg.api_key()


def test_missing_or_invalid_config_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")
    (tmp_path / "bad.yaml").write_text("llm: {timeout_s: not-a-number}\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "bad.yaml")
