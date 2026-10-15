"""Run history in SQLite: runs, the builds of each run, their steps and published artifacts."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    commit_sha  TEXT,
    branch      TEXT,
    trigger     TEXT NOT NULL,
    targets     TEXT NOT NULL,
    params      TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS builds (
    run_id      TEXT NOT NULL REFERENCES runs(id),
    build_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    position    INTEGER NOT NULL,
    depends_on  TEXT NOT NULL,
    status      TEXT NOT NULL,
    reason      TEXT,
    started_at  TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, build_id)
);
CREATE TABLE IF NOT EXISTS steps (
    run_id      TEXT NOT NULL,
    build_id    TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL,
    exit_code   INTEGER,
    started_at  TEXT,
    finished_at TEXT,
    log_path    TEXT,
    PRIMARY KEY (run_id, build_id, idx)
);
CREATE TABLE IF NOT EXISTS artifacts (
    run_id   TEXT NOT NULL,
    build_id TEXT NOT NULL,
    path     TEXT NOT NULL,
    size     INTEGER NOT NULL,
    PRIMARY KEY (run_id, build_id, path)
);
CREATE INDEX IF NOT EXISTS runs_project ON runs(project, created_at);
"""

TERMINAL = {"success", "failure", "timeout", "canceled", "error", "skipped"}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Store:
    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def _exec(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        with self._lock, self._conn() as c:
            c.execute(sql, args)

    def _query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock, self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    # runs ---------------------------------------------------------------------------------
    def create_run(
        self,
        run_id: str,
        project: str,
        commit: str | None,
        branch: str | None,
        trigger: str,
        targets: list[str],
        params: dict[str, str],
    ) -> None:
        self._exec(
            "INSERT INTO runs (id, project, commit_sha, branch, trigger, targets, params, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)",
            (run_id, project, commit, branch, trigger, json.dumps(targets), json.dumps(params), now()),
        )

    def set_run(self, run_id: str, status: str, **fields: str | None) -> None:
        cols = ["status = ?"] + [f"{k} = ?" for k in fields]
        self._exec(f"UPDATE runs SET {', '.join(cols)} WHERE id = ?", (status, *fields.values(), run_id))

    def run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not rows:
            return None
        r = rows[0]
        r["targets"] = json.loads(r["targets"])
        r["params"] = json.loads(r["params"])
        r["builds"] = self.builds(run_id)
        return r

    def runs(self, project: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if project:
            return self._query(
                "SELECT * FROM runs WHERE project = ? ORDER BY created_at DESC, rowid DESC LIMIT ?", (project, limit)
            )
        return self._query("SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))

    # builds -------------------------------------------------------------------------------
    def add_build(self, run_id: str, build_id: str, name: str, position: int, depends_on: list[str]) -> None:
        self._exec(
            "INSERT INTO builds (run_id, build_id, name, position, depends_on, status) VALUES (?, ?, ?, ?, ?,"
            " 'queued')",
            (run_id, build_id, name, position, json.dumps(depends_on)),
        )

    def set_build(self, run_id: str, build_id: str, status: str, **fields: str | None) -> None:
        cols = ["status = ?"] + [f"{k} = ?" for k in fields]
        self._exec(
            f"UPDATE builds SET {', '.join(cols)} WHERE run_id = ? AND build_id = ?",
            (status, *fields.values(), run_id, build_id),
        )

    def builds(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM builds WHERE run_id = ? ORDER BY position", (run_id,))
        for b in rows:
            b["depends_on"] = json.loads(b["depends_on"])
            b["steps"] = self._query(
                "SELECT * FROM steps WHERE run_id = ? AND build_id = ? ORDER BY idx", (run_id, b["build_id"])
            )
            b["artifacts"] = self._query(
                "SELECT path, size FROM artifacts WHERE run_id = ? AND build_id = ? ORDER BY path",
                (run_id, b["build_id"]),
            )
        return rows

    # steps --------------------------------------------------------------------------------
    def add_step(self, run_id: str, build_id: str, idx: int, name: str, log_path: str) -> None:
        self._exec(
            "INSERT INTO steps (run_id, build_id, idx, name, status, log_path, started_at)"
            " VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (run_id, build_id, idx, name, log_path, now()),
        )

    def finish_step(self, run_id: str, build_id: str, idx: int, status: str, exit_code: int | None) -> None:
        self._exec(
            "UPDATE steps SET status = ?, exit_code = ?, finished_at = ? WHERE run_id = ? AND build_id = ? AND idx = ?",
            (status, exit_code, now(), run_id, build_id, idx),
        )

    def step(self, run_id: str, build_id: str, idx: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM steps WHERE run_id = ? AND build_id = ? AND idx = ?", (run_id, build_id, idx))
        return rows[0] if rows else None

    # artifacts ----------------------------------------------------------------------------
    def add_artifacts(self, run_id: str, build_id: str, files: list[tuple[str, int]]) -> None:
        with self._lock, self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO artifacts (run_id, build_id, path, size) VALUES (?, ?, ?, ?)",
                [(run_id, build_id, p, s) for p, s in files],
            )
