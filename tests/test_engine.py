from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from ciforge import config
from ciforge.engine import Engine, RunRequest

EXAMPLE = Path(__file__).parent.parent / "examples" / "hello" / ".ciforge.yml"


def pipeline(builds: dict, **extra) -> config.Pipeline:
    return config.parse({"version": 1, "project": "t", "builds": builds, **extra})


def run(engine: Engine, p: config.Pipeline, source: Path, **kw) -> dict:
    req = RunRequest(project=p.project, pipeline=p, source=str(source), **kw)
    engine.execute(req)
    r = engine.store.run(req.run_id)
    assert r is not None
    return r


def test_the_example_chain_runs_end_to_end_and_hands_artifacts_down(tmp_path, repo):
    sha = repo({"README.md": "x"})
    engine = Engine(tmp_path / "data")
    r = run(engine, config.load(EXAMPLE), repo.root)
    assert r["status"] == "success"
    assert r["commit_sha"] == sha
    assert r["branch"] == "main"
    by_id = {b["build_id"]: b for b in r["builds"]}
    assert [b["build_id"] for b in r["builds"]] == ["compile", "test", "lint", "package"]
    assert all(b["status"] == "success" for b in r["builds"])
    assert [a["path"] for a in by_id["package"]["artifacts"]] == ["release.tgz"]
    app = tmp_path / "data/artifacts" / r["id"] / "compile/bin/app.txt"
    assert app.read_text().strip() == f"hello from commit {sha}"
    test_log = Path(by_id["test"]["steps"][0]["log_path"]).read_text()
    assert "tests passed" in test_log


def test_a_failed_build_cancels_its_dependents_but_not_independent_builds(tmp_path, repo):
    repo({"f": "1"})
    p = pipeline(
        {
            "a": {"steps": [{"name": "boom", "run": "echo going down; exit 3"}]},
            "b": {"depends_on": ["a"], "steps": [{"name": "s", "run": "true"}]},
            "c": {"steps": [{"name": "s", "run": "true"}]},
            "report": {"depends_on": ["a"], "run_if": "always", "steps": [{"name": "s", "run": "echo always"}]},
        }
    )
    r = run(Engine(tmp_path / "d"), p, repo.root)
    st = {b["build_id"]: (b["status"], b["reason"]) for b in r["builds"]}
    assert r["status"] == "failure"
    assert st["a"] == ("failure", "step 'boom' exited with 3")
    assert st["b"][0] == "canceled" and "dependency did not succeed: a (failure)" in st["b"][1]
    assert st["c"][0] == "success"
    assert st["report"][0] == "success"


def test_steps_stop_at_the_first_failure_unless_allowed_to_continue(tmp_path, repo):
    repo({"f": "1"})
    p = pipeline(
        {
            "a": {
                "steps": [
                    {"name": "soft", "run": "exit 1", "continue_on_error": True},
                    {"name": "hard", "run": "exit 2"},
                    {"name": "never", "run": "true"},
                ]
            }
        }
    )
    r = run(Engine(tmp_path / "d"), p, repo.root)
    steps = r["builds"][0]["steps"]
    assert [(s["name"], s["status"], s["exit_code"]) for s in steps] == [("soft", "failure", 1), ("hard", "failure", 2)]


def test_timeouts_kill_the_step_and_its_children(tmp_path, repo):
    repo({"f": "1"})
    p = pipeline({"a": {"steps": [{"name": "hang", "run": "sleep 30 & sleep 30; wait", "timeout_minutes": 0.02}]}})
    t0 = time.monotonic()
    r = run(Engine(tmp_path / "d"), p, repo.root)
    assert time.monotonic() - t0 < 10
    b = r["builds"][0]
    assert b["status"] == "timeout"
    assert b["steps"][0]["status"] == "timeout"
    assert "timed out" in Path(b["steps"][0]["log_path"]).read_text()


