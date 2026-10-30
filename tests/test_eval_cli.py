"""The eval command line: `python -m evals.run_eval` and `python -m evals.scorecard`."""
import json

from evals import scorecard
from evals.run_eval import main
from reviewer.fake import FakeProvider


def quiet_provider():
    return FakeProvider({"baseline": {"findings": []}, "judge": {"matches": []}})


def test_run_eval_takes_arms_runs_and_an_output_folder(tmp_path, capsys):
    out = tmp_path / "res"
    code = main(["--arms", "baseline", "--runs", "2", "--out", str(out), "--prs", "pr06",
                 "--skip-judge-check"], provider=quiet_provider())

    assert code == 0
    saved = json.loads((out / "results.json").read_text())
    assert saved["config"]["runs"] == 2 and saved["config"]["arms"] == ["baseline"]
    assert saved["config"]["pr_ids"] == ["pr06"] and len(saved["records"]) == 2
    assert saved["judge_agreement"] is None
    shown = capsys.readouterr().out
    assert str(out / "results.json") in shown and "baseline: precision" in shown


def test_run_eval_runs_the_judge_check_unless_told_not_to(tmp_path):
    out = tmp_path / "res"
    main(["--runs", "1", "--out", str(out), "--prs", "pr06"], provider=quiet_provider())
    assert json.loads((out / "results.json").read_text())["judge_agreement"]["n"] == 6


def test_unknown_arm_and_unknown_pr_are_reported_with_exit_code_2(tmp_path, capsys):
    assert main(["--arms", "nope", "--out", str(tmp_path / "a")], provider=quiet_provider()) == 2
    assert "unknown arm" in capsys.readouterr().err
    assert main(["--prs", "pr99", "--out", str(tmp_path / "b")], provider=quiet_provider()) == 2
    assert "pr99" in capsys.readouterr().err


def test_scorecard_command_rebuilds_the_html(tmp_path, capsys):
    out = tmp_path / "res"
    main(["--runs", "1", "--out", str(out), "--prs", "pr06", "--skip-judge-check"], provider=quiet_provider())
    (out / "scorecard.html").unlink()

    assert scorecard.main(["--from", str(out)]) == 0
    assert (out / "scorecard.html").is_file()
    assert scorecard.main(["--from", str(tmp_path / "missing")]) == 2
    assert "error" in capsys.readouterr().err
