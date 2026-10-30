"""Structure checks for the composite action and the example adopter workflow (D11, D15, D16).

Nothing here runs GitHub; it pins the properties that matter for safety and for the PR-mode CLI
contract (head-SHA checkout, no pull_request_target, no PR text in shell).
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "action.yml"
WORKFLOW = ROOT / "examples" / "ai-review.yml"


def _load(path):
    return yaml.safe_load(path.read_text())


def _run_steps(steps):
    return [s for s in steps if "run" in s]


def test_action_is_composite_with_the_d16_inputs():
    doc = _load(ACTION)
    assert doc["runs"]["using"] == "composite"
    ins = doc["inputs"]
    assert ins["openai-api-key"]["required"] is True
    assert ins["github-token"].get("required", False) is False
    assert ins["github-token"]["default"] == "${{ github.token }}"
    assert not ins["config"].get("required", False)
    assert ins["fail-on"]["default"] == "never"


def test_action_installs_uv_and_syncs_from_the_lockfile():
    doc = _load(ACTION)
    text = ACTION.read_text()
    assert any(str(s.get("uses", "")).startswith("astral-sh/setup-uv@") for s in doc["runs"]["steps"])
    assert "--frozen" in text and "$ACTION_PATH" in text


def test_action_runs_pr_mode_with_posting_against_the_workspace():
    steps = _load(ACTION)["runs"]["steps"]
    review = [s for s in _run_steps(steps) if "reviewer" in s["run"]][-1]
    assert "--repo ." in review["run"] and "--pr " in review["run"] and "--post" in review["run"]
    assert "--fail-on" in review["run"] and "--config" in review["run"]
    env = review["env"]
    assert env["GITHUB_TOKEN"] == "${{ inputs.github-token }}"
    assert env["OPENAI_API_KEY"] == "${{ inputs.openai-api-key }}"
    assert env["PR_URL"] == "${{ github.event.pull_request.html_url }}"


def test_action_never_interpolates_expressions_into_shell():
    for step in _run_steps(_load(ACTION)["runs"]["steps"]):
        assert "${{" not in step["run"], step["run"]
        assert step.get("shell") == "bash"


def test_workflow_triggers_on_pull_request_and_skips_drafts():
    doc = _load(WORKFLOW)
    triggers = doc.get("on", doc.get(True))
    assert set(triggers) == {"pull_request"}
    assert set(triggers["pull_request"]["types"]) == {"opened", "synchronize", "ready_for_review"}
    assert "github.event.pull_request.draft == false" in doc["jobs"]["review"]["if"]
    # the word may appear in the explanatory comment, never in the parsed workflow
    assert "pull_request_target" not in yaml.dump(doc)


def test_workflow_permissions_are_minimal():
    assert _load(WORKFLOW)["permissions"] == {"contents": "read", "pull-requests": "write"}


def test_workflow_checks_out_the_head_sha_with_full_history():
    steps = _load(WORKFLOW)["jobs"]["review"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert checkout["with"]["fetch-depth"] == 0
    assert any(str(s.get("uses", "")).startswith("ckcabang/ai-pr-reviewer@") for s in steps)


def test_workflow_has_no_shell_steps_and_pins_actions_to_major_versions():
    steps = _load(WORKFLOW)["jobs"]["review"]["steps"]
    assert not _run_steps(steps), "PR text must never reach shell code"
    for s in steps:
        uses = s["uses"]
        if uses.startswith("ckcabang/ai-pr-reviewer@"):
            continue  # the reviewer itself is referenced by branch until it is tagged
        assert re.fullmatch(r"[\w./-]+@v\d+", uses), uses
    assert "SHA" in WORKFLOW.read_text()  # the pinning note
