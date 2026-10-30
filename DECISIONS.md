# Decisions

A running log of choices made while building the POC, each with its reason. Terms follow `GLOSSARY.md`. The spec is `docs/plan.md`; where an entry here contradicts it, this file wins.

## D1: src-layout package named `reviewer`; eval folder named `evals/`
The repo was created with `uv init --package` (src layout, `uv_build`). We keep that and set `[tool.uv.build-backend] module-name = "reviewer"`, so the code lives in `src/reviewer/`. Because the package is installed, `uv run --project <this repo> python -m reviewer` works from any directory, which `--repo PATH` and the GitHub Action rely on. A flat `reviewer/` would only resolve from the project root. The eval harness is `evals/` at the root (unpackaged, run from the project root) so it doesn't share a name with the `eval` builtin.

## D2: Findings sit on the changed line that causes them
A finding's anchor is always a new-side line inside the diff, so it can be posted inline and counts as "introduced by this change". For cross-file breakage (e.g. pr09: `pricing.py` now returns cents, unchanged `invoices.py` still assumes dollars), the broken caller goes in the explanation and evidence. Expected issues may list `related: [{file, line_hint}]`, and the judge accepts a match at the anchor or at any related location. Without this, a correct cross-file catch would score as FP + FN.

## D3: Deletions count as changes
A chunk is a changed function if it overlaps an added line **or** the new-side position of a deleted line. Otherwise a change that only removes a safeguard (e.g. a `check_and_store` call) would leave the affected function out of the context bundle.

## D4: Expected issues use `area`, not `category`
In `labels.yaml` the field is `area: correctness|security|standards`. `Finding.category` stays a fine-grained kind (`logic`, `injection`, `rule`, …). The judge ignores both, because any specialist may catch any issue.

## D5: `requires` is the minimum required context
Each expected issue gets exactly one `requires` value: the least context that makes it catchable (`diff` < `rules` < `cross_file`). pr01 (missing auth on a new endpoint) is `diff`. This keeps the "context lifts rules/cross-file recall" result from being inflated by issues the diff already shows.

## D6: The baseline gets the same checklist as the specialists
The specialists' flag / do-not-flag lists closely match the kinds of bugs planted in the dataset. The baseline prompt therefore includes the same correctness + security lists, merged. The measured gap then comes from the pipeline's structure (context, specialization, verifier, triage), not from a better checklist. This is disclosed in the scorecard's methodology note.

## D7: The baseline's findings are scored raw; the no-verifier ablation is protected
Baseline findings are scored with no confidence threshold (just the per-reviewer cap), which matches how a single-prompt reviewer behaves in practice. To show the precision gain isn't just the threshold, `pipeline_no_verifier` moves ahead of `pipeline_no_context` when deciding what to cut: `pipeline_no_context` is cut first.

## D8: The judge uses a different model from the verifier
If the judge and verifier share a model, the judge may favour findings its own model already approved. We use a different model for the judge when the provider offers one, and list this under limitations either way.

## D9: This file is the only decision log
No `docs/adr/`. Decisions live here as short entries with reasons, so there's one place to read them and it doubles as demo material.

## D10: Models
Chosen from `models.list()` on the personal key, using OpenAI's pricing page (Oct 2026) and third-party quality indexes:
- **Specialists + baseline: `gpt-6.1-terra`** ($1 / $5 per 1M tokens in / out). About the price of gpt-5.4-mini but much stronger, with the lowest hallucination rate in the GPT-6 family. Hallucinated findings are the noise this project exists to cut. The baseline shares the model for fairness.
- **Verifier: `gpt-6.1-sol`** ($2 / $10). Strongest at a sane price, and a different model from the specialists, so the re-check is a genuinely second opinion. `gpt-6-astra` costs 5× more for about 2 index points; `gpt-5.5` costs 2.5× more and is older.
- **Judge: `gpt-4.1`** ($2 / $8). A different generation from both specialists and verifier (D8), so it can't favour either arm. Non-reasoning, takes `temperature: 0`, and matching findings to expected issues is an easy task. Validated by the judge agreement check.
- **Embeddings: `text-embedding-3-small`** ($0.02).
- Rejected: `gpt-6-luna` (79% hallucination rate), `gpt-5.6-*` (more expensive than the 6.x equivalents), `gpt-6-sol` (superseded by 6.1 at the same price).

