"""The package installs as `reviewer` and is runnable both ways (D1)."""
import shutil
import subprocess
import sys


def test_python_dash_m_help_lists_review_command():
    out = subprocess.run(
        [sys.executable, "-m", "reviewer", "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0
    assert "review" in out.stdout


def test_console_script_help_works():
    exe = shutil.which("reviewer", path=str(__import__("pathlib").Path(sys.executable).parent))
    assert exe, "console script `reviewer` is not installed"
    out = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    assert "review" in out.stdout
