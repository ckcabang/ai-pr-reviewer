"""Disk cache: a repeat review is served from disk and flagged as cached."""
import pytest

from reviewer.fake import FakeProvider
from reviewer.llm import CachedProvider
from reviewer.models import ReviewInput
from reviewer.pipeline import review
from reviewer.render import render_markdown

FINDING = {
    "file": "a.py", "line": 2, "category": "logic", "severity": "high", "confidence": 0.9,
    "title": "Bug", "explanation": "e", "evidence": "x = 2",
}


@pytest.fixture
def review_input(tmp_path, make_repo, diff_of):
    repo = make_repo(tmp_path / "repo", {"a.py": "x = 1\ny = 1\n"}, {"a.py": "x = 1\nx = 2\n"})
    return ReviewInput(repo_root=repo, diff=diff_of(repo), title="t")


def test_repeat_review_is_served_from_cache_and_flagged_cached(tmp_path, config, review_input):
    fake = FakeProvider({"correctness": {"findings": [FINDING]}})
    provider = CachedProvider(fake, tmp_path / "cache")

    first = review(review_input, provider=provider, config=config)
    second = review(review_input, provider=provider, config=config)

    assert len(fake.calls) == 1  # the second review never reached the model
    assert [f.id for f in second.findings] == [f.id for f in first.findings]
    assert first.stats["specialists"]["correctness"]["cached"] is False
    assert second.stats["specialists"]["correctness"]["cached"] is True
    assert second.stats["totals"]["cached"] is True
    # tokens (and so cost) are those of the original call, not zero
    assert second.stats["totals"]["input_tokens"] == first.stats["totals"]["input_tokens"] > 0
    assert "(cached)" in render_markdown(second)
    assert "(cached)" not in render_markdown(first)


def test_cache_is_keyed_by_model_prompts_and_temperature(tmp_path):
    fake = FakeProvider({"r": {"ok": True}})
    provider = CachedProvider(fake, tmp_path)
    ask = lambda **kw: provider.chat_json(**{"role": "r", "model": "m", "system": "s", "user": "u", **kw})  # noqa: E731

    ask()
    ask()
    assert len(fake.calls) == 1
    for change in ({"model": "m2"}, {"system": "s2"}, {"user": "u2"}, {"temperature": 0.5}):
        ask(**change)
    assert len(fake.calls) == 5


def test_a_corrupt_cache_entry_is_just_a_miss(tmp_path):
    fake = FakeProvider({"r": {"ok": True}})
    provider = CachedProvider(fake, tmp_path)
    provider.chat_json(role="r", model="m", system="s", user="u")
    [entry] = (tmp_path / "chat").glob("*.json")
    entry.write_text("{not json")

    data, usage = provider.chat_json(role="r", model="m", system="s", user="u")

    assert data == {"ok": True} and not usage.cached and len(fake.calls) == 2


def test_embeddings_are_cached_per_text(tmp_path):
    fake = FakeProvider()
    provider = CachedProvider(fake, tmp_path)

    first = provider.embed(model="e", texts=["a", "b"])
    again = provider.embed(model="e", texts=["b", "c"])

    assert again[0] == first[1]
    assert fake.embed_calls == [["a", "b"], ["c"]]  # only the unseen text went to the provider
