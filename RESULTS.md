# Results

Every number comes from a run that actually happened.

## Test suite — authoring sandbox, 2026-10-15

**Machine:** Linux, 2 vCPU, Python 3.13.16, git, Docker Engine 29.8.2.
**Method:** `pytest --cov=ciforge` with `CIFORGE_TEST_IMAGE=postgres:16-alpine` (Docker Hub is
blocked in the sandbox, so the Docker-runner test used an Alpine-based image already present;
CI uses `alpine:3.20`).

| | |
|---|---|
| Tests | 34 passed, 0 failed, 0 skipped (Docker runner included), on Python 3.13.16 and 3.12 |
| Line coverage | 92 % (983 statements, 80 missed) |
| `ruff check`, `ruff format --check` | clean |
| `mypy --strict` | no issues in 15 source files |

## Defects found by the tests while building

| Found by | Defect | Fix |
|---|---|---|
| `test_docker_runner_executes_inside_the_image_with_the_workspace_mounted` | `%param%` references in a Docker runner's image were not substituted (`docker: invalid reference format`) | the engine substitutes the image and options per build |
| CI on Python 3.12 (5 failures; 3.13 green) | `out/** => bin` published nothing on 3.12: its `Path.glob` matches only directories for a trailing `**` | trailing `**` normalised to `**/*`; regression test |

## A real pipeline: TestHarness — run `20261016-053049-a47fe6`, 2026-10-16

**What:** [TestHarness](https://github.com/RaktimChandra/TestHarness)'s own `.ciforge.yml`
(9 builds: build → API, BDD, two UI shards, PIT, Gatling, quarantined tests → quality gate),
run with this repository at `2ef5457` on the 2-vCPU sandbox:
`ciforge run .ciforge.yml --source <TestHarness> --branch phase5 -p mvn="mvn -o -B" -p suite.args=...`.

| | |
|---|---|
| Result | **success**, all 9 builds |
| Wall-clock | **8 min 11 s** (05:30:49 → 05:39:00 UTC), `max_parallel: 2` |
| Snapshot dependency | every build checked out the same commit (`cff831e`) from the run's mirror |
| Artifact dependency | the quality gate fetched 48 report files from 4 upstream builds and counted 158 tests, 0 failed |
| Parallelism | `api` and `bdd` started together at 05:33:37; never more than 2 builds at once |

Per-build timings and log extracts are committed in TestHarness:
`experiments/ciforge/run-20261016-053049-a47fe6.json`.


## The same pipeline inside GitHub Actions — TestHarness `ciforge.yml`, 2026-10-20

TestHarness's `ciforge.yml` installs CIForge from this repository's `main` on a GitHub runner
(4 vCPU) and runs `.ciforge.yml` on the pushed commit, with the quality gate in the **Docker
runner** (`python:3.12-slim`).

| Actions run | CIForge run | Result | Wall-clock |
|---|---|---|---|
| 39410844981 | `20261020-105132-7c7e70` | **failure**: both UI shards failed for want of `chrome-linux` visual baselines; the gate was cancelled because its dependencies failed — the expected behaviour | — |
| 39419521879 | `20261020-120450-36aafa` | **success**, 9 of 9 builds, gate green in its container | **5 min 25 s** |

The fix was a TestHarness parameter (`-p suite.args=-Dharness.visual.platform=chrome-ci`), not
a CIForge change. Both run records are committed in TestHarness under `experiments/ciforge/`.
