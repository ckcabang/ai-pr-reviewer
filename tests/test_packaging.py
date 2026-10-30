"""The package installs as `reviewer` and is runnable both ways (D1)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


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


def test_demo_service_dependencies_are_dev_only():
    """`uv sync --no-dev` (the Action) must install only what the reviewer itself needs."""
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+
    doc = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    name = lambda spec: spec.split(">")[0].split("=")[0]  # noqa: E731
    runtime = {name(d) for d in doc["project"]["dependencies"]}
    dev = {name(d) for d in doc["dependency-groups"]["dev"]}
    assert not runtime & {"fastapi", "uvicorn", "httpx"}
    assert {"fastapi", "uvicorn", "httpx"} <= dev
