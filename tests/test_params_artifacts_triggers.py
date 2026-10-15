from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import pytest

from ciforge import artifacts, config, params, triggers


def test_substitution_with_env_escapes_and_errors():
    assert params.substitute("%a%-%env.X%-100%%", {"a": "1"}, {"X": "y"}) == "1-y-100%"
    with pytest.raises(params.ParamError, match="unknown parameter %b%"):
        params.substitute("%b%", {})
    with pytest.raises(params.ParamError, match="environment variable NOPE"):
        params.substitute("%env.NOPE%", {}, {})


def test_secrets_are_masked_but_short_values_are_not():
    assert params.mask("token=s3cr3t-value ok", ["s3cr3t-value", "ab"]) == "token=*** ok"


def test_artifact_rules_preserve_the_path_below_the_wildcard(tmp_path):
    ws = tmp_path / "ws"
    for p in [
        "suites/api/target/surefire-reports/TEST-a.xml",
        "suites/ui/target/surefire-reports/TEST-b.xml",
        "out/app.txt",
        "README.md",
    ]:
        (ws / p).parent.mkdir(parents=True, exist_ok=True)
        (ws / p).write_text(p)
    store = tmp_path / "store"
    published = dict(
        artifacts.publish(
            ws, ["suites/*/target/surefire-reports/*.xml => reports", "out/** => bin", "README.md"], store
        )
    )
    assert set(published) == {
        "reports/api/target/surefire-reports/TEST-a.xml",
        "reports/ui/target/surefire-reports/TEST-b.xml",
        "bin/app.txt",
        "README.md",
    }
    into = tmp_path / "next"
    got = artifacts.fetch(store, ["reports/**/*.xml"], into)
    assert got == ["reports/api/target/surefire-reports/TEST-a.xml", "reports/ui/target/surefire-reports/TEST-b.xml"]
    assert (into / "reports/api/target/surefire-reports/TEST-a.xml").read_text().endswith("TEST-a.xml")


def test_artifact_downloads_cannot_escape_their_directory(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        artifacts.safe_path(tmp_path / "a", "../../etc/passwd")


def test_github_signatures_are_verified_in_constant_time():
    body = b'{"ref":"refs/heads/main"}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert triggers.verify_signature("s3cret", body, good)
    assert not triggers.verify_signature("s3cret", body + b" ", good)
    assert not triggers.verify_signature("other", body, good)
    assert not triggers.verify_signature("s3cret", body, None)
    assert not triggers.verify_signature("", body, good)


def test_push_events_and_branch_patterns():
    push = triggers.Push.parse(
        {"ref": "refs/heads/release/1.2", "after": "abc123", "repository": {"clone_url": "https://x/y.git"}}
    )
    assert push == triggers.Push("release/1.2", "abc123", "https://x/y.git")
    assert triggers.Push.parse({"ref": "refs/tags/v1", "after": "abc"}) is None
    assert triggers.Push.parse({"ref": "refs/heads/x", "after": "0" * 40}) is None
    p = config.parse(
        {
            "version": 1,
            "project": "p",
            "builds": {"a": {"steps": [{"name": "s", "run": "true"}]}},
            "triggers": [{"type": "vcs", "branches": ["main", "release/*"], "builds": ["a"]}],
        }
    )
    assert triggers.vcs_targets(p, "release/1.2") == [["a"]]
    assert triggers.vcs_targets(p, "feature/x") is None


def test_schedules_fire_once_per_crossing():
    p = config.parse(
        {
            "version": 1,
            "project": "p",
            "builds": {"a": {"steps": [{"name": "s", "run": "true"}]}},
            "triggers": [{"type": "schedule", "cron": "0 2 * * *"}],
        }
    )
    before = datetime(2026, 10, 15, 1, 59, 50, tzinfo=UTC)
    after = datetime(2026, 10, 15, 2, 0, 10, tzinfo=UTC)
    assert len(triggers.due_schedules(p, before, after)) == 1
    assert triggers.due_schedules(p, after, after.replace(minute=5)) == []
