# REST API

**Authentication.** `POST .../runs` and `POST .../cancel` need `Authorization: Bearer <token>`
with a token from `CIFORGE_API_TOKENS` (comma-separated, compared in constant time); with no
tokens configured they answer `503`. With `CIFORGE_READ_REQUIRES_TOKEN=true` every other `/api`
endpoint and the web UI need a token too. `/healthz` is always open; webhooks authenticate with
their HMAC signature instead.

| Method and path | Purpose |
|---|---|
| `GET /healthz` | liveness |
| `GET /api/projects` | configured projects with their chain and triggers (or the load error) |
| `POST /api/projects/{project}/runs` | start a run: `{"builds": [...], "branch": "...", "commit": "...", "params": {...}}` → `202 {"run_id"}` |
| `GET /api/runs?project=&limit=` | recent runs |
| `GET /api/runs/{id}` | a run with its builds, steps and artifacts |
| `POST /api/runs/{id}/cancel` | cancel a running run |
| `GET /api/runs/{id}/builds/{build}/steps/{n}/log` | a step's log (text) |
| `GET /api/runs/{id}/builds/{build}/artifacts/{path}` | download an artifact |
| `POST /api/webhooks/github/{project}` | GitHub webhook (`push`, `ping`); needs `X-Hub-Signature-256` |
| `GET /`, `GET /runs/{id}` | web UI |

Errors: `404` unknown project/run/artifact, `422` invalid pipeline or unknown build,
`401` bad webhook signature or missing/invalid bearer token, `503` webhooks without a configured
secret or run triggers without configured tokens, `400` artifact path
outside its directory. FastAPI also serves the OpenAPI document at `/docs`.
