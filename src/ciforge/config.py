"""Pipeline configuration: the YAML file a project keeps in its repository.

The model follows TeamCity's vocabulary so the concepts transfer:

* a **pipeline** (TeamCity: project) has a **VCS root**, **parameters**, **triggers** and
  **build configurations**;
* a build configuration has **steps** run by a **runner** (local process or Docker container),
  **artifact rules** for what it publishes, **snapshot dependencies** (``depends_on``: the
  builds that must succeed first, on the same commit) and **artifact dependencies**
  (``artifacts_from``: files taken from those builds' published artifacts);
* asking for one build runs its whole **build chain**.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

NAME = r"^[a-z][a-z0-9_-]{0,62}$"
PARAM_REF = re.compile(r"%([A-Za-z_][A-Za-z0-9_.\-]*)%")


class ConfigError(ValueError):
    """The pipeline file is invalid; the message lists every problem found."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VcsRoot(Strict):
    url: str = Field(description="git URL or local repository path")
    branch: str = "main"


class LocalRunner(Strict):
    type: Literal["local"] = "local"
    shell: str = "bash"


class DockerRunner(Strict):
    type: Literal["docker"]
    image: str
    shell: str = "sh"
    options: list[str] = Field(default_factory=list, description="extra `docker run` options, e.g. --network host")
    mount_docker_socket: bool = False


Runner = Annotated[LocalRunner | DockerRunner, Field(discriminator="type")]


class Step(Strict):
    name: str
    run: str = Field(description="shell script; %param% references are substituted")
    env: dict[str, str] = Field(default_factory=dict)
    working_dir: str = "."
    timeout_minutes: float | None = None
    continue_on_error: bool = False


class ArtifactDependency(Strict):
    build: str
    paths: list[str] = Field(default_factory=lambda: ["**"], description="globs within that build's artifacts")
    into: str = "."


class BuildConfig(Strict):
    name: str | None = None
    runner: Runner = Field(default_factory=LocalRunner)
    steps: list[Step] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    artifacts_from: list[ArtifactDependency] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list, description="TeamCity rules: 'glob => target/dir'")
    params: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_minutes: float = 60
    run_if: Literal["dependencies_succeeded", "always"] = "dependencies_succeeded"
    fail_on_text: list[str] = Field(default_factory=list, description="fail the build if a step log contains any")


class VcsTrigger(Strict):
    type: Literal["vcs"]
    branches: list[str] = Field(default_factory=lambda: ["main"], description="fnmatch patterns")
    builds: list[str] = Field(default_factory=list, description="chains to run; empty = every build")


class ScheduleTrigger(Strict):
    type: Literal["schedule"]
    cron: str
    branch: str | None = None
    builds: list[str] = Field(default_factory=list)

    @field_validator("cron")
    @classmethod
    def valid_cron(cls, v: str) -> str:
        if not croniter.is_valid(v):
            raise ValueError(f"invalid cron expression: {v!r}")
        return v


Trigger = Annotated[VcsTrigger | ScheduleTrigger, Field(discriminator="type")]


