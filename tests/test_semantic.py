"""Semantic search in the context bundle, observed at Seam 1 (`review()` with a fake provider
that answers embeddings from a script)."""
import re
import textwrap

import pytest

from reviewer.context import build_context
from reviewer.diff import parse_diff
from reviewer.fake import FakeProvider
from reviewer.llm import CachedProvider, ProviderError
from reviewer.models import ReviewInput
from reviewer.pipeline import review
from reviewer.render import render_markdown


def src(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


PRICING_BASE = src('''
    from decimal import Decimal

    TAX = Decimal("0.10")


    def calculate_tax(amount):
        return amount * TAX


    def get_order_total(subtotal):
        return subtotal + calculate_tax(subtotal)
''')
PRICING_HEAD = PRICING_BASE.replace("return subtotal + calculate_tax(subtotal)",
                                    "return int((subtotal + calculate_tax(subtotal)) * 100)")

# Unrelated to the change by calls, but about the same subject: only embeddings find it.
VAT = src('''
    def vat_rate(country):
        """Tax rate for a country: tax tax tax."""
        return {"DE": 19, "FR": 20}.get(country, 0)
''')
USERS = src('''
    def greet(name):
        return "hello " + name
''')
MAILER = src('''
    def send_welcome(address):
        return "mail to " + address
''')

BASE = {"app/__init__.py": "", "app/pricing.py": PRICING_BASE, "app/vat.py": VAT,
        "app/users.py": USERS, "app/mailer.py": MAILER}
HEAD = {"app/pricing.py": PRICING_HEAD}

KEYWORDS = ["tax", "greet", "mail"]


class KeywordEmbedder(FakeProvider):
    """Scripted embeddings: a text's vector counts how often each keyword occurs in it, so
    texts about the same subject are close and the test decides what is 'similar'."""

    def embed(self, *, model, texts):
        with self._lock:
            self.embed_calls.append(list(texts))
        return [[float(t.lower().count(k)) + 0.001 for k in KEYWORDS] for t in texts]


@pytest.fixture
def build(tmp_path, make_repo, diff_of):
    count = iter(range(1000))

    def _build(base=BASE, head=HEAD):
        repo = make_repo(tmp_path / f"repo{next(count)}", base, head)
        return ReviewInput(repo_root=repo, diff=diff_of(repo), title="Fix tax in the order total",
                           description="Totals are computed in cents now.")
    return _build


@pytest.fixture
def sem(config):
    config.context.semantic.enabled = True
    return config


def prompt_of(provider, review_input, config) -> str:
    review(review_input, provider=provider, config=config)
    [call] = provider.calls_for("correctness")
    return call.user


def new_provider() -> KeywordEmbedder:
    return KeywordEmbedder({"correctness": {"findings": []}})


def headers(prompt: str) -> list[str]:
    return re.findall(r"^=== (.*)$", prompt, re.M)


def test_similar_code_that_nothing_calls_joins_the_bundle_labelled_as_a_semantic_match(build, sem):
    sem.context.semantic.top_k = 1
    provider = new_provider()

    result_prompt = prompt_of(provider, build(), sem)

    [match] = [h for h in headers(result_prompt) if "semantic match" in h]
    assert match.startswith("app/vat.py:1-3 (semantic match")
    assert "    2 |     \"\"\"Tax rate for a country: tax tax tax.\"\"\"" in result_prompt
    assert "greet" not in result_prompt  # top_k = 1: only the closest chunk


def test_what_is_embedded_is_qualname_plus_source_for_chunks_and_title_description_diff_for_the_query(build, sem):
    provider = new_provider()
    review_input = build()

    prompt_of(provider, review_input, sem)

    *chunk_batches, [query] = provider.embed_calls
    chunk_texts = [t for batch in chunk_batches for t in batch]
    assert 'vat_rate\ndef vat_rate(country):\n    """Tax rate for a country: tax tax tax."""' in "\n".join(chunk_texts)
    assert query.startswith("Fix tax in the order total\nTotals are computed in cents now.\n")
    assert review_input.diff in query


def test_chunks_already_in_the_bundle_are_not_repeated_as_semantic_matches(build, sem):
    sem.context.semantic.top_k = 8  # more than there are chunks left over
    provider = new_provider()

    prompt = prompt_of(provider, build(), sem)

    seen = [h.split(" (")[0] for h in headers(prompt)]
    assert len(seen) == len(set(seen))
    [callee] = [h for h in headers(prompt) if h.startswith("app/pricing.py:6-7")]  # calculate_tax: the most similar
    assert "called by changed function `get_order_total`" in callee and "semantic" not in callee
    assert sum("semantic match" in h for h in headers(prompt)) >= 2  # the rest of the repo


def test_semantic_matches_are_the_first_thing_the_budget_drops(build, sem):
    sem.context.max_context_chars = 330  # the changed function and its callee fit, the semantic blocks do not
    provider = new_provider()

    result = review(build(), provider=provider, config=sem)

    prompt = provider.calls_for("correctness")[0].user
    kept, _, note = prompt.partition("[context budget reached:")
    assert "(changed function `get_order_total`)" in kept and "called by changed" in kept
    assert "semantic match" not in kept
    assert "app/vat.py:1-3" in note
    assert result.stats["context"]["omitted_blocks"] >= 1


def test_stats_count_the_semantic_matches(build, sem):
    sem.context.semantic.top_k = 2

    stats = review(build(), provider=new_provider(), config=sem).stats["context"]

    assert stats["semantic_matches"] == 2
    assert "semantic_error" not in stats


def test_markdown_footer_mentions_semantic_matches_only_when_there_are_some(build, sem):
    sem.context.semantic.top_k = 2
    md = render_markdown(review(build(), provider=new_provider(), config=sem))
    assert "- Context: 1 changed, 0 callers, 1 callees, 2 semantic, 0 rules (" in md

    sem.context.semantic.enabled = False
    md = render_markdown(review(build(), provider=new_provider(), config=sem))
    assert "- Context: 1 changed, 0 callers, 1 callees, 0 rules (" in md


def test_with_semantic_search_disabled_no_embedding_call_is_made(build, sem):
    sem.context.semantic.enabled = False
    provider = new_provider()

    prompt = prompt_of(provider, build(), sem)

    assert provider.embed_calls == []
    assert "semantic match" not in prompt
    assert "(changed function `get_order_total`)" in prompt  # the rest of the bundle is unchanged


def test_no_provider_means_no_semantic_search(build, sem):
    review_input = build()

    bundle = build_context(review_input, parse_diff(review_input.diff), sem)  # what the `context` command does

    assert bundle.stats["semantic_matches"] == 0
    assert "semantic match" not in bundle.code
    assert "(changed function `get_order_total`)" in bundle.code


def test_an_embeddings_endpoint_that_fails_costs_the_review_only_the_semantic_blocks(build, sem):
    class NoEmbeddings(KeywordEmbedder):
        def embed(self, *, model, texts):
            raise ProviderError("404 Not Found: /embeddings")

    provider = NoEmbeddings({"correctness": {"findings": []}})

    result = review(build(), provider=provider, config=sem)

    prompt = provider.calls_for("correctness")[0].user
    assert "(changed function `get_order_total`)" in prompt and "called by changed" in prompt
    assert "semantic match" not in prompt
    assert "ProviderError" in result.stats["context"]["semantic_error"]
    assert result.stats["context"]["semantic_matches"] == 0


# -- cache: per file content hash -------------------------------------------------------------------

class FileCacheOnly(CachedProvider):
    """The CLI's cached provider, minus its per-text embedding cache, so that what reaches the
    inner provider is exactly what the per-file cache lets through."""

    def embed(self, *, model, texts):
        return self.inner.embed(model=model, texts=texts)


@pytest.fixture
def cached(tmp_path):
    inner = new_provider()
    return FileCacheOnly(inner, tmp_path / "cache"), inner


def embedded_chunks(inner: KeywordEmbedder, after: int = 0) -> str:
    """The chunk texts embedded since call number `after` (the query is not a chunk)."""
    return "\n".join(t for batch in inner.embed_calls[after:] for t in batch if not t.startswith("Fix tax"))


def test_unchanged_files_are_not_embedded_again(build, sem, cached):
    provider, inner = cached
    review_input = build()

    review(review_input, provider=provider, config=sem)
    first = len(inner.embed_calls)
    review(review_input, provider=provider, config=sem)

    assert "vat_rate" in embedded_chunks(inner, 0)
    assert embedded_chunks(inner, first) == ""  # only the query went out the second time


def test_an_edited_file_is_embedded_again_and_only_that_file(build, sem, cached):
    provider, inner = cached
    review_input = build()
    review(review_input, provider=provider, config=sem)
    first = len(inner.embed_calls)

    vat = review_input.repo_root / "app" / "vat.py"
    vat.write_text(vat.read_text().replace("19", "21"))
    review(review_input, provider=provider, config=sem)

    again = embedded_chunks(inner, first)
    assert "vat_rate" in again and '"DE": 21' in again
    assert "greet" not in again and "send_welcome" not in again


def test_cached_vectors_are_per_embedding_model(build, sem, cached):
    provider, inner = cached
    review_input = build()
    review(review_input, provider=provider, config=sem)
    first = len(inner.embed_calls)

    sem.models.embedding = "another-embedding-model"
    review(review_input, provider=provider, config=sem)

    assert "vat_rate" in embedded_chunks(inner, first)  # vectors of one model are never reused for another


def test_without_a_disk_cache_every_review_embeds_the_repository(build, sem):
    provider = new_provider()  # not a CachedProvider: `--no-cache`, `cache.enabled: false`, evals
    review_input = build()

    review(review_input, provider=provider, config=sem)
    first = len(provider.embed_calls)
    review(review_input, provider=provider, config=sem)

    assert "vat_rate" in embedded_chunks(provider, first)
