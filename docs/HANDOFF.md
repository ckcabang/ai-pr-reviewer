# Handoff: rewire the provider, review real PRs, prep the demo

A different session continues from here. The POC is built and merged: `main` = `poc-v1` = the commit that added this file. Every ticket is closed (spec [#1](https://github.com/ckcabang/ai-pr-reviewer/issues/1), tickets #2–#18). CI runs ruff plus the offline suite (416 tests).

The next session has three goals:
1. **Rewire the API.** Point the reviewer at a different provider or gateway and different models.
2. **Target a real repo and real PRs.**
3. **Prep the demo** in that environment.

## Read first

- `README.md` is the source of truth for usage. The sections that matter here:
  - *Quickstart*
  - *Work-laptop setup*: provider config, CA bundle, semantic search and JSON mode off, the adapter recipe
  - *Adopt it in a GitHub repo*
  - *Results*
  - *Live demo script*
- `DECISIONS.md` (D1–D49) wins over `docs/plan.md`. Entries for this work:
  - D13: one committed `config.yaml`; environment settings live in that copy only
  - D15/D16: the Action reviews the PR head; inputs; never blocks
  - D21: models that reject `temperature`
  - D22: `httpx2`
  - D23: `Provider.chat_json` takes a `role`
  - D24: config and cache location
  - D36: rules are read from the base revision
  - D45: the action and demo seeding
  - D49: the review fixes, including the lazy provider and the deferred refactors
- `GLOSSARY.md` for vocabulary (finding, specialist, expected issue, arm…).
- Final eval: `evals/results/final/scorecard.html` and `results.json`. Rebuild offline with `uv run python -m evals.scorecard --from evals/results/final`.

## 1. Rewire the API

Follow README *Work-laptop setup*, steps 1–6. The levers are all in `config.yaml`:
- `llm.base_url`
- `llm.api_key_env`
- `llm.ca_bundle`
- `llm.json_mode`
- `models.*`
- `pricing_per_1m_tokens`
- `context.semantic.enabled`

Things that aren't obvious from the README:

- **Smoke-test each new model first.** Use `scripts/smoke_api.py`, which does one JSON round trip per chat model plus one embedding call. Record what each model accepts (`temperature`, JSON mode) as a new D-entry, the way D21 did for the current models. The provider already retries once without `temperature` and remembers that per model.
- **Keep the baseline on the specialist model** (`models.baseline` equal to `models.specialist`), or the comparison stops being fair (D6, D10).
- **Give the judge a different model from the verifier** (D8). The scorecard flags it if they match.
- **Write an adapter only if the gateway isn't OpenAI-compatible.** Implement the `Provider` protocol in `src/reviewer/llm.py` and return the adapter from `build_provider`. `src/reviewer/openai_provider.py` is the reference implementation, and its tests in `tests/` use `httpx2.MockTransport`.
- **Keep environment-specific settings in that copy.** Gateway URLs and internal model names are committed there and never pushed back to this repo (D13).
- **Expect a cold cache.** Changing models or prompts invalidates it, and `.cache/` is gitignored, so a fresh clone starts empty anyway.

## 2. Target a real repo and real PRs

Three ways in, cheapest first:

- **Local, base mode:**
  `uv run reviewer review --repo <repo> --base origin/main --title "..." --description-file <file>`
  Add a `REVIEW.md` at the reviewed repo's root to turn on the standards specialist. It uses the format of `demo_service/REVIEW.md`: numbered rules plus a don't-flag list. Rules are read from the base revision (D36). See the context a review would use with `uv run reviewer context --repo <repo> --base <ref>`, which needs no key and costs nothing.
- **Local, PR mode:**
  `uv run reviewer review --repo <repo> --pr <PR URL> [--post]`
  - Needs `GITHUB_TOKEN`.
  - On GitHub Enterprise the API host is derived from the PR URL, so there's nothing to configure.
  - Without `--post` nothing is written to GitHub. Try it read-only first.
  - `--post` submits one COMMENT review with inline comments on commentable lines.
- **CI:** follow README *Adopt it in a GitHub repo*. Answer the open questions in README *Work-laptop setup* step 8 before adopting:
  - Is code allowed to go to the model API from CI?
  - Are Actions enabled?
  - Can the runners reach the gateway?

  Known gaps:
  - Fork PRs get a read-only token, so posting fails.
  - Each run posts a new review; old ones aren't replaced.
  - Runs are uncached.
  - On a new repo, Actions may have to be enabled before any run starts.
  - The secret name in the repo must match the workflow's `secrets.<NAME>` exactly.
  - `action.yml` always exports the key as `OPENAI_API_KEY`. A config used by the action must keep `llm.api_key_env: OPENAI_API_KEY`, or `action.yml` needs extending.

**Scoring real PRs is not built yet.** The eval harness only scores dataset PRs: `evals/dataset/` patches against `demo_service/`, materialized by `evals/dataset/materialize.py`. To score real PRs you'd need:
- a loader that takes base and head SHAs from a real repo
- hand-written expected issues in the `labels.yaml` format (D2, D4, D5)

The arms (`evals/arms.py`), judge, metrics and scorecard can be reused as they are. Scope this with `/to-spec` before building it.

## 3. Prep the demo

The demo script is in README *Live demo script*, timed at 5–7 minutes. Steps in the new environment:

1. Fill the cache once with a key: `uv run --env-file .env scripts/demo.sh pr09`, then `pr06`. Expected results:
   - pr09: one action-required finding at `app/services/pricing.py:54` naming the invoice caller
   - pr06: "No findings"
2. Run `scripts/demo.sh --verify`. It must print `ok` for both PRs. It replays with no key, and any cache miss shows as `STALE`. Rerun it after any change to the config, prompts or `REVIEW.md`.
3. Decide which numbers to show:
   - The committed results were measured on the current OpenAI models (4 arms × 16 PRs × 3 runs, $4.87).
   - If you rewire the models, either present those results as the reference measurement, or rerun `python -m evals.run_eval --arms baseline,pipeline,pipeline_no_verifier,pipeline_no_context --runs 3` and commit the new results under `evals/results/`.
   - The README states the results as measured: wins on recall and cross-file, loses on precision and cost. Keep that honesty.
4. Live PRs: [pr09](https://github.com/ckcabang/ai-review-demo/pull/1) and [pr06](https://github.com/ckcabang/ai-review-demo/pull/2) in the private demo repo. If you demo on a real repo instead, open a PR there and let the action, or `--pr --post`, review it.

## Environment gotchas from the previous session

- **Commit trailer:** end commits with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage files by name.
- **Git in the sandbox:** run `git fetch/pull/push` as standalone commands from the repo directory (no `git -C`, no chaining), and push with `git push origin <branch>`. Subagents in worktrees can't push, so the main session pushes.
- **Sandbox artefacts:** the Claude Code sandbox puts character-device files (`.bashrc`, `.mcp.json`, …) in the repo root. List them in `.git/info/exclude`, which is local, so `git status` stays clean.
- **API key:** load it with `uv run --env-file .env ...`. Never print it.
- **Tests:** pytest runs in importlib mode. Share helpers through fixtures, not `import conftest`. The dataset integrity tests take about 60 s; while iterating, use `--deselect tests/test_dataset.py`.
- **Prompt changes:** `tests/test_prompt_pins.py` pins the exact prompt text. Any intentional prompt change updates the pins and gets a D-entry saying why it's general rather than tuned to the dataset (D42, D47).

## Suggested skills

- `mattpocock-skills:tdd`: an adapter, a real-PR loader, any behaviour change.
- `mattpocock-skills:diagnosing-bugs`: provider, TLS or gateway failures that resist a first look.
- `mattpocock-skills:wizard`: human-only steps such as gateway credentials, the CI secret and enabling Actions.
- `mattpocock-skills:grill-with-docs` then `mattpocock-skills:to-spec`: if scoring real PRs is in scope.
- `mattpocock-skills:domain-modeling`: record new choices as D-entries in `DECISIONS.md`.
- `mattpocock-skills:code-review`: before demoing changes made in the new environment.
