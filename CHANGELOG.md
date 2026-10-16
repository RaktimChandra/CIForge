# Changelog

## [1.1.0] — 2026-10-16
- API authentication: starting and cancelling runs needs a bearer token from
  `CIFORGE_API_TOKENS` (constant-time comparison); without tokens those endpoints are disabled
  (503), the same secure default as webhooks without a secret. Optional
  `CIFORGE_READ_REQUIRES_TOKEN=true` protects reads and the web UI too.

## [1.0.1] — 2026-10-16
- Documented a real pipeline run: TestHarness's nine-build chain on CIForge (RESULTS.md).

## [1.0.0] — 2026-10-15
- Pipeline file with build configurations, snapshot and artifact dependencies, parameters,
  secrets, VCS and schedule triggers; validated at load time.
- Engine: one commit per chain, parallel builds up to `max_parallel`, failure propagation,
  `run_if: always`, step and build timeouts, cancellation, `fail_on_text`.
- Local runner (process groups) and Docker runner.
- Artifact publishing and fetching with TeamCity-style rules.
- Server: REST API, HMAC-verified GitHub webhooks, cron schedules, web UI; Docker image.
- CLI: `validate`, `graph`, `run`, `serve`.
