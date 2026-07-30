# Runbook

Status: **Phase 5 operator procedures**

Use this runbook when setting up, checking, or recovering the monitor. Share only command names, exit codes, correlation IDs, and redacted status summaries. Never share `.env`, tokens, private keys, passwords, raw provider payloads, post authors, or full database URLs.

새 GitHub/Supabase/Vercel 계정을 처음 연결하는 경우에는 먼저 [Cloud deployment handoff](cloud-deployment-handoff.md)를 끝까지 실행합니다. Manifold를 다루는 모든 release/incident 작업은 [Manifold staged release operations](manifold-release-operations.md)의 단계와 receipt chain을 따릅니다. 이 runbook은 이미 연결된 시스템의 반복 운영과 복구용입니다.

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
| `OPERATIONAL_PENDING_CADENCE` or `cadence_30d=HOLD` | Day-zero release passed but the exact 30-day window has not. | Keep the product operationally pending; smoke/manual runs do not count. |
| `PRIVACY_HOLD` | A privacy/authorization incident cannot yet prove complete purge. | Keep state at `restore_writing`; do not use ordinary Matrix-B finalization. |

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
uv run --package monitor-api alembic -c apps/api/alembic.ini upgrade 20260727_0011
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

1. Stop new work and record the last accepted receipt; do not invent a replacement predecessor.
2. Before Manifold activation, leave it disabled. After `0011`, an ordinary technical/data/search/free-tier failure uses Matrix B: protected `0011 -> 0010`, DCInside-only binding restore/zero-provider verification, exact-`REVIEWED_SHA` Vercel rebuild when needed, `matrix-b-health`, Matrix-B `materialize-chain`, then `rollback-finalize`.
3. A privacy or authorization-scope violation uses Matrix P instead: `privacy-contain`, then `privacy-purge`, then Matrix B while state remains `restore_writing`, and finally `privacy-verify`. Only accepted `privacy-verify` may append `restored`; an incomplete provider deletion/search remains `PRIVACY_HOLD`.
4. Record only hashes, counts, timestamps, statuses, and the official evidence location. Never copy offending text, URLs, raw IDs, author/profile/address data, provider bodies, or secrets into a public receipt.
5. Do not scrape another route or substitute Toss/Naver/another provider as a workaround. Surface the dashboard state as blocked, skipped, failed, unauthorized, or privacy hold.

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

The sole Alembic head is `20260727_0011`; a different, missing, or multiple head is `HOLD`. Do not send Production directly to `head`. The protected sequence is:

1. collect and content-address fresh provider/local/Production evidence;
2. accept the complete pre-`0010` free-tier result and credential-free `no-spend-preflight`;
3. bootstrap exactly `20260726_0009 -> 20260727_0010`;
4. deploy and verify compatible API/Web at the reviewed SHA;
5. verify the generation- and attempt-bound activation evidence and reviewed CI;
6. migrate exactly `20260727_0010 -> 20260727_0011`;
7. prove Manifold is prepared, disabled, and unlinked before any binding or provider request.

Every post-`0010` manual workflow uses `dispatch-reserve` -> `dispatch-workflow` -> `select-run` -> `verify-receipt` with exact SHA/plan/activation/dispatch/reservation/attempt bindings. The protected `0010` pre-ledger exception uses only `bootstrap-dispatch` -> `bootstrap-select` -> `bootstrap-verify`. Run-name and attempt directory rules are fixed in [Manifold staged release operations](manifold-release-operations.md).

If a successful committed `0010`, `0011`, or `0011 -> 0010` operation loses its response/artifact, use the exact `fresh_search_release_gate.py recover-operation-receipt` path from the reviewed commit. It is read-only and reconstructs byte-identical ledger bytes; do not rerun a committed migration.

A Production rollback after `0011` is Matrix B, not a generic dump restore or `alembic downgrade` typed by hand. A privacy/authorization event must run containment and purge before Matrix B and may finish only through `privacy-verify`. Production-write or incident authorization is required separately.

Public Actions may upload a migration backup only as pinned/checksummed authenticated-encryption `.age` ciphertext after decrypt-test and plaintext deletion. Plaintext dumps, raw billing captures, DOM/screenshots, protected IDs, or database URLs never enter public commits, logs, caches, attestations, or artifacts.

The ordinary CI RP-07 test still runs without `RP07_DATABASE_URL`; its URL-gated skip is not real-DB proof. A disposable local/Preview DB at current head `20260727_0011` may run it with a direct async URL and must report `1 passed`. It creates test data and a restricted reader role, so it is forbidden on Production.

## Post-deploy core path

After Vercel and Supabase credentials are configured, verify the production path without source secrets first:

1. API project has Dashboard Root Directory `apps/api`; its isolated matrix job selects `VERCEL_API_PROJECT_ID` and runs `vercel pull`, `vercel build`, root `.vercel/output` check, and `vercel deploy --prebuilt` from the repository root.
2. Web project has Dashboard Root Directory `apps/web`; its separate matrix job selects `VERCEL_WEB_PROJECT_ID` and runs the same repository-root prebuilt sequence.
3. Call `/v1/health` on the API deployment. Accept only HTTP `200` with `status: ok` and `db: ok`; HTTP `200` with `status: degraded` and `db: unavailable` blocks deployment.
4. Log into the dashboard through the web deployment.
5. Confirm through a fresh, redacted provider capture that the repository is intentionally public, standard `ubuntu-latest` runners are used, paid/overage paths are disabled, and the public repository/artifacts contain none of the prohibited evidence classes. Unknown or private visibility is `HOLD`.
6. Follow the binding/activation chain. Before activation commit, only authorization probe/refresh traffic is allowed; binding-prestate and binding-handshake modes make zero provider requests.
7. After activation commit, reserve/dispatch/select/verify the exact attempt-indexed smoke collection and smoke verifier, then run `source_bindings.py verify-github`. Never trigger an unreserved manual workflow.
8. Materialize the release chain and run `cadence --phase initial`. Its truthful day-zero result is `cadence_30d=HOLD` / `OPERATIONAL_PENDING_CADENCE`.
9. Keep `.github/workflows/verify.yml` at `*/15 * * * *` and `.github/workflows/collect.yml` at `17 */3 * * *` on protected main only. After the exact half-open 30-day epoch, use the content-addressed acceptance capture/refresh chain in `docs/source-compliance.md`. `COMPLETE` requires exactly 240 workflow-level collection slots and 2,880 workflow-level verifier slots, each with both frozen-source subreceipts.
