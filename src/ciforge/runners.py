"""Runners execute one step's script: as a local process or inside a Docker container.

Output is streamed line by line to the step log with secrets masked. A step that exceeds its
timeout is killed (its whole process group, or the container) and reported as ``timeout``.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import signal
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ciforge.config import DockerRunner, LocalRunner
from ciforge.params import mask


@dataclass(frozen=True)
class StepResult:
    exit_code: int | None
    timed_out: bool
    canceled: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.canceled


def _pump(stream: IO[str], log: IO[str], secrets: list[str]) -> None:
    for line in stream:
        log.write(mask(line, secrets))
        log.flush()


def _wait(
    proc: subprocess.Popen[str], timeout: float | None, cancel: threading.Event, kill: Callable[[], None]
) -> tuple[bool, bool]:
    """Waits for the process; returns (timed_out, canceled)."""
    waited = 0.0
    while True:
        try:
            proc.wait(timeout=0.25)
            return False, False
        except subprocess.TimeoutExpired:
            waited += 0.25
            if cancel.is_set():
                kill()
                proc.wait()
                return False, True
            if timeout is not None and waited >= timeout:
                kill()
                proc.wait()
                return True, False


def run_local(
    runner: LocalRunner,
    script: str,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    timeout: float | None,
    secrets: list[str],
    cancel: threading.Event,
) -> StepResult:
    with log_path.open("a", encoding="utf-8") as log:
        log.write(mask(f"$ {runner.shell} -c <<script\n{script}\nscript\n", secrets))
        log.flush()
        proc = subprocess.Popen(
            [runner.shell, "-c", script],
            cwd=cwd,
            env={**os.environ, **env},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        assert proc.stdout is not None
        pump = threading.Thread(target=_pump, args=(proc.stdout, log, secrets), daemon=True)
        pump.start()

        def kill() -> None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)

        timed_out, canceled = _wait(proc, timeout, cancel, kill)
        pump.join(timeout=5)
        if timed_out:
            log.write(f"\n[ciforge] step timed out after {timeout:.0f} s and was killed\n")
        if canceled:
            log.write("\n[ciforge] step canceled\n")
        return StepResult(proc.returncode, timed_out, canceled)


def docker_command(
    runner: DockerRunner, script: str, workspace: Path, working_dir: str, env: dict[str, str], name: str
) -> list[str]:
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "-v",
        f"{workspace.resolve()}:/workspace",
        "-w",
        f"/workspace/{working_dir}".rstrip("/.") or "/workspace",
    ]
    if runner.mount_docker_socket:
        cmd += ["-v", "/var/run/docker.sock:/var/run/docker.sock"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    cmd += list(runner.options)
    cmd += [runner.image, runner.shell, "-c", script]
    return cmd


def run_docker(
    runner: DockerRunner,
    script: str,
    workspace: Path,
    working_dir: str,
    env: dict[str, str],
    log_path: Path,
    timeout: float | None,
    secrets: list[str],
    cancel: threading.Event,
) -> StepResult:
    name = "ciforge-" + uuid.uuid4().hex[:12]
    cmd = docker_command(runner, script, workspace, working_dir, env, name)
    with log_path.open("a", encoding="utf-8") as log:
        shown = [c if not any(s and s in c for s in secrets) else "***" for c in cmd[:-1]]
        log.write(mask("$ " + " ".join(shlex.quote(c) for c in shown) + " <<script\n" + script + "\nscript\n", secrets))
        log.flush()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert proc.stdout is not None
        pump = threading.Thread(target=_pump, args=(proc.stdout, log, secrets), daemon=True)
        pump.start()

        def kill() -> None:
            subprocess.run(["docker", "kill", name], capture_output=True, check=False)

        timed_out, canceled = _wait(proc, timeout, cancel, kill)
        pump.join(timeout=5)
        if timed_out:
            log.write(f"\n[ciforge] step timed out after {timeout:.0f} s; container {name} was killed\n")
        if canceled:
            log.write(f"\n[ciforge] step canceled; container {name} was killed\n")
        return StepResult(proc.returncode, timed_out, canceled)
