"""Git access: resolve a branch to a commit, and check a commit out into a build workspace.

Each run fetches the repository once into ``<run>/repo.git``; every build of the run then
clones from that mirror and checks out the same commit, so all builds of a chain see exactly
the same sources (TeamCity's snapshot-dependency guarantee).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class VcsError(RuntimeError):
    pass


def _git(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise VcsError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def resolve(url: str, branch: str) -> str:
    out = _git("ls-remote", url, f"refs/heads/{branch}")
    if not out:
        raise VcsError(f"branch {branch!r} not found in {url}")
    return out.split()[0]


def mirror(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--quiet", "--bare", url, str(dest))
    return dest


def checkout(mirror_dir: Path, commit: str, workspace: Path) -> None:
    workspace.parent.mkdir(parents=True, exist_ok=True)
    _git("clone", "--quiet", "--shared", "--no-checkout", str(mirror_dir), str(workspace))
    _git("-c", "advice.detachedHead=false", "checkout", "--quiet", commit, cwd=workspace)