def test_parameters_builtins_env_and_secret_masking(tmp_path, repo, monkeypatch):
    sha = repo({"f": "1"})
    monkeypatch.setenv("DEPLOY_TOKEN", "tok-1234567")
    p = pipeline(
        {
            "a": {
                "params": {"who": "build"},
                "env": {"GREETING": "hi %who%"},
                "steps": [{"name": "s", "run": 'echo "$GREETING %vcs.commit% %level% $DEPLOY_TOKEN"'}],
            }
        },
        params={"level": "debug"},
        secrets=["DEPLOY_TOKEN"],
    )
    r = run(Engine(tmp_path / "d"), p, repo.root, params={"level": "trace"})
    log = Path(r["builds"][0]["steps"][0]["log_path"]).read_text()
    assert f"hi build {sha} trace ***" in log
    assert "tok-1234567" not in log


def test_fail_on_text_turns_a_green_exit_into_a_failure(tmp_path, repo):
    repo({"f": "1"})
    p = pipeline(
        {"a": {"fail_on_text": ["BUILD FAILURE"], "steps": [{"name": "s", "run": "echo '[INFO] BUILD FAILURE'"}]}}
    )
    r = run(Engine(tmp_path / "d"), p, repo.root)
    assert r["builds"][0]["status"] == "failure"
    assert "contains 'BUILD FAILURE'" in r["builds"][0]["reason"]


def test_independent_builds_run_in_parallel_up_to_max_parallel(tmp_path, repo):
    repo({"f": "1"})
    builds = {f"b{i}": {"steps": [{"name": "s", "run": "sleep 1"}]} for i in range(4)}
    t0 = time.monotonic()
    r = run(Engine(tmp_path / "d"), pipeline(builds, max_parallel=4), repo.root)
    assert r["status"] == "success"
    assert time.monotonic() - t0 < 3.5, "4 one-second builds with 4 agents should overlap"


def test_a_specific_commit_is_built_even_after_the_branch_moved(tmp_path, repo):
    first = repo({"v.txt": "one"})
    repo({"v.txt": "two"})
    p = pipeline({"a": {"steps": [{"name": "s", "run": "cat v.txt"}]}})
    r = run(Engine(tmp_path / "d"), p, repo.root, commit=first)
    assert r["commit_sha"] == first
    assert "one" in Path(r["builds"][0]["steps"][0]["log_path"]).read_text()


def test_unreachable_sources_end_the_run_as_error(tmp_path):
    p = pipeline({"a": {"steps": [{"name": "s", "run": "true"}]}})
    r = run(Engine(tmp_path / "d"), p, tmp_path / "no-such-repo")
    assert r["status"] == "error"
    assert r["builds"][0]["status"] == "canceled"


def test_cancel_stops_the_running_step_and_cancels_the_rest(tmp_path, repo):
    repo({"f": "1"})
    p = pipeline(
        {
            "a": {"steps": [{"name": "long", "run": "sleep 30"}]},
            "b": {"depends_on": ["a"], "steps": [{"name": "s", "run": "true"}]},
        }
    )
    engine = Engine(tmp_path / "d")
    req = RunRequest(project="t", pipeline=p, source=str(repo.root))
    engine.submit(req)
    t = threading.Thread(target=engine.execute, args=(req,))
    t.start()
    time.sleep(1.5)
    assert engine.cancel(req.run_id)
    t.join(timeout=15)
    r = engine.store.run(req.run_id)
    assert r["status"] == "canceled"
    assert [b["status"] for b in r["builds"]] == ["canceled", "canceled"]


@pytest.mark.docker
def test_docker_runner_executes_inside_the_image_with_the_workspace_mounted(tmp_path, repo, docker_available):
    if not docker_available:
        pytest.skip("Docker daemon not available")
    repo({"in.txt": "from the workspace"})
    image = os.environ.get("CIFORGE_TEST_IMAGE", "alpine:3.20")  # any Alpine-based image
    p = pipeline(
        {
            "a": {
                "runner": {"type": "docker", "image": "%img%"},
                "params": {"img": image},
                "steps": [{"name": "s", "run": "cat in.txt && cat /etc/alpine-release && echo made > out.txt"}],
                "artifacts": ["out.txt"],
            }
        }
    )
    r = run(Engine(tmp_path / "d"), p, repo.root)
    b = r["builds"][0]
    log = Path(b["steps"][0]["log_path"]).read_text()
    assert b["status"] == "success", log
    assert "from the workspace" in log
    assert [a["path"] for a in b["artifacts"]] == ["out.txt"]