Estimated about $0.10 per PR for the pipeline and $5–10 for a 3-run eval of both arms. M1 checks whether each model accepts `temperature` and `json_object` mode and records the result here.

## D11: The GitHub integration is a composite action in this public repo
`ai-pr-reviewer` is public. Its root `action.yml` is a composite action, so a repo being reviewed adds a short workflow with `uses: ckcabang/ai-pr-reviewer@main`. Alternatives rejected: checking out the reviewer a second time with a PAT (a credential to manage), and running the demo PRs inside this repo (repo- vs. service-relative path translation, and the Action would review its own PRs).

## D12: A separate demo repo, seeded by script
`ckcabang/ai-review-demo` (private): `main` is the base state of `demo_service/` with `REVIEW.md` at its root, plus a workflow that uses the action. `evals/seed_github.py` opens one branch + PR per selected dataset PR, defaulting to pr09 and pr06 (the ones in the demo script). Expected issues are never pushed. Seeding is part of M6, because M6 can't be tested without a PR to review.

## D13: One committed `config.yaml`; work settings live in the work copy
The work laptop runs a detached fork of this repo, not a clone, so internal gateway URLs and model names are committed only there and can't leak back here. No `config.local.yaml` layer. The only environment input is the API key, read from the variable named in `llm.api_key_env`.

## D14: Build on `poc-v1`, fast-forward `main` at the end
One commit per milestone (M1–M7) on `poc-v1`, pushed as a backup. `main` is fast-forwarded once the README dry-run in M7 passes, so `main` always matches what the README describes.

## D15: The Action reviews the PR head and posts via `--pr`
On `pull_request`, `actions/checkout` defaults to the merge commit, whose line numbers can differ from the head commit that reviews are posted against. The action instead checks out `pull_request.head.sha` (`fetch-depth: 0`) and runs `review --repo . --pr <url> --post`. `--pr` mode reviews in place when `--repo` is already at the head SHA, and creates a temporary worktree otherwise. Title and body come from the GitHub API inside Python, so no PR text ever reaches shell code.

