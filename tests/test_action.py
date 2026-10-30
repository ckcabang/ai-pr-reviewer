"""Structure checks for the composite action and the example adopter workflow (D11, D15, D16).

Nothing here runs GitHub; it pins the properties that matter for safety and for the PR-mode CLI
contract (head-SHA checkout, no pull_request_target, no PR text in shell).
"""
import os
import re
import subprocess
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


# --- the review step's exit handling (story 53: the check passes by default) ---------------------

def _run_review_step(tmp_path, *, fail_on, exit_code, stderr="error: boom"):
    """Run the action's real review script under bash with a stub `uv` that fails like the CLI."""
    step = [s for s in _run_steps(_load(ACTION)["runs"]["steps"]) if "reviewer" in s["run"]][-1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(f'#!/usr/bin/env bash\nprintf %s "$FAKE_STDERR" >&2\nexit {exit_code}\n')
    uv.chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
           "ACTION_PATH": str(ROOT), "PR_URL": "https://github.com/o/r/pull/1", "GITHUB_TOKEN": "t",
           "OPENAI_API_KEY": "k", "REVIEW_CONFIG": "", "REVIEW_FAIL_ON": fail_on, "FAKE_STDERR": stderr}
    return subprocess.run(["bash", "-c", step["run"]], capture_output=True, text=True, env=env, cwd=tmp_path)


def test_a_reviewer_error_only_warns_when_fail_on_is_never(tmp_path):
    out = _run_review_step(tmp_path, fail_on="never", exit_code=2, stderr="error: invalid API key")
    assert out.returncode == 0
    assert "::warning" in out.stdout and "invalid API key" in out.stdout
    assert "error: invalid API key" in out.stderr  # still in the log


def test_the_warning_cannot_be_used_to_inject_workflow_commands(tmp_path):
    evil = "error: oops\n::error::pwned\r\n::set-output name=x::y"
    out = _run_review_step(tmp_path, fail_on="never", exit_code=2, stderr=evil)
    assert out.returncode == 0
    command_lines = [line for line in out.stdout.splitlines() if line.startswith("::")]
    assert len(command_lines) == 1 and command_lines[0].startswith("::warning")


def test_a_reviewer_error_still_fails_the_step_with_fail_on_action_required(tmp_path):
    out = _run_review_step(tmp_path, fail_on="action_required", exit_code=2)
    assert out.returncode == 2 and "::warning" not in out.stdout


def test_action_required_findings_fail_the_step_only_when_asked_to(tmp_path):
    assert _run_review_step(tmp_path, fail_on="action_required", exit_code=1).returncode == 1
    assert _run_review_step(tmp_path, fail_on="never", exit_code=0).returncode == 0
