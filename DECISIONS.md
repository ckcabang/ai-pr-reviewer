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
Two labels differ from the plan table. pr04's Idempotency-Key removal is `rules` rather than `cross_file`: the removed header and `check_and_store` call are visible in the diff, and only rule R5 says they are mandatory. pr05's capture retry is `diff` rather than `cross_file`: the diff itself shows a retried `POST /charges` without a key (and removes the docstring saying why capture is never retried). pr08's missing auth on the export endpoint is `diff`, matching pr01 (D5): a new route with no `get_current_user` dependency is visible in the diff alone. pr04's R9 issue stays `cross_file` because the harm (which states may be refunded) is defined in `order_state.py`. pr05's swallowed error also makes the existing `test_failed_capture_returns_generic_error_and_frees_the_key` fail on the patched tree, as pr09's invoice test does; integrity only requires green tests for clean PRs.

## D28: PR mode details: `GITHUB_TOKEN`, the API host comes from the PR URL, the diff is `base_sha...head_sha`
`review --pr URL` reads the token from `GITHUB_TOKEN` (the name Actions and the plan use) and errors with exit 2 if it is unset; the token is never put in error messages. The API base is derived from the URL: `github.com` uses `api.github.com`, any other host (GitHub Enterprise) uses `https://HOST/api/v3`, so the work laptop needs no extra setting. The diff is `base.sha...head.sha` from the API response, so it is the merge-base diff GitHub shows even when main has moved on, and the commentable lines match GitHub's. Missing commits are fetched (`git fetch origin pull/N/head`; the base by SHA) without touching any local ref. `--title` and `--description-file` are rejected with `--pr`, so there is one source of PR text.

## D29: Posting one review: summary lists inline findings, GitHub's 422 falls back to summary-only
`--post` sends one `COMMENT` review for the head commit. Inline comments (single line, RIGHT side) go only on commentable lines; the summary spells out every other finding in full and just lists the inline ones. A review with no findings is still posted (it shows the AI ran). If GitHub rejects the review with 422 (its idea of the diff differs from ours), it is resent once with no inline comments and everything in the summary, so a finding is never lost and the check does not fail on a line-mapping quirk. Posting happens after the output is written, so a failed post still leaves the review on stdout.

## D30: The verifier fails open, and judges what it can verify
A verifier batch that errors (or a finding the verifier returns no verdict for) keeps its findings as proposed, with `verifier_reason` saying so and `stats.stages.verifier.status` set to `partial`/`failed` (shown in the Markdown footer). Losing a review because the second opinion was down is worse than showing unverified findings; triage's confidence threshold still applies. Its inputs are the finding text plus a numbered ±15-line window of the head file (added lines marked `+`), the team rules and the don't-flag list; it does not get the context bundle. A `duplicate_of` that is unknown, self-referential, points at a dropped finding, or is mutual (the more confident one survives) is ignored, so a model slip cannot erase a real finding.

## D31: Triage details the spec left open
Near-duplicates (same file and category, lines within 3) are collapsed greedily from the highest confidence down, so the survivor is the most confident, not the highest severity. Every dropped finding stays in the output with `triage_reason` (JSON includes them; Markdown does not). The cap counts only kept findings and is reported as `stats.stages.triage.cap_removed`; other drop counts are in `dropped_by_reason`. Verifier skipping (`review(..., verify=False)`) is the ablation switch: findings keep their own confidence and still pass through triage.

## D32: The baseline reuses the specialist scaffold; its tier is only a label
`reviewer.agents.baseline.review_baseline` has the same signature as `review` and runs one diff-only prompt (the correctness and security checklists merged, D6) on `models.baseline`, with the specialists' output contract, per-reviewer cap and anchor check (D7: nothing else is removed). Its findings carry `triage: action_required` when severity and confidence meet the pipeline's `action_required` rule and `review_recommended` otherwise, so "clean PRs with an action-required finding" is the same measurement for both arms; no baseline finding is ever dropped. A failed model call raises (the eval records an error) instead of returning a clean-looking empty review.

## D33: The judge decides sameness; location and one-to-one are checked in code
The judge model only scores "same underlying problem?" per (expected issue, finding) pair and sees descriptions and locations, never area, severity or required context (D4). Code then rejects any pair that is not in the same file as the anchor or a related location (D2) and either within 10 lines of it or inside the same function (resolved with `ast` on the materialized HEAD), and assigns greedily and one-to-one at score >= 0.5. Equal scores go to the pair with fewer alternatives, then list position, so the result does not depend on the order the judge listed pairs. Findings are addressed to the judge as F1..Fn (not by hash id, which two findings of one reviewer can share). Rejected, weak and superseded pairs are all kept in the results (`judge.candidates[].status`).

