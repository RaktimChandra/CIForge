# Pipeline file reference

A pipeline is one YAML file (by convention `.ciforge.yml` at the repository root). Unknown keys
are errors, so typos are caught when the file is loaded (`ciforge validate`).

## Top level

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | `1` | required | file format version |
| `project` | name | required | lowercase, `[a-z][a-z0-9_-]*` |
| `description` | text | `""` | shown in the UI |
| `vcs.url` | git URL or path | none | repository to build (`--source` overrides) |
| `vcs.branch` | text | `main` | default branch |
| `params` | map | `{}` | pipeline parameters |
| `secrets` | list | `[]` | environment variables whose values are masked in logs |
| `max_parallel` | 1–32 | `2` | builds of one run executing at the same time ("agents") |
| `triggers` | list | `[]` | see below |
| `builds` | map id → build | required | build configurations |

## Build configuration

| Key | Default | Meaning |
|---|---|---|
| `name` | the id | display name |
| `runner` | `{type: local}` | `{type: local, shell: bash}` or `{type: docker, image, shell: sh, options: [], mount_docker_socket: false}` |
| `steps` | required | list of `{name, run, env, working_dir, timeout_minutes, continue_on_error}` |
| `depends_on` | `[]` | snapshot dependencies: these builds run first, on the same commit |
| `artifacts_from` | `[]` | `{build, paths: [globs], into: dir}`; the source build must be upstream |
| `artifacts` | `[]` | rules `glob => target-dir` published after the steps (also when the build fails) |
| `params` | `{}` | build parameters (override pipeline ones) |
| `env` | `{}` | environment for every step (values may use parameters) |
| `timeout_minutes` | `60` | for the whole build |
| `run_if` | `dependencies_succeeded` | or `always` (e.g. a report build after a failing test build) |
| `fail_on_text` | `[]` | fail the build if any step log contains one of these strings |

## Parameters

`%name%` is replaced in step scripts, step and build `env` values, the Docker image and Docker
options. Resolution, later wins: pipeline `params`, build `params`, run parameters
(`ciforge run -p name=value`, API `params`), built-ins:

| Built-in | Value |
|---|---|
| `%vcs.commit%`, `%vcs.branch%` | the commit and branch being built |
| `%ciforge.run.id%`, `%ciforge.build.id%`, `%ciforge.build.name%` | identifiers |
| `%ciforge.workspace%`, `%ciforge.artifacts%` | absolute paths of the build's workspace and artifact store |

`%env.NAME%` reads the server's environment; `%%` is a literal `%`. A reference to an unknown
parameter is an error at load time. Steps also get `CIFORGE_RUN_ID`, `CIFORGE_BUILD_ID`,
`CIFORGE_COMMIT` and `CIFORGE_BRANCH` as environment variables.

## Triggers

```yaml
triggers:
  - type: vcs          # GitHub push webhook
    branches: [main, "release/*"]   # fnmatch patterns
    builds: [deploy]   # chains to run; empty = every build
  - type: schedule
    cron: "30 1 * * 1-5"            # server time zone UTC
    branch: main
    builds: [nightly]
```

## Artifact rules

`suites/*/target/surefire-reports/*.xml => reports` copies every matching file into the
build's artifact store under `reports/`, keeping the path below the first wildcard
(`reports/api-suite/target/surefire-reports/TEST-x.xml`). A rule without `=>` keeps the path
below the glob's fixed prefix. Artifacts are listed in the UI and downloadable from the API.
