# API contract

Status: **Phase 5 handoff draft, current implementation matched**  
Audience: Windows operator and future implementers

This API is fail-closed. A route returning `503 service_unavailable`, `401`, `403`, `409`, or `422` is not a bug by itself; it often means a required credential, source approval, database repository, or capability proof is missing. Do not mark collection, analysis, reports, or deployment as complete from an empty response or a green health check.

## Current execution truth

| Area | Current state | What unblocks it |
|---|---|---|
| Public health | Implemented at `GET /v1/health`; always HTTP `200` with `status: ok, db: ok` or `status: degraded, db: unavailable`. | `DATABASE_URL` must point to reachable PostgreSQL for the healthy form; `503` applies to protected operations with unavailable adapters, not this public probe. |
| BFF/session/token contracts | Route contracts and durable service-token adapters are composed when every identity setting listed below validates and `DATABASE_URL` builds database sessions. Durable administrator session handlers additionally require a valid `WEB_PUBLIC_ORIGIN`. If a prerequisite is missing or invalid, only the affected adapter is unavailable and its protected route stays fail-closed (`503`), with no mock success. | Production still needs real account/credential setup (BFF, administrator, worker, cron, and GitHub) plus tested identity repositories and token issuer; conditional wiring is not live-deployment proof. |
| Dashboard/posts/reports reads | SQL reader exists when `DATABASE_URL` is valid. | Schema migration, real rows, and BFF read JWT. No mock success. |
| Collector control plane | SQL collection repository can wire when `DATABASE_URL` and a 32-byte `SESSION_HMAC_SECRET` exist. | Reviewed source-authorization evidence, approved source-account credentials, and scoped GitHub OIDC exchange; wiring alone does not make a source live. |
| Verification | SQL verifier repository wires when `DATABASE_URL` builds database sessions and a non-empty `MONITOR_SCOPE_VERSION` is present. Missing or invalid prerequisites leave the handler unavailable, so the route remains fail-closed (`503`). | Durable verifier repository and 30 consecutive UTC days of retained evidence. |
| Windows worker | Routes and local worker boundary exist, but capability proof is failed. | Approved Codex Pro/automation/legal evidence plus zero-tool, zero-network, zero-read, low-privilege, hard-cap sandbox proof. |
| Daily report cron | The SQL report/retention handler wires when `DATABASE_URL` builds database sessions, the configured report files parse, and `MONITOR_SCOPE_VERSION` matches the reviewed configuration. Missing or invalid prerequisites leave the handler unavailable; incomplete identity settings (including `CRON_SECRET`) also leave cron authentication unavailable, so the route fails closed. | Durable report/retention repository and `CRON_SECRET` in the API environment; production acceptance still requires fresh account/credential and deployment evidence. |

## Required response rules

- Every response must be secret-free and include `Cache-Control: no-store` on private/control surfaces.
- Error bodies use the typed envelope from `apps/api/app/core/errors.py` and must include a `correlation_id`.
- Unknown, pending, blocked, skipped, unauthorized, and failed states must not be converted to zero, neutral, or success.
- Write paths require scoped service identity, short token expiry, and idempotency or CAS evidence.

## Route surface

