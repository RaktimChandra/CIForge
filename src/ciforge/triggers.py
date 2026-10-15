"""VCS triggers (GitHub push webhooks) and schedule triggers (cron)."""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from croniter import croniter

from ciforge.config import Pipeline, ScheduleTrigger, VcsTrigger


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """GitHub's ``X-Hub-Signature-256: sha256=<hex HMAC of the raw body>``; constant-time compare."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@dataclass(frozen=True)
class Push:
    branch: str
    commit: str
    repository: str

    @staticmethod
    def parse(payload: dict[str, Any]) -> Push | None:
        """A branch push that created or moved a commit; None for tags and branch deletions."""
        ref = payload.get("ref", "")
        after = payload.get("after", "")
        if not ref.startswith("refs/heads/") or not after or set(after) == {"0"}:
            return None
        repo = payload.get("repository") or {}
        return Push(ref.removeprefix("refs/heads/"), after, repo.get("clone_url") or repo.get("html_url") or "")


def vcs_targets(pipeline: Pipeline, branch: str) -> list[list[str]] | None:
    """The build lists to run for a push to ``branch`` (one per matching trigger); None if nothing matches."""
    matched = [
        t.builds
        for t in pipeline.triggers
        if isinstance(t, VcsTrigger) and any(fnmatch.fnmatchcase(branch, pat) for pat in t.branches)
    ]
    return matched or None


def due_schedules(pipeline: Pipeline, last: datetime, now: datetime) -> list[ScheduleTrigger]:
    """Schedule triggers that fired in (last, now]."""
    due = []
    for t in pipeline.triggers:
        if isinstance(t, ScheduleTrigger):
            nxt = croniter(t.cron, last).get_next(datetime)
            if nxt <= now:
                due.append(t)
    return due
