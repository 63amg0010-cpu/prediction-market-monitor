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

처음 배포하는 운영자는 [Cloud deployment handoff](cloud-deployment-handoff.md)를 0단계부터 순서대로 실행해야 합니다. Manifold release는 [Manifold staged release operations](manifold-release-operations.md)의 immutable evidence/attempt chain도 함께 따라야 합니다. 이 문서의 목록만 보고 설정을 추측하지 마세요.

## Budget actions

| Threshold | Required behavior |
|---|---|
| below 70% | Continue only when every applicable current and projected dimension is known and strictly below 70%. |
| equal to or above 70% | `HOLD`; stop new activation/dispatch and reduce the bounded workload. Do not retry into paid usage. |
| Unknown, stale, paid, overage-enabled, or unbounded | `HOLD`; it is never interpreted as zero. |

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
- `DCINSIDE_USER_AGENT` while the reviewed DCInside route is enabled
- `REDDIT_USER_AGENT` only after Reddit approval
- `MONITOR_SOURCE_BINDINGS_JSON` only after source approval
- `REDDIT_OAUTH_ACCESS_TOKEN` only after Reddit approval
- `MIGRATION_DATABASE_URL` only in protected migration environment
- `PG_DUMP_DATABASE_URL` and `PG_RESTORE_DATABASE_URL` only in the protected migration environment
- `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_API_PROJECT_ID`, and `VERCEL_WEB_PROJECT_ID` only in the `preview-deploy` and `production-deploy` environments
- `ACTIONS_FREE_MODE=public-standard`, recorded outside the repository before enabling production schedules

Collectors and verifiers must exchange GitHub OIDC through the API. They must not connect directly to PostgreSQL except the protected migration workflow.

The ordinary CI job intentionally does not receive `RP07_DATABASE_URL`: no database password is placed in a broadly readable CI job. After a disposable local or Preview Supabase database has migrated to the single current head `20260727_0011`, an operator runs the same exact command once with a direct async URL in `RP07_DATABASE_URL`. Its binary result must be `1 passed`; `1 skipped` proves only that the CI URL gate is closed. Do not run this fixture against Production because it creates test rows and a restricted reader role.

GitHub documents standard GitHub-hosted runners as free for public repositories. The production cadence therefore requires a freshly verified public repository using `ubuntu-latest`; larger runners are not eligible for this rule. The same capture must prove paid/overage paths are disabled. Public-runner eligibility is only a cost input, not cadence evidence.

The exact acceptance horizon is 30 days: 240 collection slots and 2,880 verifier slots. Quota projection separately enumerates every provider's real hourly/daily/weekly/billing-month window and every bounded initial/retry/manual/rollback attempt that overlaps it. A private or unknown-visibility repository cannot satisfy the no-paid scheduled cadence and remains `HOLD`; a one-off private manual authorization does not authorize schedules or Production acceptance.

Before enabling schedules, inspect repository visibility and Actions settings. Unknown or private visibility is fail-closed for the scheduled verifier. Do not remove the workflow visibility condition, enable larger runners, or add a paid scheduler as a fallback.

## Immutable free-tier evidence

Before the first Production write, the local measurement, four provider captures, and read-only Production measurement are six independent immutable leaves. Each is passed through the exact prefixes:

```text
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py canonical-hash
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py evidence-import
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py evidence-join
uv run --package monitor-api python apps/api/scripts/free_tier_gate.py verify --phase pre-0010
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py no-spend-preflight
```

The accepted pre-first-write result is always `<attemptDir>/free-tier/pre-0010/free-tier-result.json`. A post-`0010` refresh, when used, writes a new content-addressed directory and may only tighten the envelope; it never overwrites or substitutes for the pre-`0010` result.

`U=max(10,000,3×trailing_30d_page_requests)` is the accepted normal single-admin scenario, not a bound on hostile traffic and not a provider-capacity reservation. The projection includes all known authorized requests, scheduled slots, bounded retries, rejected duplicate/orphan allowance, partial/failed payloads, artifacts, logs, encrypted backup reserve, Matrix B, and both API/Web deployment attempts. Provider drift or observed abuse is immediate operational `HOLD` and affected cadence slots receive no credit.

Provider/account captures and read-only Production measurements must be younger than two hours when consumed. A provider UI/API that omits a counter yields `unknown`, never zero. Zero or N/A requires both immutable project configuration and provider usage evidence.

## Public-repository evidence boundary

The repository and its standard-runner artifacts are public. Only schema-closed redacted numeric/window projections, receipt hashes, statuses, neutral public evidence URLs, and authenticated-encryption backup ciphertext may be committed or uploaded.

Plaintext dumps, raw billing/API responses, DOM exports, full-page billing screenshots, account identifiers, protected project/organization IDs, database URLs, secrets, raw provider payloads, and author/profile/address fields stay out of commits, logs, caches, attestations, and artifacts. Screenshots/responses/DOM remain owner-only and uncommitted; imports retain only allowlisted projections and content hashes. Backup artifacts must be pinned/checksummed `.age` ciphertext, decrypt-tested in the protected environment, with plaintext deleted before upload.

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

Use Supabase Free only after the account/project dashboard confirms the current free quotas. Track cached and uncached egress separately; do not merge them into one bucket for hard-stop decisions. Alembic uses either the IPv6 direct endpoint or the official IPv4 fallback, Supavisor session mode on port 5432, through `MIGRATION_DATABASE_URL=postgresql+asyncpg://...`; native `pg_dump` and `pg_restore` use the same direct/session-mode host through `PG_DUMP_DATABASE_URL=postgresql://...` and `PG_RESTORE_DATABASE_URL=postgresql://...`. Host runtime uses transaction mode through `DATABASE_URL`; Docker Compose maps `CONTAINER_DATABASE_URL` into the API container as `DATABASE_URL`. Never use Supavisor transaction mode (6543) for migrations, pass an async-driver URL to libpq tools, or expose passwords in logs, docs, screenshots, or command output.

Production readiness remains blocked until the live database, migration, API, web app, source authorization, 30-day freshness evidence, and report/worker gates have fresh artifacts.