## D34: Eval scoring rules
Only findings that are not `dropped` are judged (what a user would see); dropped ones stay in the results for debugging. Metrics are pooled over PRs within a run (micro-averaged), then mean and sample std over runs (std is null for one run); undefined ratios (no findings, no expected issues in a bucket) are null, never 0. A review or judge call that fails is recorded (`status: error | judge_error`), excluded from the metrics and reported loudly, never scored as an empty review. Arms are plain functions registered in `evals.arms.ARMS`; `results.json` is the single source for the scorecard, which re-derives every metric from the raw records.

## D35: The call graph is name-based, with cheap qualification to cut noise
There is no type inference. A call `foo(...)` or `x.foo(...)` links to every function, method or class named `foo`, and the caller/callee search is ranked rather than exact. A match is strong when the call is qualified like the target (`pricing.foo()` for a function in `pricing.py`, `from pricing import foo`, a bare call within the same file, `self.foo()` inside the class, `Class.foo()`); otherwise it is weak, and a weak match is used only when the name is defined once in the repo and is not a ubiquitous name (`get`, `execute`, `send`, ...). Callers rank strong before weak, then production code before tests; callees rank strong before weak, then rarely-called helpers before utilities such as `db.execute`, then call order. Known misses: dynamic dispatch, aliased imports (`from m import f as g`), attribute access without a call (properties), HTTP routes (called by the framework, so route handlers have no callers). A function the diff removed or renamed is searched by its old name, so unchanged code still calling it is shown as a stale reference. Changed test functions are shown but not expanded into callers and callees.

## D36: Team rules are read from the base revision
`REVIEW.md` is read with `git show <base>:REVIEW.md` (`ReviewInput.base_ref`: `--base REF` in base mode, the PR's base SHA in PR mode), and only if the base has none does the checkout's file count. Otherwise a PR could edit or empty the rules it is judged by, and PR mode reviews the head checkout. An explicit rules file (`--rules`, `review_rules_path`) is used as given. The rules file and the don't-flag list are outside the code-context character budget; with `context.enabled: false` (the no-context ablation) specialists get neither rules nor code context.

## D37: Scorecard examples are chosen by rule; the page is static and self-contained
`evals.scorecard.render_html` writes one HTML file with inline CSS and SVG only (no script, no external request) and re-derives every number from the raw records. The two example findings are not hand-picked: they are the planted issues the `pipeline` arm found (first run that matched), ranked by how rarely the `baseline` found them, then by how reliably the pipeline did, on different PRs where possible, shown next to the baseline findings on the same PR and run. The page says so. Arm names are shown as given; bars use a fixed colour per arm position. No pipeline arm, or no pipeline match, degrades to a plain note, not an error.

## D38: Semantic search is a best-effort extra at the lowest priority
Every chunk (`qualname` + source, cut at 8000 chars) is embedded and compared by numpy cosine with `title + description + diff`; chunks already chosen are excluded before the top-k, so `top_k` new blocks come back (labelled `semantic match, similarity 0.xx`, dropped first by the budget). Vectors are cached per (embedding model, file content hash) in `<cache.dir>/semantic/`, only when the provider is the disk-cached one, so `--no-cache` and the eval harness (always uncached) skip it too. A failing embeddings call (for example a gateway without `/embeddings`) is logged and recorded as `stats.context.semantic_error`; the review keeps the rest of the bundle. The one switch is `context.semantic.enabled: false`, which makes no embedding call at all. The `context` debug command passes no provider, so it never shows semantic matches. `embed` reports no token usage, so embedding cost (about $0.02 per million tokens) is not in the stats.

## D39: A finding anchored outside the diff is dropped and counted, for every specialist (D25 stands)
All three specialists (and the baseline, which shares the scaffold) get the same anchoring rule in the system prompt: the anchor is a new-side line inside the diff that causes the problem; a change that only removes a safeguard anchors on the nearest remaining line at that spot; breakage in an unchanged caller, in this file or another, goes in the explanation and evidence, never in the anchor and never as a second finding; each underlying problem is reported once. In code, `run_specialist` still drops any finding whose file and line are not a commentable line of the diff and counts it in `stats.specialists.<name>.dropped_invalid_anchor`, which the Markdown footer shows. We considered snapping such a finding to the nearest changed line and keeping it as an unanchored note. Both were rejected: snapping invents an anchor the model did not choose and can pin the finding on unrelated code, and an unanchored finding is no longer "introduced by this change" (D2), so it can neither be posted inline nor be scored by the same rules as the rest. The cost is that a cross-file catch anchored on the caller is lost; the counter shows how often that happens, and the prompt is where to fix it.

## D40: The standards specialist works only from the rules, and cites one that exists
`STANDARDS` flags violations of a specific numbered rule in `<team_rules>` and nothing else. It must set `rule_id` and quote the rule's text, then the offending code, in `evidence` (prompt rules). In code, `needs_rules` makes two guarantees: a finding whose `rule_id` is missing or not one of the loaded rules (matched case-insensitively, trailing colon ignored, stored in the rule's own spelling) is dropped and counted in `dropped_invalid_rule`, and when there are no rules at all (no `REVIEW.md`, or `context.enabled: false`) the specialist is skipped with `status: skipped` and makes no model call, since it has nothing to check. A skip is not a failure: `AllSpecialistsFailed` is raised only when every specialist that was asked has failed. The other specialists keep seeing all the rules (they are not told to ignore them), so one problem can be reported by two specialists; the verifier's `duplicate_of` and triage's near-duplicate collapse are what merge those, and the shared prompt only asks each specialist to report one finding per underlying problem.