## D16: Action inputs, never blocking
`action.yml` inputs: `openai-api-key` (required), `github-token` (defaults to `github.token`), `config` (optional path in the reviewed repo, defaulting to this repo's `config.yaml`), and `fail-on` (defaults to `never`). The check always passes and the review comments carry the signal, which matches v1's rule of never using `REQUEST_CHANGES` or `APPROVE`.

## D17: Eval results are saved; the scorecard rebuilds offline
Eval runs bypass the cache by design, so they cost money each time. `run_eval` writes every arm's raw findings, judge matches and stats to `evals/out/<timestamp>/results.json`, and `scorecard.py --from <dir>` rebuilds the HTML from that file with no API calls.

## D18: The live demo replays from the cache, labelled as such
The M7 dry-run fills the cache for pr09 and pr06. In the live demo, cached stats are marked "(cached)" (latency would otherwise look misleadingly low), and `--no-cache` is ready for a fresh run on request.

## D19: Demo service lives at `demo_service/`; one pytest run covers it and the repo tests
The service is `demo_service/app/...` (imports as `app`) with its own tests in `demo_service/tests/`, as the spec lays out. To avoid pytest collection clashes with the repo's `tests/` (same basenames such as `test_pricing.py`, two `conftest.py` files), `pyproject.toml` sets `addopts = "--import-mode=importlib"` and `pythonpath = ["demo_service"]`. `uv run pytest` runs both. The service tests use fixtures (not `import conftest`) for helpers, since importlib mode doesn't put test directories on `sys.path`. `httpx` was added as a dependency (the service's gateway/email clients use it, and FastAPI's `TestClient` needs it).

## D20: Dataset lives in `evals/dataset/`; one directory per PR, materialized into a real git repo
Each dataset PR is `evals/dataset/prs/<id>_<slug>/{meta.yaml, change.patch, labels.yaml}`; the patch is paths relative to `demo_service/`. `evals.dataset.materialize(pr, dest)` copies `demo_service/` as the base commit on `main`, applies the patch and commits it on branch `<id>`. Commits use a fixed author and date, so SHAs are deterministic (same base SHA for every PR), which keeps cache keys and demo output stable. `evals/dataset/integrity.py` checks every PR (patch applies, every `app.*` module imports, anchors fall inside a hunk of a changed file, related locations exist, no hint words in title/description, and clean PRs keep the service tests green). Anchors must be inside a hunk (D2), not merely in the right file. pytest's `pythonpath` now includes the repo root so tests can import `evals`.

## D21: Smoke-test results: the GPT-6.1 models reject `temperature`, all accept JSON mode
`scripts/smoke_api.py` (Oct 2026, personal key), probing each configured model with and without `response_format={"type": "json_object"}` and `temperature: 0`:
- **`gpt-6.1-terra` (specialists, baseline) and `gpt-6.1-sol` (verifier):** JSON mode accepted; `temperature: 0` rejected with a 400 ("only the default (1) value is supported"). The provider therefore retries once without `temperature` and remembers that per model for the rest of the process.
- **`gpt-4.1` (judge):** accepts both JSON mode and `temperature: 0`.
- **`text-embedding-3-small`:** works, 1536 dimensions.
- Per-model prices in `config.yaml` match OpenAI's pricing page (checked Oct 2026).

Consequence: specialists and verifier sample at temperature 1, so two uncached runs of the same PR differ. That is what the eval's multiple runs and mean ± std are for, and why the demo replays from the cache (D18).

## D22: The HTTP library is `httpx2`, not `httpx`
`openai` 3.x is built on `httpx2`, and an `httpx` client cannot be handed to it (plain `httpx` is only a direct dependency for the demo service's test client). Our own HTTP code (provider, later the GitHub client) uses `httpx2` (declared as a direct dependency), and tests fake HTTP with `httpx2.MockTransport`. The plan's "httpx" means this.

## D23: `Provider.chat_json` takes a `role`
The plan's protocol has no `role`, but the fake provider must answer by role, not by prompt hash. Callers pass `role` (`correctness`, `security`, `standards`, `verifier`, `judge`, `baseline`); real providers ignore it. The cache key stays model + prompts + temperature, as specified.

## D24: Config location and cache directory
The default config is `config.yaml` at the root of the reviewer's own project (found from the package location, so it works through `uv run --project` from any directory). `--config FILE` overrides it. A relative `cache.dir` resolves against the config file's directory, never the reviewed repo, so reviewing a repo does not write into it. Unknown config keys are an error, to catch typos.

## D25: Specialists see a line-numbered diff; anchors are checked in code
The diff in the prompt carries a gutter with new-side line numbers (deleted lines have none), because models cannot reliably derive them from hunk headers. A finding whose file or line is not a commentable line of the diff is dropped and counted in `stats.specialists.<name>.dropped_invalid_anchor` (D2). A deleted line's "position" is the new-side number of the line now occupying that spot, i.e. the first line after the deleted run.

## D26: Until triage exists, every finding is "review recommended"; no specialists left means exit 2
The walking skeleton has no verifier or triage: kept findings get `verdict: keep` and `triage: review_recommended`. The triage ticket replaces that. If every specialist fails there is no review to show, so `review()` raises and the CLI exits 2 rather than printing a falsely clean review; partial failures are recorded in stats and the other specialists' findings survive.

## D27: Batch A `requires` values were re-judged against the real patches
Two labels differ from the plan table. pr04's Idempotency-Key removal is `rules` rather than `cross_file`: the removed header and `check_and_store` call are visible in the diff, and only rule R5 says they are mandatory. pr05's capture retry is `diff` rather than `cross_file`: the diff itself shows a retried `POST /charges` without a key (and removes the docstring saying why capture is never retried). pr04's R9 issue stays `cross_file` because the harm (which states may be refunded) is defined in `order_state.py`. pr05's swallowed error also makes the existing `test_failed_capture_returns_generic_error_and_frees_the_key` fail on the patched tree, as pr09's invoice test does; integrity only requires green tests for clean PRs.