| Method/path | Principal | Success shape |
|---|---|---|
| `GET /v1/health` | none | `{status, version, db}` |
| `POST /v1/service-tokens/bff/exchange` | server-only `BFF_CLIENT_CREDENTIAL` | 5-minute scoped JWT |
| `POST /v1/service-tokens/github/exchange` | GitHub OIDC token | 10-minute collector/verifier JWT |
| `POST /v1/service-tokens/worker/exchange` | signed worker bootstrap request | 10-minute worker JWT |
| `POST /v1/auth/login` | `bff:auth` JWT | admin session, expiry, CSRF |
| `GET /v1/auth/session` | `bff:auth` JWT + session header | validated/rotated session |
| `POST /v1/auth/logout` | BFF JWT + session + CSRF | `204` |
| `GET /v1/dashboard` | `bff:read` JWT | metric and operations snapshot |
| `GET /v1/posts` | `bff:read` JWT | author-free paginated posts |
| `GET /v1/reports` | `bff:read` JWT | paginated latest report revisions |
| `GET /v1/reports/{report_date}` | `bff:read` JWT | one latest report revision or `404` |
| `POST /v1/commands/collection-retry` | `admin:command` BFF JWT + session + CSRF | `202` new or `200` duplicate command |
| `POST /v1/admin/daily-reconcile` | `admin:command` BFF JWT + session + CSRF | `202` new or `200` duplicate command |
| `POST /v1/collector/materialize` | `collector:materialize` | durable command IDs |
| `POST /v1/collector/commands/{command_id}/reserve` | `collector:reserve` | command reservation |
| `POST /v1/collector/commands/{command_id}/confirm-dispatch` | `collector:reserve` | dispatch confirmation |
| `POST /v1/collector/commands/{command_id}/claim` | `collector:claim` | command plus source runs |
| `GET /v1/collector/runs/{run_id}/checkpoint` | `collector:page_commit` | checkpoint replay tuple |
| `POST /v1/collector/runs/{run_id}/pages` | `collector:page_commit` | `201` first commit or `200` idempotent replay |
| `POST /v1/collector/commands/{command_id}/heartbeat` | `collector:heartbeat` | refreshed command state |
| `POST /v1/collector/commands/{command_id}/complete` | `collector:complete` | persisted completion response |
| `GET /v1/verification/snapshot` | `verify:read` | no-store verifier snapshot |
| `POST /v1/verification/observations` | `verify:write` | `201` expected-slot observation |
| `POST /v1/worker/lease` | `worker:lease` | `leased`, `empty`, or `blocked_capability` |
| `POST /v1/worker/heartbeat` | `worker:heartbeat` | extended lease expiry |
| `POST /v1/worker/ack` | `worker:ack` | `204` |
| `GET /api/cron/daily` | `Authorization: Bearer <CRON_SECRET>` | up to seven Seoul-date outcomes |

The authoritative page commit and report-retention invariants are in `docs/architecture/phase0-execution-contracts.md`.

## Environment variables

The API parses identity settings only when all required keys are present and valid:

- `API_BASE_URL`
- `WEB_PUBLIC_ORIGIN` (the exact browser-facing scheme, host, and port used for production BFF mutation checks)
- `SERVICE_TOKEN_KEY_ID`
- `SERVICE_TOKEN_ISSUER_PRIVATE_KEY`
- `SERVICE_TOKEN_ISSUER_PUBLIC_KEY`
- `BFF_CLIENT_CREDENTIAL`
- `BFF_CREDENTIAL_VERSION`
- `WORKER_BOOTSTRAP_SECRET`
- `WORKER_CREDENTIAL_VERSION`
- `CRON_SECRET`
- `ADMIN_PASSWORD_ARGON2ID_HASH`
- `SESSION_HMAC_SECRET`
- `GITHUB_REPOSITORY`
- `GITHUB_WORKFLOW_REFS`
- `GITHUB_ALLOWED_REFS`
- `GITHUB_ALLOWED_ENVIRONMENTS`
- `WEB_PUBLIC_ORIGIN` (administrator session and CSRF allowlist; same environment-specific value as the Web server)
- `MONITOR_SCOPE_VERSION` (must match the reviewed source configuration for verifier, admin command, and daily cron adapters)

`DATABASE_URL` is parsed separately. Missing or invalid database configuration makes database-backed handlers unavailable instead of fabricating empty data.

## Minimal local checks

```powershell
uv sync --all-packages
pnpm install --frozen-lockfile
uv run --package monitor-api pytest apps/api/tests/contracts apps/api/tests/unit -q
$env:PYTHONPATH = "workers/codex-worker/src"
uv run python -m monitor_worker
```

The worker command currently exits non-zero and prints `blocked_capability`. That is the expected honest state until the capability proof is replaced by a complete approved proof.
