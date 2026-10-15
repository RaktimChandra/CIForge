from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from ciforge.server import Settings, create_app

EXAMPLE = Path(__file__).parent.parent / "examples" / "hello" / ".ciforge.yml"


def wait_done(c: TestClient, run_id: str, seconds: float = 30) -> dict:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        r = c.get(f"/api/runs/{run_id}").json()
        if r["status"] not in ("queued", "running"):
            return r
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def pipeline_for(repo_root: Path, tmp: Path) -> Path:
    text = EXAMPLE.read_text().replace("builds:\n", f"vcs:\n  url: {repo_root}\n  branch: main\nbuilds:\n", 1)
    p = tmp / "pipeline.yml"
    p.write_text(text)
    return p


def app(tmp_path: Path, repo_root: Path, secret: str | None = "s3cret") -> TestClient:
    s = Settings(tmp_path / "data", {"hello": pipeline_for(repo_root, tmp_path)}, secret, schedule_interval=3600)
    return TestClient(create_app(s))


def test_trigger_run_follow_and_download(tmp_path, repo):
    repo({"a": "1"})
    with app(tmp_path, repo.root) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        projects = c.get("/api/projects").json()
        assert projects[0]["chain"] == ["compile", "test", "lint", "package"]
        rid = c.post("/api/projects/hello/runs", json={"builds": ["test"]}).json()["run_id"]
        r = wait_done(c, rid)
        assert r["status"] == "success"
        assert [b["build_id"] for b in r["builds"]] == ["compile", "test"]
        log = c.get(f"/api/runs/{rid}/builds/test/steps/0/log").text
        assert "tests passed" in log
        art = c.get(f"/api/runs/{rid}/builds/test/artifacts/reports/report.txt")
        assert art.status_code == 200 and "tests passed" in art.text
        assert c.get(f"/api/runs/{rid}/builds/test/artifacts/..%2F..%2Fciforge.db").status_code in (400, 404)
        page = c.get(f"/runs/{rid}").text
        assert "Compile" in page and "success" in page
        assert rid in c.get("/").text


def test_unknown_things_are_404_or_422(tmp_path, repo):
    repo({"a": "1"})
    with app(tmp_path, repo.root) as c:
        assert c.get("/api/runs/nope").status_code == 404
        assert c.post("/api/projects/ghost/runs", json={}).status_code == 404
        assert c.post("/api/projects/hello/runs", json={"builds": ["deploy"]}).status_code == 422


def signed(body: dict, secret: str) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "X-GitHub-Event": "push", "Content-Type": "application/json"}


def test_github_push_webhook_starts_a_run_on_the_pushed_commit(tmp_path, repo):
    sha = repo({"a": "1"})
    with app(tmp_path, repo.root) as c:
        raw, headers = signed({"ref": "refs/heads/main", "after": sha, "repository": {}}, "s3cret")
        resp = c.post("/api/webhooks/github/hello", content=raw, headers=headers)
        assert resp.status_code == 202
        rid = resp.json()["runs"][0]
        r = wait_done(c, rid)
        assert r["commit_sha"] == sha and r["trigger"].startswith("push main@")
        assert r["status"] == "success"

        raw, headers = signed({"ref": "refs/heads/feature/x", "after": sha}, "s3cret")
        assert "no VCS trigger matches" in c.post("/api/webhooks/github/hello", content=raw, headers=headers).text
        raw, headers = signed({"ref": "refs/heads/main", "after": sha}, "wrong")
        assert c.post("/api/webhooks/github/hello", content=raw, headers=headers).status_code == 401
        assert c.post(
            "/api/webhooks/github/hello",
            content=raw,
            headers={
                **headers,
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": signed({}, "s3cret")[1]["X-Hub-Signature-256"],
            },
        ).status_code in (200, 401)


def test_webhooks_are_refused_without_a_configured_secret(tmp_path, repo):
    repo({"a": "1"})
    with app(tmp_path, repo.root, secret=None) as c:
        raw, headers = signed({"ref": "refs/heads/main", "after": "abc"}, "x")
        assert c.post("/api/webhooks/github/hello", content=raw, headers=headers).status_code == 503
