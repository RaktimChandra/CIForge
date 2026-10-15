from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path):
    """A throwaway git repository; returns a function to write a file and commit it."""
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "t", cwd=root)

    def commit(files: dict[str, str], message: str = "c") -> str:
        for name, content in files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        git("add", "-A", cwd=root)
        git("commit", "-q", "-m", message, cwd=root)
        return git("rev-parse", "HEAD", cwd=root)

    commit.root = root  # type: ignore[attr-defined]
    return commit


@pytest.fixture
def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
