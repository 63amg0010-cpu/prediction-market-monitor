# Runbook

Status: **Phase 5 operator procedures**

Use this runbook when setting up, checking, or recovering the monitor. Share only command names, exit codes, correlation IDs, and redacted status summaries. Never share `.env`, tokens, private keys, passwords, raw provider payloads, post authors, or full database URLs.

새 GitHub/Supabase/Vercel 계정을 처음 연결하는 경우에는 먼저 [Cloud deployment handoff](cloud-deployment-handoff.md)를 끝까지 실행합니다. 이 runbook은 이미 연결된 시스템의 반복 운영과 복구용입니다.

## First response checklist

1. Run the safe Windows setup check.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\Verify-LocalSetup.ps1
```

2. If dry-run is clean, run actual local checks.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\Verify-LocalSetup.ps1 -RunChecks
```

3. Check API health if the local API is running.

```powershell
Invoke-WebRequest http://127.0.0.1:8000/v1/health | Select-Object StatusCode, Content
```

4. Record the binary result: command, exit code, exact status field, and artifact path.

## Common states

| Symptom | Meaning | Action |
|---|---|---|
| `blocked_capability` from worker | Windows Codex safety proof is incomplete. | Do not run analysis. Keep queue blocked/pending. |
| `db: unavailable` in health | API process cannot use PostgreSQL. | Check Docker `db` health and `DATABASE_URL`; do not claim live data. |
| source `enabled: false` | Source is not approved for collection. | Keep adapter disabled until source evidence is approved. |
| dashboard read returns `503 service_unavailable` | Durable handler or auth dependency is not wired. | Fix configuration/repository wiring; do not replace with mock data. |
| verifier has missing slots | 30-day freshness clock failed. | Preserve evidence and restart the 30-day acceptance window after fix. |
| `skipped_quota` | Free-tier hard stop protected the account. | Reduce scope or wait for quota reset; do not upgrade automatically. |

## Local DB/API recovery

Start PostgreSQL:

```powershell
docker compose up -d db
docker compose ps
```

Apply migrations:

```powershell
$dotenv = Get-Content .env | Where-Object { $_ -match "^[^#][^=]+=" }
foreach ($line in $dotenv) { $name, $value = $line.Split("=", 2); Set-Item -Path "Env:$name" -Value $value }
$env:PYTHONPATH = "apps/api"
uv run --package monitor-api alembic -c apps/api/alembic.ini upgrade head
```

Run API:

```powershell
$env:PYTHONPATH = "apps/api"
uv run --package monitor-api uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Generate OpenAPI only when you intentionally want to refresh the local artifact:

```powershell
$env:PYTHONPATH = "apps/api"
uv run --package monitor-api python -c "from pathlib import Path; from app.main import app; from app.openapi import write_openapi; write_openapi(app, Path('apps/api/openapi.json'))"
```

## Source incident response

If a source policy, quota, auth, or route changes:

1. Disable the source or leave it disabled.
2. Record the observed status and official evidence location.
3. Do not scrape another route as a workaround.
4. Do not substitute Toss/Naver or another finance provider without a reviewed source record.
5. Surface the dashboard state as blocked, skipped, failed, or unauthorized.

## Windows worker incident response

The checked-in worker is not an analysis runner yet. It is a fail-closed capability state reporter.

```powershell
$env:PYTHONPATH = "workers/codex-worker/src"
uv run python -m monitor_worker
```

Expected current observable:

- exit code: non-zero
- JSON contains `capability_status:"blocked_capability"`
- JSON contains `alternate_model_fallback:"none"`

Any future change that claims worker enablement must include a new capability proof artifact and an adversarial test artifact. Until then, the worker must not lease, transmit, mark analyzed, or retry with another model.

## Production deployment gate

Production acceptance requires fresh evidence for:

- source authorization and route compliance;
- free-tier quota settings and account dashboards;
- migrated live Supabase database;
- Vercel API and web deployments;
- GitHub Actions OIDC collector and verifier;
- 30 consecutive UTC days of freshness evidence;
- Windows Codex capability proof;
- 400-item human-labeled benchmark with `correct/400 >= .85`;
- report reproduction after eligible raw-row purge.

Missing evidence means `blocked` or `not_started`, not done.

## Production migration procedure

Use the protected `.github/workflows/migrate.yml` workflow only after all three secrets point to the same direct Supabase endpoint: `MIGRATION_DATABASE_URL` uses `postgresql+asyncpg://` for Alembic, while `PG_DUMP_DATABASE_URL` and `PG_RESTORE_DATABASE_URL` use native `postgresql://` for libpq. The workflow validates these schemes without printing values, creates an ephemeral backup, checks current and repository heads, upgrades to head, verifies current again, and attempts native restore if the migration fails. At this frozen revision the sole Alembic head is `20260723_0005`; a different, missing, or multiple head result blocks deployment.

For an operator-run rollback inside free tooling, create a private local dump before migration and keep it outside the repository:

```powershell
pg_dump --format=custom --no-owner --no-acl --file "$env:TEMP\prediction-monitor-pre-migration.dump" "$env:PG_DUMP_DATABASE_URL"
```

If rollback is approved, restore that private dump:

```powershell
pg_restore --clean --if-exists --no-owner --no-acl --dbname "$env:PG_RESTORE_DATABASE_URL" "$env:TEMP\prediction-monitor-pre-migration.dump"
```

Never attach dumps to issues, commits, CI artifacts, or chat. They may contain original post text and operational state.

The ordinary CI gate runs `uv run --package monitor-api pytest apps/api/tests/integration/test_postgres_report_retention.py -q -rs` without `RP07_DATABASE_URL`. Its required observable is one URL-gated skip, not a false database success. After a Preview or disposable local DB reaches `20260723_0005`, run the same command once with a direct async `RP07_DATABASE_URL`; accept only `1 passed`. It creates test data and a restricted reader role, so it is forbidden on Production.

## Post-deploy core path

After Vercel and Supabase credentials are configured, verify the production path without source secrets first:

1. API project has Dashboard Root Directory `apps/api`; its isolated matrix job selects `VERCEL_API_PROJECT_ID` and runs `vercel pull`, `vercel build`, root `.vercel/output` check, and `vercel deploy --prebuilt` from the repository root.
2. Web project has Dashboard Root Directory `apps/web`; its separate matrix job selects `VERCEL_WEB_PROJECT_ID` and runs the same repository-root prebuilt sequence.
3. Call `/v1/health` on the API deployment. Accept only HTTP `200` with `status: ok` and `db: ok`; HTTP `200` with `status: degraded` and `db: unavailable` blocks deployment.
4. Log into the dashboard through the web deployment.
5. Confirm the GitHub repository is intentionally public, contains no committed secrets, and uses standard `ubuntu-latest` runners. Private repositories cannot pass the no-paid 15-minute verifier gate.
6. Trigger one manual verifier. On a private repository this requires a fresh included-minutes check and explicit `authorize_private_minutes=true`; it does not authorize a schedule.
7. Enable `.github/workflows/verify.yml` at `*/15 * * * *` and `.github/workflows/collect.yml` at `17 */3 * * *` only after the public-runner proof is current.
8. After 30 complete UTC days, apply the evidence procedure in `docs/source-compliance.md`. Keep production acceptance blocked unless all 240 collection slots and all 2,880 verifier slots pass with zero missing verifier slots.
