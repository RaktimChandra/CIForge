"""The CIForge server: REST API, GitHub webhook endpoint, schedule loop and a small web UI.

Configuration (environment):
  CIFORGE_DATA_DIR        where runs, logs, artifacts and the SQLite database live (default ./ciforge-data)
  CIFORGE_PROJECTS        name=path/to/pipeline.yml[,name2=...]
  CIFORGE_WEBHOOK_SECRET  shared secret for GitHub webhooks (webhooks are refused without it)
  CIFORGE_MAX_RUNS        runs executed at the same time (default 1); builds inside a run use max_parallel
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ciforge import config, triggers
from ciforge.artifacts import safe_path
from ciforge.engine import Engine, RunRequest

WEB = Path(__file__).parent / "web"


class TriggerBody(BaseModel):
    builds: list[str] = Field(default_factory=list)
    branch: str | None = None
    commit: str | None = None
    params: dict[str, str] = Field(default_factory=dict)


@dataclass
class Settings:
    data_dir: Path
    projects: dict[str, Path]
    webhook_secret: str | None
    max_runs: int = 1
    schedule_interval: float = 20.0

    @staticmethod
    def from_env() -> Settings:
        projects = {}
        for item in filter(None, (os.environ.get("CIFORGE_PROJECTS") or "").split(",")):
            name, _, path = item.partition("=")
            projects[name.strip()] = Path(path.strip())
        return Settings(
            Path(os.environ.get("CIFORGE_DATA_DIR", "ciforge-data")),
            projects,
            os.environ.get("CIFORGE_WEBHOOK_SECRET") or None,
            int(os.environ.get("CIFORGE_MAX_RUNS", "1")),
        )


@dataclass
class Server:
    settings: Settings
    engine: Engine = field(init=False)
    pool: ThreadPoolExecutor = field(init=False)
    stop: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = Engine(self.settings.data_dir)
        self.pool = ThreadPoolExecutor(max_workers=self.settings.max_runs, thread_name_prefix="run")

    def pipeline(self, project: str) -> config.Pipeline:
        path = self.settings.projects.get(project)
        if path is None:
            raise HTTPException(404, f"unknown project {project!r}")
        try:
            return config.load(path)
        except config.ConfigError as e:
            raise HTTPException(422, str(e)) from e

    def start_run(
        self,
        project: str,
        trigger: str,
        builds: list[str],
        branch: str | None,
        commit: str | None,
        params: dict[str, str],
    ) -> str:
        p = self.pipeline(project)
        try:
            p.chain(builds)
        except config.ConfigError as e:
            raise HTTPException(422, str(e)) from e
        req = RunRequest(
            project=project, pipeline=p, targets=builds, params=params, branch=branch, commit=commit, trigger=trigger
        )
        run_id = self.engine.submit(req)
        self.pool.submit(self.engine.execute, req)
        return run_id

    def schedule_loop(self) -> None:
        last = datetime.now(UTC)
        while not self.stop.wait(self.settings.schedule_interval):
            current = datetime.now(UTC)
            for name in self.settings.projects:
                try:
                    p = self.pipeline(name)
                except HTTPException:
                    continue
                for t in triggers.due_schedules(p, last, current):
                    self.start_run(name, f"schedule {t.cron}", t.builds, t.branch, None, {})
            last = current


def create_app(settings: Settings | None = None) -> FastAPI:
    server = Server(settings or Settings.from_env())

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        t = threading.Thread(target=server.schedule_loop, name="scheduler", daemon=True)
        t.start()
        yield
        server.stop.set()
        server.pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="CIForge", version="1.0.0", lifespan=lifespan)
    app.state.server = server
    templates = Jinja2Templates(directory=str(WEB / "templates"))
    app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")

    def run_or_404(run_id: str) -> dict[str, Any]:
        r = server.engine.store.run(run_id)
        if r is None:
            raise HTTPException(404, f"unknown run {run_id!r}")
        return r

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/projects")
    def projects() -> list[dict[str, Any]]:
        out = []
        for name in server.settings.projects:
            try:
                p = server.pipeline(name)
                out.append(
                    {
                        "name": name,
                        "description": p.description,
                        "builds": list(p.builds),
                        "chain": p.chain(),
                        "triggers": [t.model_dump() for t in p.triggers],
                    }
                )
            except HTTPException as e:
                out.append({"name": name, "error": e.detail})
        return out

    @app.post("/api/projects/{project}/runs", status_code=202)
    def trigger(project: str, body: TriggerBody) -> dict[str, str]:
        return {"run_id": server.start_run(project, "manual", body.builds, body.branch, body.commit, body.params)}

    @app.get("/api/runs")
    def runs(project: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return server.engine.store.runs(project, min(limit, 500))

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> dict[str, Any]:
        return run_or_404(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, bool]:
        run_or_404(run_id)
        return {"canceled": server.engine.cancel(run_id)}

    @app.get("/api/runs/{run_id}/builds/{build_id}/steps/{idx}/log", response_class=PlainTextResponse)
    def log(run_id: str, build_id: str, idx: int) -> str:
        s = server.engine.store.step(run_id, build_id, idx)
        if s is None or not s["log_path"]:
            raise HTTPException(404, "no such step")
        p = Path(s["log_path"])
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

    @app.get("/api/runs/{run_id}/builds/{build_id}/artifacts/{path:path}")
    def artifact(run_id: str, build_id: str, path: str) -> FileResponse:
        run_or_404(run_id)
        root = server.settings.data_dir / "artifacts" / run_id / build_id
        try:
            f = safe_path(root, path)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if not f.is_file():
            raise HTTPException(404, "no such artifact")
        return FileResponse(f)

    @app.post("/api/webhooks/github/{project}")
    async def github(project: str, request: Request) -> JSONResponse:
        body = await request.body()
        secret = server.settings.webhook_secret
        if not secret:
            raise HTTPException(503, "webhooks are disabled: set CIFORGE_WEBHOOK_SECRET")
        if not triggers.verify_signature(secret, body, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(401, "invalid signature")
        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return JSONResponse({"pong": True})
        if event != "push":
            return JSONResponse({"ignored": f"event {event!r}"})
        push = triggers.Push.parse(await request.json())
        if push is None:
            return JSONResponse({"ignored": "not a branch push"})
        p = server.pipeline(project)
        targets = triggers.vcs_targets(p, push.branch)
        if targets is None:
            return JSONResponse({"ignored": f"no VCS trigger matches branch {push.branch!r}"})
        started = [
            server.start_run(project, f"push {push.branch}@{push.commit[:7]}", t, push.branch, push.commit, {})
            for t in targets
        ]
        return JSONResponse({"runs": started}, status_code=202)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "index.html", {"projects": projects(), "runs": server.engine.store.runs(limit=30)}
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str) -> HTMLResponse:
        r = run_or_404(run_id)
        return templates.TemplateResponse(request, "run.html", {"run": r, "duration": _duration})

    return app


def _duration(start: str | None, end: str | None) -> str:
    if not start:
        return ""
    a = datetime.fromisoformat(start)
    b = datetime.fromisoformat(end) if end else datetime.now(UTC)
    s = int((b - a).total_seconds())
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def serve(host: str, port: int, settings: Settings) -> None:
    import uvicorn

    uvicorn.run(create_app(settings), host=host, port=port, log_level="info")


__all__ = ["Settings", "create_app", "serve", "time"]
