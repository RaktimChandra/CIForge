from __future__ import annotations

from pathlib import Path

import pytest

from ciforge import config

EXAMPLE = Path(__file__).parent.parent / "examples" / "hello" / ".ciforge.yml"


def minimal(**builds):
    return {"version": 1, "project": "p", "builds": builds or {"a": {"steps": [{"name": "s", "run": "true"}]}}}


def step(run="true"):
    return [{"name": "s", "run": run}]


def test_the_example_is_valid_and_ordered_dependencies_first():
    p = config.load(EXAMPLE)
    assert p.chain() == ["compile", "test", "lint", "package"]
    assert p.chain(["test"]) == ["compile", "test"]
    assert p.upstream("package") == {"compile", "test", "lint"}


def test_unknown_fields_and_types_are_reported_with_their_location():
    data = minimal(a={"steps": step(), "runer": {"type": "local"}})
    with pytest.raises(config.ConfigError) as e:
        config.parse(data)
    assert "builds.a.runer" in str(e.value)


def test_cycles_are_rejected_with_the_cycle_spelled_out():
    data = minimal(
        a={"steps": step(), "depends_on": ["c"]},
        b={"steps": step(), "depends_on": ["a"]},
        c={"steps": step(), "depends_on": ["b"]},
    )
    with pytest.raises(config.ConfigError, match=r"dependency cycle: (a -> c -> b -> a|.*->.*->.*->)"):
        config.parse(data)


def test_unknown_dependencies_and_artifact_sources_outside_the_chain_are_rejected():
    data = minimal(a={"steps": step(), "depends_on": ["nope"]})
    with pytest.raises(config.ConfigError, match="depends_on unknown build 'nope'"):
        config.parse(data)
    data = minimal(a={"steps": step()}, b={"steps": step(), "artifacts_from": [{"build": "a"}]})
    with pytest.raises(config.ConfigError, match="needs it in depends_on"):
        config.parse(data)


def test_parameters_must_be_defined_somewhere():
    data = minimal(a={"steps": step("echo %missing% %vcs.commit% %env.HOME%")})
    with pytest.raises(config.ConfigError, match="unknown parameter %missing%"):
        config.parse(data)
    ok = minimal(a={"steps": step("echo %x%"), "params": {"x": "1"}})
    assert config.parse(ok).builds["a"].params == {"x": "1"}


def test_triggers_are_validated():
    data = minimal()
    data["triggers"] = [{"type": "schedule", "cron": "not a cron"}]
    with pytest.raises(config.ConfigError, match="invalid cron"):
        config.parse(data)
    data["triggers"] = [{"type": "vcs", "builds": ["ghost"]}]
    with pytest.raises(config.ConfigError, match="unknown build 'ghost'"):
        config.parse(data)


def test_asking_for_an_unknown_build_names_the_known_ones():
    p = config.load(EXAMPLE)
    with pytest.raises(config.ConfigError, match="known: compile, test, lint, package"):
        p.chain(["deploy"])


def test_files_that_are_not_yaml_or_missing_say_so(tmp_path):
    bad = tmp_path / "x.yml"
    bad.write_text("builds: [unclosed")
    with pytest.raises(config.ConfigError, match="not valid YAML"):
        config.load(bad)
    with pytest.raises(config.ConfigError, match="file not found"):
        config.load(tmp_path / "missing.yml")