class Pipeline(Strict):
    version: Literal[1]
    project: str = Field(pattern=NAME)
    description: str = ""
    vcs: VcsRoot | None = None
    params: dict[str, str] = Field(default_factory=dict)
    secrets: list[str] = Field(default_factory=list, description="environment variables masked in logs")
    max_parallel: int = Field(default=2, ge=1, le=32)
    triggers: list[Trigger] = Field(default_factory=list)
    builds: dict[str, BuildConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def check_graph(self) -> Pipeline:
        problems: list[str] = []
        for build_id, b in self.builds.items():
            if not re.match(NAME, build_id):
                problems.append(f"build id {build_id!r} must match {NAME}")
            for dep in b.depends_on:
                if dep not in self.builds:
                    problems.append(f"{build_id}: depends_on unknown build {dep!r}")
                if dep == build_id:
                    problems.append(f"{build_id}: depends on itself")
            for ad in b.artifacts_from:
                if ad.build not in self.builds:
                    problems.append(f"{build_id}: artifacts_from unknown build {ad.build!r}")
                elif ad.build not in self.upstream(build_id, strict=False):
                    problems.append(
                        f"{build_id}: artifacts_from {ad.build!r} needs it in depends_on (directly or"
                        " through the chain) so the artifacts exist"
                    )
            for rule in b.artifacts:
                if "=>" in rule and not rule.split("=>", 1)[1].strip():
                    problems.append(f"{build_id}: artifact rule {rule!r} has an empty target")
        for trig in self.triggers:
            for target in trig.builds:
                if target not in self.builds:
                    problems.append(f"trigger {trig.type}: unknown build {target!r}")
        cycle = self.find_cycle()
        if cycle:
            problems.append("dependency cycle: " + " -> ".join(cycle))
        problems.extend(self.unresolved_params())
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def upstream(self, build_id: str, strict: bool = True) -> set[str]:
        """Every build that must run before ``build_id`` (transitively)."""
        seen: set[str] = set()
        stack = list(self.builds[build_id].depends_on)
        while stack:
            b = stack.pop()
            if b in seen or b not in self.builds:
                if strict and b not in self.builds:
                    raise KeyError(b)
                continue
            seen.add(b)
            stack.extend(self.builds[b].depends_on)
        return seen

    def find_cycle(self) -> list[str] | None:
        state: dict[str, int] = {}
        path: list[str] = []

        def visit(b: str) -> list[str] | None:
            state[b] = 1
            path.append(b)
            for d in self.builds[b].depends_on:
                if d not in self.builds:
                    continue
                if state.get(d) == 1:
                    return [*path[path.index(d) :], d]
                if d not in state:
                    found = visit(d)
                    if found:
                        return found
            path.pop()
            state[b] = 2
            return None

        for b in self.builds:
            if b not in state:
                found = visit(b)
                if found:
                    return found
        return None

    def unresolved_params(self) -> list[str]:
        """References to parameters defined nowhere (TeamCity would mark the build incompatible)."""
        from ciforge.params import BUILTINS

        problems = []
        for build_id, b in self.builds.items():
            known = set(self.params) | set(b.params) | BUILTINS
            texts = [s.run for s in b.steps] + list(b.env.values()) + [v for s in b.steps for v in s.env.values()]
            if isinstance(b.runner, DockerRunner):
                texts.append(b.runner.image)
            for text in texts:
                for name in PARAM_REF.findall(text):
                    if name not in known and not name.startswith("env."):
                        problems.append(f"{build_id}: unknown parameter %{name}%")
        return sorted(set(problems))

    def chain(self, targets: list[str] | None = None) -> list[str]:
        """Builds to run for ``targets`` (all when empty), dependencies first, in a stable order."""
        wanted: set[str] = set(targets or self.builds)
        for t in list(wanted):
            if t not in self.builds:
                raise ConfigError(f"unknown build {t!r}; known: {', '.join(self.builds)}")
            wanted |= self.upstream(t)
        order: list[str] = []
        done: set[str] = set()

        def add(b: str) -> None:
            if b in done:
                return
            for d in self.builds[b].depends_on:
                add(d)
            done.add(b)
            order.append(b)

        for b in self.builds:
            if b in wanted:
                add(b)
        return order

    def display_name(self, build_id: str) -> str:
        return self.builds[build_id].name or build_id


def load(path: str | Path) -> Pipeline:
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"{p}: file not found") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"{p}: not valid YAML: {e}") from e
    return parse(data, str(p))


def parse(data: object, source: str = "<pipeline>") -> Pipeline:
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: the top level must be a mapping")
    try:
        return Pipeline.model_validate(data)
    except ValidationError as e:
        lines = []
        for err in e.errors():
            where = ".".join(str(x) for x in err["loc"]) or "(top level)"
            lines.append(f"  {where}: {err['msg']}")
        raise ConfigError(f"{source}: invalid pipeline\n" + "\n".join(lines)) from e
