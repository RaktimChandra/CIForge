# Results

Every number comes from a run that actually happened.

## Test suite — authoring sandbox, 2026-10-15

**Machine:** Linux, 2 vCPU, Python 3.13.16, git, Docker Engine 29.8.2.
**Method:** `pytest --cov=ciforge` with `CIFORGE_TEST_IMAGE=postgres:16-alpine` (Docker Hub is
blocked in the sandbox, so the Docker-runner test used an Alpine-based image already present;
CI uses `alpine:3.20`).

| | |
|---|---|
| Tests | 33 passed, 0 failed, 0 skipped (Docker runner included) |
| Line coverage | 92 % (983 statements, 80 missed) |
| `ruff check`, `ruff format --check` | clean |
| `mypy --strict` | no issues in 15 source files |

## Defects found by the tests while building

| Found by | Defect | Fix |
|---|---|---|
| `test_docker_runner_executes_inside_the_image_with_the_workspace_mounted` | `%param%` references in a Docker runner's image were not substituted (`docker: invalid reference format`) | the engine substitutes the image and options per build |
