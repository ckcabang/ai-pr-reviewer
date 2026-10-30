#!/usr/bin/env bash
# Live demo: materialize a dataset PR into a fresh git repo and review it.
#
#   scripts/demo.sh pr09              # the cross-file catch (replays from the cache if filled)
#   scripts/demo.sh pr06              # the clean PR (should be quiet)
#   scripts/demo.sh pr09 --no-cache   # fresh, real API calls (anything after the PR id goes to `review`)
#
# Needs OPENAI_API_KEY (or `uv run --env-file .env`) only for calls that are not in the cache: a
# fully cached replay runs with no key at all. The review uses the project's config.yaml, so the
# cache is <project>/.cache (D24).
set -euo pipefail
pr="${1:?usage: scripts/demo.sh pr09|pr06 [review args...]}"; shift || true
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
cd "$root"
uv run python -m evals.dataset materialize "$pr" "$work/repo" >&2
uv run python - "$pr" "$work" <<'PY'
import sys
from pathlib import Path
from evals.dataset import load_pr
pr = load_pr(sys.argv[1])
Path(sys.argv[2], "title.txt").write_text(pr.title)
Path(sys.argv[2], "description.md").write_text(pr.description)
PY
uv run reviewer review --repo "$work/repo" --base main \
  --title "$(cat "$work/title.txt")" --description-file "$work/description.md" "$@"