## D41: The stats footer is one line per stage, in pipeline order, with a cached marker per stage
Footer order: totals (files, context, findings, tokens, cost, latency), then one line each for the specialists, the verifier and triage. Each model stage shows its time, tokens in/out and estimated cost, and `(cached)` when every one of its calls came from the cache (D18). The latency line says `(cached)` only if every call in the run was cached and `(partly cached: N of M calls)` when some were, since a mixed run's wall time means neither "fresh" nor "replayed". A failed specialist shows `FAILED (error)`, a skipped one `skipped (reason)`. Known gap: the tokens and cost of a specialist that failed (for example after its repair retry) are not counted in the totals.

## D42: Live-demo validation: pr09 caught and pr06 quiet on real runs; the verifier now sees the code context (general change)
Real API, cache off, `scripts/demo.sh <pr> --no-cache`, models as in `config.yaml` (terra specialists, sol verifier). Before any change (D30 verifier: finding text plus a ±15-line window only): pr06 0 findings in 3/3 runs; pr09 caught in 3/3 and then 8 runs in all gave 7 action-required and 1 `review_recommended` (medium). Two weaknesses showed. (1) The verifier's reason on the cross-file correctness finding was, in every run, "the invoice caller is not shown, so it cannot be verified"; it kept only the standards (R3) finding and marked the cross-file ones as duplicates of it, so the demo catch rested on that specialist alone. (2) In the one failed run the surviving R3 finding was rated medium, so the outcome was "recommended", not "action required".
Changes, both general (no PR-specific text; they apply to any PR), in `verifier.py` and its prompt: (a) the verifier gets the same `<context>` the specialists saw (changed code, callers, callees; untrusted data like the rest) and is told to confirm a claim about a caller from it, and to treat a claim as unconfirmable only when the code is in neither the window nor the context; with `context.enabled: false` it gets none, so the no-context ablation stays honest. (b) When it keeps one finding and marks others as its duplicates, it sets the kept finding's severity to the highest that confirmed evidence in any of them justifies, so the choice of which duplicate survives cannot lower the severity. After the change: pr09 8/8 action-required (the surviving finding is usually the cross-file one; the verifier now says the invoice caller "demonstrably consumes" the cents value), pr06 4/4 zero findings, verifier input grows by the context (pr09 about $0.043 per review, up from $0.033; clean PRs unchanged, since the verifier makes no call without findings). n=8 and n=4 is a small sample: the eval (3 runs x 12 PRs) is the real measure. The verifier's confirmation and the cross-file catch still depend on the call graph finding the caller (D35).
The demo replays from the cache (D18): `scripts/demo.sh pr09` / `pr06`. A cache key includes the prompts, so any later change to a prompt, the config's models or the context engine invalidates the demo entries; refill by running both commands once with a key, then check that the rerun says `(cached)`.

## D43: The `pipeline` arm is `reviewer.pipeline.review` itself; a degraded review is scored but flagged
`evals.arms.ARMS["pipeline"]` registers the CLI's own entry point with no wrapper, so the eval measures what ships. Its scored findings are the non-dropped ones (action required and review recommended, D34); false alarms on clean PRs are reported twice, for any scored finding and for action-required ones only. An arm that raises on a PR (for example all specialists failed) is `status: error`, excluded and counted as before. A review that comes back with a stage down (one specialist failed, or the verifier failed or went partial and failed open) is still scored, because a user would have seen it, but `summary[arm].degraded` counts it (`{specialists: {name: n}, verifier: n, reviews: n}`) and the scorecard and the command-line summary warn that the arm may be understating what it finds. A specialist `skipped` for lack of rules is not degraded.
