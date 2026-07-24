# Free-tier operations

Status: **no-paid-fallback operating policy**

This project is designed for Vercel Hobby, Supabase Free, and GitHub Actions free allowances. It must stop or remain blocked before causing paid usage. Do not add a paid scheduler, paid model, paid database tier, or paid source API as an automatic fallback.

## Provider roles

| Provider | Role | Current limit decision |
|---|---|---|
| GitHub Actions | 3-hour collector and 15-minute independent verifier | Workflows are best effort. They must record observed timing; a scheduled event alone is not proof. |
| Vercel Hobby | Stateless API/web and once-daily report cron | Hobby cron is daily only; it is not used for the 3-hour collector. |
| Supabase Free | PostgreSQL control/data plane | Use internal 70% soft and 80% hard stop before quota exhaustion. |
| Windows PC + Codex CLI | Local analysis worker only after proof | Current state is `blocked_capability`; no alternate paid/free model fallback. |

See `docs/evidence/provider-budget-proof.md` for the dated provider evidence. Recheck provider pages and account dashboards immediately before deployment.

처음 배포하는 운영자는 [Cloud deployment handoff](cloud-deployment-handoff.md)를 0단계부터 순서대로 실행해야 합니다. 이 문서의 목록만 보고 설정을 추측하지 마세요.

## Budget actions

| Threshold | Required behavior |
|---|---|
| below 70% | Continue only for enabled and authorized sources. |
| 70% soft limit | Reduce scope, page count, or run size. Surface warning in operations UI. |
| 80% hard limit | Stop new collection/writes with `skipped_quota`. Do not retry into paid usage. |
| Unknown quota | Treat as blocked until measured. |

## GitHub Actions

Workflow files:

- `.github/workflows/ci.yml`: contracts/unit tests plus the exact `uv run --package monitor-api pytest apps/api/tests/integration/test_postgres_report_retention.py -q -rs` RP-07 retention-replay proof, then web checks/builds. RP-07 deliberately reports `SKIPPED [1] RP07_DATABASE_URL is required for real PostgreSQL proof` when no disposable direct PostgreSQL URL is supplied; that is a green gate for ordinary CI, not a claim of real-DB proof. Each isolated deployment matrix job then selects API or Web with `VERCEL_PROJECT_ID` and runs pinned `vercel@51.7.0` from the repository root with `pull -> build -> test .vercel/output -> deploy --prebuilt`.
- `.github/workflows/collect.yml`: `17 */3 * * *`, 6-minute timeout, collector-only scoped API execution.
- `.github/workflows/verify.yml`: independent `*/15 * * * *` verifier, 3-minute timeout, no source secrets. Scheduled jobs run only when `github.event.repository.private == false`.
- `.github/workflows/migrate.yml`: manual production migration only when `confirm` is `migrate-production`.

Required repository variables/secrets:

- `MONITOR_API_URL`
- `MONITOR_SCOPE_VERSION`
- `MONITOR_DEPLOYMENT_ACTIVATION_AT`
- `MONITOR_SOURCE_IDS`
- `REDDIT_USER_AGENT` only after Reddit approval
- `MONITOR_SOURCE_BINDINGS_JSON` only after source approval
- `REDDIT_OAUTH_ACCESS_TOKEN` only after Reddit approval
- `MIGRATION_DATABASE_URL` only in protected migration environment
- `PG_DUMP_DATABASE_URL` and `PG_RESTORE_DATABASE_URL` only in the protected migration environment
- `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_API_PROJECT_ID`, and `VERCEL_WEB_PROJECT_ID` only in the `preview-deploy` and `production-deploy` environments
- `ACTIONS_FREE_MODE=public-standard`, recorded outside the repository before enabling production schedules

Collectors and verifiers must exchange GitHub OIDC through the API. They must not connect directly to PostgreSQL except the protected migration workflow.

The ordinary CI job intentionally does not receive `RP07_DATABASE_URL`: no database password is placed in a broadly readable CI job. After a disposable local or Preview Supabase database has migrated to the single current head `20260723_0005`, an operator runs the same exact command once with a direct async URL in `RP07_DATABASE_URL`. Its binary result must be `1 passed`; `1 skipped` proves only that the CI URL gate is closed. Do not run this fixture against Production because it creates test rows and a restricted reader role.

GitHub documents standard GitHub-hosted runners as free and unlimited for public repositories. The production cadence therefore requires an intentionally public repository using `ubuntu-latest`; larger runners are not eligible for this rule. Confirm that the public repository contains no committed secrets before schedules are enabled.

Private GitHub Free cannot satisfy the required cadence under the no-paid policy. At workflow timeouts, collection is `8 * 6 * 31 = 1,488` minutes and independent verification is `96 * 3 * 31 = 8,928` minutes, for `10,416` minutes per 31-day month. The verifier's visibility condition skips every private-repository schedule. A private operator may run one manual verifier only after checking remaining included minutes and setting `authorize_private_minutes=true`; this is explicit one-run authorization, not approval for scheduled or paid usage. Production acceptance remains blocked in private mode.

Before enabling schedules, inspect repository visibility and Actions settings. Unknown or private visibility is fail-closed for the scheduled verifier. Do not remove the workflow visibility condition, enable larger runners, or add a paid scheduler as a fallback.

## Vercel

API config:

- `apps/api/vercel.json`
- Python function entry: `apps/api/api/index.py`
- Daily cron: `GET /api/cron/daily` at `20 15 * * *` UTC

Web config:

- `apps/web/vercel.json`
- build command: `pnpm build`
- install command: `pnpm install --frozen-lockfile`

Vercel project roots:

- API project root: `apps/api`
- Web project root: `apps/web`
- Vercel CLI current directory: repository root only; do not combine an app subdirectory cwd with the Dashboard Root Directory.
- CI build output: root `.vercel/output`, owned by one isolated API/Web matrix job and deployed before that job exits.
- Both projects require the same production/preview server-only credentials where applicable, but browser-exposed variables must never contain service, source, database, or BFF secrets.

Required environment values must be set in Vercel project settings, not committed. `BFF_CLIENT_CREDENTIAL`, service token keys, session secret, and cron secret are server-only.

The API project also requires server-only `WEB_PUBLIC_ORIGIN` for durable administrator session/CSRF composition and `MONITOR_SCOPE_VERSION` for verifier, administrator command, and daily cron composition. API and Web use the same environment-specific `WEB_PUBLIC_ORIGIN`; only the API receives `MONITOR_SCOPE_VERSION`. Neither value is a `NEXT_PUBLIC_` browser variable.

## Supabase

Use Supabase Free only after the account/project dashboard confirms the current free quotas. Track cached and uncached egress separately; do not merge them into one bucket for hard-stop decisions. Alembic uses the direct host through `MIGRATION_DATABASE_URL=postgresql+asyncpg://...`; native `pg_dump` and `pg_restore` use `PG_DUMP_DATABASE_URL=postgresql://...` and `PG_RESTORE_DATABASE_URL=postgresql://...`. Host runtime uses `DATABASE_URL`; Docker Compose maps `CONTAINER_DATABASE_URL` into the API container as `DATABASE_URL`. Do not use pooler URLs for migrations, pass an async-driver URL to libpq tools, or expose passwords in logs, docs, screenshots, or command output.

Production readiness remains blocked until the live database, migration, API, web app, source authorization, 30-day freshness evidence, and report/worker gates have fresh artifacts.
