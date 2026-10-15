"""``ciforge`` command line: validate, show the chain, run locally, or serve."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ciforge import __version__, config


def _params(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        k, sep, v = item.partition("=")
        if not sep:
            raise SystemExit(f"--param needs NAME=VALUE, got {item!r}")
        out[k] = v
    return out


def cmd_validate(a: argparse.Namespace) -> int:
    p = config.load(a.file)
    print(f"{a.file}: valid — project {p.project}, {len(p.builds)} build(s), chain: {' -> '.join(p.chain())}")
    return 0


def cmd_graph(a: argparse.Namespace) -> int:
    p = config.load(a.file)
    print("flowchart LR")
    for b, cfg in p.builds.items():
        print(f'  {b}["{p.display_name(b)}"]')
        for d in cfg.depends_on:
            print(f"  {d} --> {b}")
        for ad in cfg.artifacts_from:
            print(f"  {ad.build} -. artifacts .-> {b}")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    from ciforge.engine import Engine, RunRequest

    p = config.load(a.file)
    engine = Engine(Path(a.data_dir))
    req = RunRequest(
        project=p.project,
        pipeline=p,
        targets=a.build,
        params=_params(a.param),
        branch=a.branch,
        commit=a.commit,
        source=a.source,
        trigger="cli",
    )
    print(f"run {req.run_id}: {' -> '.join(p.chain(a.build))}", flush=True)

    def say(build: str, status: str, message: str) -> None:
        print(f"[{build}] {status}: {message}", flush=True)

    status = engine.execute(req, say)
    run = engine.store.run(req.run_id)
    assert run is not None
    print(f"\n{'build':<16} {'status':<9} reason")
    for b in run["builds"]:
        print(f"{b['build_id']:<16} {b['status']:<9} {b['reason'] or ''}")
    print(f"\nrun {req.run_id}: {status}  (logs and artifacts under {Path(a.data_dir).resolve()})")
    return 0 if status == "success" else 1


def cmd_serve(a: argparse.Namespace) -> int:
    from ciforge.server import Settings, serve

    s = Settings.from_env()
    if a.data_dir:
        s.data_dir = Path(a.data_dir)
    for item in a.project:
        name, _, path = item.partition("=")
        s.projects[name] = Path(path)
    serve(a.host, a.port, s)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ciforge", description="A small TeamCity-style CI orchestrator.")
    ap.add_argument("--version", action="version", version=f"ciforge {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check a pipeline file")
    v.add_argument("file")
    v.set_defaults(fn=cmd_validate)

    g = sub.add_parser("graph", help="print the build chain as a Mermaid flowchart")
    g.add_argument("file")
    g.set_defaults(fn=cmd_graph)

    r = sub.add_parser("run", help="run builds (and their chains) now, without a server")
    r.add_argument("file")
    r.add_argument("-b", "--build", action="append", default=[], help="build to run with its chain (repeatable)")
    r.add_argument("--source", help="git repository (path or URL) overriding the pipeline's vcs.url")
    r.add_argument("--branch")
    r.add_argument("--commit")
    r.add_argument("-p", "--param", action="append", default=[], help="NAME=VALUE (repeatable)")
    r.add_argument("--data-dir", default=".ciforge-data")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("serve", help="start the server (API, webhooks, schedules, web UI)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8111)
    s.add_argument("--data-dir")
    s.add_argument("--project", action="append", default=[], help="NAME=path/to/pipeline.yml (repeatable)")
    s.set_defaults(fn=cmd_serve)

    a = ap.parse_args(argv)
    try:
        return int(a.fn(a))
    except config.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
