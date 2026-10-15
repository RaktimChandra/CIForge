# Design notes

Each decision: what was chosen, the alternative, and why.

## C1 — TeamCity's vocabulary, YAML in the repository
Build configurations, snapshot dependencies, artifact dependencies, `%param%` references,
`glob => target` artifact rules and VCS/schedule triggers behave as in TeamCity, so the model
transfers to a real TeamCity setup (the TestHarness repository contains both a CIForge pipeline
and a TeamCity Kotlin DSL for the same stages). The file lives in the repository, as with
GitHub Actions and TeamCity's versioned settings, so pipeline changes are reviewed with code.

## C2 — Validate everything at load time
Unknown keys, unknown builds, cycles (reported as the actual cycle, e.g. `a -> c -> b -> a`),
artifact sources that are not upstream, invalid cron expressions and references to undefined
parameters are all errors before anything runs. A pipeline that would fail ten minutes in
because of a typo fails in a second instead.

## C3 — One commit per chain
A run resolves the branch to a commit once, mirrors the repository once, and every build checks
out that commit into its own workspace (`git clone --shared` from the mirror: cheap). This is
the guarantee snapshot dependencies exist for — the tests in a chain test the code the compile
step compiled — and it is tested by moving the branch during a run and pinning a commit.

## C4 — Threads and subprocesses, not asyncio
Builds are subprocess-heavy and few; a thread pool sized `max_parallel` is simpler to reason
about and to cancel than an event loop. Each local step runs in its own process group so a
timeout or cancel kills its children too (tested with background `sleep`s). Docker steps run
under a generated container name so they can be `docker kill`ed.

## C5 — Failure semantics are explicit
A failed, timed-out or errored build cancels its dependents with a recorded reason
(`dependency did not succeed: compile (failure)`); independent branches of the graph keep
going; `run_if: always` exists for report/cleanup builds. A run is `success` only if every build
succeeded.

## C6 — SQLite and the filesystem
Runs, builds, steps and artifact metadata in SQLite; logs and artifacts as files under the data
directory. Zero setup, transactional, enough for one server. A production version would use
PostgreSQL and object storage.

## C7 — Security basics
Webhooks require a configured secret and a valid `X-Hub-Signature-256` (HMAC-SHA256 compared
in constant time); without a secret they are refused (503), not accepted. Secret values are
masked in logs. Artifact downloads are resolved under their directory and refuse traversal.
The Docker socket is mounted only when a build asks for it (`mount_docker_socket`), because it
grants control of the host.

## C8 — Honest limits
Single process, no remote agents, no authentication on the API, no log streaming, no caches.
These are deliberate scope cuts for a learning project, listed in the README.
