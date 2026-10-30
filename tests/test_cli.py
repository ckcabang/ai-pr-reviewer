"""CLI: `reviewer review --repo PATH --base REF ...` end to end, with a fake provider injected."""
import json
import os
import subprocess
import sys

import pytest
import yaml

from reviewer.cli import exit_code_for, main
from reviewer.config import default_config_path
from reviewer.fake import FakeProvider
from reviewer.models import ReviewResult, VerifiedFinding, finding_id

FINDING = {
    "file": "a.py", "line": 2, "category": "logic", "severity": "high", "confidence": 0.9,
    "title": "Overwrites x", "explanation": "x is clobbered.", "evidence": "x = 2",
}


@pytest.fixture
def repo(tmp_path, make_repo):
    return make_repo(tmp_path / "repo", {"a.py": "x = 1\ny = 1\n"}, {"a.py": "x = 1\nx = 2\n"})


@pytest.fixture
def config_path(tmp_path):
    """The committed config, but with the cache in a temp dir."""
    raw = yaml.safe_load(default_config_path().read_text())
    raw["cache"] = {"enabled": True, "dir": str(tmp_path / "cache")}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    return str(path)


def run(args, repo, config_path, provider):
    return main(["review", "--repo", str(repo), "--base", "main", "--config", config_path, *args],
                provider=provider)


def fake(verdict="keep", severity="high"):
    fid = finding_id("correctness", FINDING["file"], FINDING["line"], FINDING["title"])
    verifier = {"verdicts": [{"id": fid, "verdict": verdict, "confidence": 0.9, "severity": severity}]}
    return FakeProvider({"correctness": {"findings": [FINDING]}, "security": {"findings": []}, "verifier": verifier})


def test_markdown_goes_to_stdout_by_default_and_exit_code_is_0(repo, config_path, capsys):
    assert run([], repo, config_path, fake()) == 0
    out = capsys.readouterr().out
    assert out.startswith("# AI Review: 1 action required · 0 recommended")
    assert "a.py:2" in out


def test_json_format_prints_only_json(repo, config_path, capsys):
    assert run(["--format", "json"], repo, config_path, fake()) == 0
    assert json.loads(capsys.readouterr().out)["findings"][0]["title"] == "Overwrites x"


def test_both_formats_write_md_and_json_files_next_to_out(repo, config_path, tmp_path):
    out = tmp_path / "reports" / "review"
    assert run(["--format", "both", "--out", str(out)], repo, config_path, fake()) == 0
    assert (tmp_path / "reports" / "review.md").read_text().startswith("# AI Review")
    assert json.loads((tmp_path / "reports" / "review.json").read_text())["findings"]


def test_single_format_writes_to_the_out_path(repo, config_path, tmp_path, capsys):
    out = tmp_path / "r.md"
    assert run(["--out", str(out)], repo, config_path, fake()) == 0
    assert out.read_text().startswith("# AI Review")
    assert capsys.readouterr().out == ""


def test_both_without_out_is_a_usage_error(repo, config_path, capsys):
    with pytest.raises(SystemExit) as e:
        run(["--format", "both"], repo, config_path, fake())
    assert e.value.code == 2


def test_title_and_description_file_reach_the_specialist(repo, config_path, tmp_path):
    desc = tmp_path / "desc.txt"
    desc.write_text("Fixes the overwrite bug.")
    provider = fake()
    run(["--title", "Fix x", "--description-file", str(desc)], repo, config_path, provider)
    [call] = provider.calls_for("correctness")
    assert "Fix x" in call.user and "Fixes the overwrite bug." in call.user


def test_reviews_a_repo_other_than_the_working_directory(repo, config_path, tmp_path, monkeypatch, capsys):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert run([], repo, config_path, fake()) == 0
    assert "a.py:2" in capsys.readouterr().out


def test_no_changes_is_an_empty_review_without_calling_the_model(repo, config_path, capsys):
    provider = fake()
    code = main(["review", "--repo", str(repo), "--base", "feature", "--config", config_path], provider=provider)
    assert code == 0 and provider.calls == []
    assert "No findings" in capsys.readouterr().out


def test_cache_is_on_by_default_and_off_with_no_cache(repo, config_path):
    provider = fake()
    run([], repo, config_path, provider)
    run([], repo, config_path, provider)
    assert len(provider.calls) == 3  # correctness + security + verifier; the second run replayed from the cache

    run(["--no-cache"], repo, config_path, provider)
    assert len(provider.calls) == 6

    run(["--no-cache"], repo, config_path, provider)
    assert len(provider.calls) == 9  # --no-cache does not write the cache either


def test_cached_run_is_labelled_in_the_output(repo, config_path, capsys):
    provider = fake()
    run([], repo, config_path, provider)
    capsys.readouterr()
    run([], repo, config_path, provider)
    assert "(cached)" in capsys.readouterr().out


def test_verbose_logs_progress_to_stderr_only(repo, config_path, capsys):
    run(["--verbose"], repo, config_path, fake())
    captured = capsys.readouterr()
    assert "reviewing 1 file(s)" in captured.err
    assert "reviewing 1 file(s)" not in captured.out


@pytest.mark.parametrize("make_args", [
    lambda repo, cfg: ["review", "--repo", str(repo / "nope"), "--base", "main", "--config", cfg],
    lambda repo, cfg: ["review", "--repo", str(repo), "--base", "no-such-ref", "--config", cfg],
    lambda repo, cfg: ["review", "--repo", str(repo), "--base", "main", "--config", cfg + ".missing"],
    lambda repo, cfg: ["review", "--repo", str(repo), "--base", "main", "--config", cfg, "--description-file", "/no/such"],
])
def test_errors_exit_2_with_a_message_on_stderr(repo, config_path, capsys, make_args):
    assert main(make_args(repo, config_path), provider=fake()) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("error:") and captured.out == ""


def test_every_specialist_failing_exits_2(repo, config_path, capsys):
    provider = FakeProvider({"correctness": RuntimeError("boom")})
    assert run([], repo, config_path, provider) == 2
    assert "boom" in capsys.readouterr().err


def test_fail_on_action_required_exits_1_only_when_such_findings_exist():
    def result(tier):
        finding = VerifiedFinding(id="x", reviewer="correctness", triage=tier, **{k: FINDING[k] for k in FINDING})
        return ReviewResult(findings=[finding], stats={})

    assert exit_code_for(result("action_required"), "action_required") == 1
    assert exit_code_for(result("action_required"), "never") == 0
    assert exit_code_for(result("review_recommended"), "action_required") == 0


def test_fail_on_action_required_exits_1_only_when_an_action_required_finding_exists(repo, config_path):
    assert run(["--fail-on", "action_required"], repo, config_path, fake()) == 1
    assert run(["--fail-on", "never"], repo, config_path, fake()) == 0
    # the verifier downgrades it to medium severity: recommended, not required
    assert run(["--fail-on", "action_required", "--no-cache"], repo, config_path, fake(severity="medium")) == 0
    assert run(["--fail-on", "action_required", "--no-cache"], repo, config_path, fake(verdict="drop")) == 0


def test_real_process_without_api_key_exits_2_with_a_clear_message(repo, config_path):
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    out = subprocess.run(
        [sys.executable, "-m", "reviewer", "review", "--repo", str(repo), "--base", "main", "--config", config_path],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert out.returncode == 2
    assert "OPENAI_API_KEY" in out.stderr
