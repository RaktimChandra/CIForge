"""Executes a pipeline run: the requested builds and their chains, dependencies first.

Builds whose dependencies have all succeeded start as soon as an agent slot is free (up to
``max_parallel`` at once). A build whose dependency failed is ``canceled`` unless it declares
``run_if: always``. Inside a build, steps run in order; the first failing step ends the build
unless it is ``continue_on_error``.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ciforge import artifacts, vcs
from ciforge.config import DockerRunner, LocalRunner, Pipeline
from ciforge.params import ParamError, substitute
from ciforge.runners import StepResult, run_docker, run_local
from ciforge.store import Store, now

Listener = Callable[[str, str, str], None]  # (build_id, status, message)


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


@dataclass
class RunRequest:
    project: str
    pipeline: Pipeline
    targets: list[str] = field(default_factory=list)
    params: dict[str, str] = field(default_factory=dict)
    branch: str | None = None
    commit: str | None = None
    source: str | None = None
    trigger: str = "manual"
    run_id: str = field(default_factory=new_run_id)


class Engine:
    def __init__(self, data_dir: Path, store: Store | None = None) -> None:
        self.data_dir = data_dir
        self.store = store or Store(data_dir / "ciforge.db")
        self._cancel: dict[str, threading.Event] = {}

    # public ------------------------------------------------------------------------------
    def submit(self, req: RunRequest) -> str:
        """Records the run as queued and returns its id (run it with :meth:`execute`)."""
        chain = req.pipeline.chain(req.targets)
        self.store.create_run(
            req.run_id,
            req.project,
            req.commit,
            req.branch or self._branch(req),
            req.trigger,
            req.targets or list(req.pipeline.builds),
            req.params,
        )
        for i, b in enumerate(chain):
            self.store.add_build(req.run_id, b, req.pipeline.display_name(b), i, req.pipeline.builds[b].depends_on)
        self._cancel[req.run_id] = threading.Event()
        return req.run_id

    def cancel(self, run_id: str) -> bool:
        ev = self._cancel.get(run_id)
        if ev is None:
            return False
        ev.set()
        return True

    def execute(self, req: RunRequest, listener: Listener | None = None) -> str:
        """Runs a submitted request to completion; returns the run status."""
        if self.store.run(req.run_id) is None:
            self.submit(req)
        say = listener or (lambda b, s, m: None)
        cancel = self._cancel.setdefault(req.run_id, threading.Event())
        run_dir = self.data_dir / "runs" / req.run_id
        p = req.pipeline
        self.store.set_run(req.run_id, "running", started_at=now())
        try:
            branch, commit, mirror = self._prepare_sources(req, run_dir)
        except (vcs.VcsError, OSError) as e:
            for b in p.chain(req.targets):
                self.store.set_build(req.run_id, b, "canceled", reason=f"sources unavailable: {e}")
            self.store.set_run(req.run_id, "error", finished_at=now())
            say("-", "error", str(e))
            return "error"
        self.store.set_run(req.run_id, "running", commit_sha=commit, branch=branch)

        chain = p.chain(req.targets)
        status: dict[str, str] = {}
        running: dict[Future[str], str] = {}
        with ThreadPoolExecutor(max_workers=p.max_parallel, thread_name_prefix="agent") as pool:
            while len(status) < len(chain):
                for b in chain:
                    if b in status or b in running.values():
                        continue
                    deps = p.builds[b].depends_on
                    if any(d not in status for d in deps):
                        continue
                    failed = [d for d in deps if status[d] != "success"]
                    if cancel.is_set():
                        status[b] = "canceled"
                        self.store.set_build(req.run_id, b, "canceled", reason="run canceled")
                        continue
                    if failed and p.builds[b].run_if != "always":
                        status[b] = "canceled"
                        reason = "dependency did not succeed: " + ", ".join(f"{d} ({status[d]})" for d in failed)
                        self.store.set_build(req.run_id, b, "canceled", reason=reason)
                        say(b, "canceled", reason)
                        continue
                    if len(running) < p.max_parallel:
                        fut = pool.submit(self._build, req, b, run_dir, mirror, commit, branch, cancel, say)
                        running[fut] = b
                if running:
                    done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                    for fut in done:
                        b = running.pop(fut)
                        try:
                            status[b] = fut.result()
                        except Exception as e:
                            status[b] = "error"
                            self.store.set_build(
                                req.run_id, b, "error", reason=f"internal error: {e}", finished_at=now()
                            )
                elif len(status) < len(chain):
                    time.sleep(0.05)
        final = self._run_status(status, cancel.is_set())
        self.store.set_run(req.run_id, final, finished_at=now())
        self._cancel.pop(req.run_id, None)
        say("-", final, f"run {req.run_id} finished: {final}")
        return final

    # internals ---------------------------------------------------------------------------
    @staticmethod
    def _branch(req: RunRequest) -> str | None:
        return req.branch or (req.pipeline.vcs.branch if req.pipeline.vcs else None)

    def _prepare_sources(self, req: RunRequest, run_dir: Path) -> tuple[str | None, str | None, Path | None]:
        url = req.source or (req.pipeline.vcs.url if req.pipeline.vcs else None)
        if url is None:
            return None, None, None
        branch = self._branch(req) or "main"
        if req.source and not req.branch and not (req.pipeline.vcs and req.pipeline.vcs.url == req.source):
            # a local working copy: build its current branch
            with contextlib.suppress(vcs.VcsError):
                branch = vcs._git("rev-parse", "--abbrev-ref", "HEAD", cwd=Path(req.source))
        commit = req.commit or vcs.resolve(url, branch)
        mirror = vcs.mirror(url, run_dir / "repo.git")
        return branch, commit, mirror

    @staticmethod
    def _run_status(status: dict[str, str], canceled: bool) -> str:
        values = set(status.values())
        if canceled:
            return "canceled"
        if values <= {"success"}:
            return "success"
        if values & {"error"} and not values & {"failure", "timeout"}:
            return "error"
        return "failure"

    def _build(
        self,
        req: RunRequest,
        b: str,
        run_dir: Path,
        mirror: Path | None,
        commit: str | None,
        branch: str | None,
        cancel: threading.Event,
        say: Listener,
    ) -> str:
        p = req.pipeline
        cfg = p.builds[b]
        bdir = run_dir / "builds" / b
        work = bdir / "work"
        logs = bdir / "logs"
        store_dir = self.data_dir / "artifacts" / req.run_id / b
        logs.mkdir(parents=True, exist_ok=True)
        self.store.set_build(req.run_id, b, "running", started_at=now())
        say(b, "running", f"started {p.display_name(b)}")
        params = {
            **p.params,
            **cfg.params,
            **req.params,
            "ciforge.run.id": req.run_id,
            "ciforge.build.id": b,
            "ciforge.build.name": p.display_name(b),
            "ciforge.workspace": str(work.resolve()),
            "ciforge.artifacts": str(store_dir.resolve()),
            "vcs.commit": commit or "",
            "vcs.branch": branch or "",
        }
        secrets = [os.environ[s] for s in p.secrets if os.environ.get(s)]
        try:
            if mirror is not None and commit is not None:
                vcs.checkout(mirror, commit, work)
            else:
                work.mkdir(parents=True, exist_ok=True)
            for dep in cfg.artifacts_from:
                got = artifacts.fetch(self.data_dir / "artifacts" / req.run_id / dep.build, dep.paths, work / dep.into)
                say(b, "running", f"fetched {len(got)} artifact(s) from {dep.build}")
            env = {k: substitute(v, params) for k, v in cfg.env.items()}
            env |= {
                "CIFORGE_RUN_ID": req.run_id,
                "CIFORGE_BUILD_ID": b,
                "CIFORGE_COMMIT": commit or "",
                "CIFORGE_BRANCH": branch or "",
            }
        except (vcs.VcsError, ParamError, OSError) as e:
            self.store.set_build(req.run_id, b, "error", reason=str(e), finished_at=now())
            say(b, "error", str(e))
            return "error"

        runner: LocalRunner | DockerRunner = cfg.runner
        if isinstance(runner, DockerRunner):
            try:
                runner = runner.model_copy(
                    update={
                        "image": substitute(runner.image, params),
                        "options": [substitute(o, params) for o in runner.options],
                    }
                )
            except ParamError as e:
                self.store.set_build(req.run_id, b, "error", reason=str(e), finished_at=now())
                return "error"
        deadline = time.monotonic() + cfg.timeout_minutes * 60
        result = "success"
        reason: str | None = None
        for i, step in enumerate(cfg.steps):
            log_path = logs / f"{i:02d}-{_slug(step.name)}.log"
            self.store.add_step(req.run_id, b, i, step.name, str(log_path))
            try:
                script = substitute(step.run, params)
                step_env = env | {k: substitute(v, params) for k, v in step.env.items()}
            except ParamError as e:
                log_path.write_text(f"[ciforge] {e}\n", encoding="utf-8")
                self.store.finish_step(req.run_id, b, i, "error", None)
                result, reason = "error", str(e)
                break
            remaining = deadline - time.monotonic()
            step_timeout = min(
                x for x in [remaining, step.timeout_minutes * 60 if step.timeout_minutes else None] if x is not None
            )
            say(b, "running", f"step {i + 1}/{len(cfg.steps)}: {step.name}")
            res = self._step(runner, script, work, step.working_dir, step_env, log_path, step_timeout, secrets, cancel)
            step_status = (
                "canceled"
                if res.canceled
                else "timeout"
                if res.timed_out
                else "success"
                if res.exit_code == 0
                else "failure"
            )
            self.store.finish_step(req.run_id, b, i, step_status, res.exit_code)
            if not res.ok:
                if res.canceled:
                    result, reason = "canceled", "run canceled"
                    break
                if res.timed_out:
                    result, reason = "timeout", f"step '{step.name}' timed out"
                    break
                if not step.continue_on_error:
                    result, reason = "failure", f"step '{step.name}' exited with {res.exit_code}"
                    break
        if result == "success" and cfg.fail_on_text:
            for log in sorted(logs.glob("*.log")):
                text = log.read_text(encoding="utf-8", errors="replace")
                hit = next((t for t in cfg.fail_on_text if t in text), None)
                if hit:
                    result, reason = "failure", f"log {log.name} contains {hit!r}"
                    break
        if cfg.artifacts and result != "canceled":
            published = artifacts.publish(work, [substitute(r, params) for r in cfg.artifacts], store_dir)
            self.store.add_artifacts(req.run_id, b, published)
            say(b, "running", f"published {len(published)} artifact(s)")
        self.store.set_build(req.run_id, b, result, reason=reason, finished_at=now())
        say(b, result, reason or "ok")
        if os.environ.get("CIFORGE_KEEP_WORKSPACES") != "1" and result == "success":
            shutil.rmtree(work, ignore_errors=True)
        return result

    @staticmethod
    def _step(
        runner: LocalRunner | DockerRunner,
        script: str,
        work: Path,
        working_dir: str,
        env: dict[str, str],
        log_path: Path,
        timeout: float,
        secrets: list[str],
        cancel: threading.Event,
    ) -> StepResult:
        if isinstance(runner, DockerRunner):
            return run_docker(runner, script, work, working_dir, env, log_path, timeout, secrets, cancel)
        cwd = (work / working_dir).resolve()
        return run_local(runner, script, cwd, env, log_path, timeout, secrets, cancel)


def _slug(s: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:40] or "step"
