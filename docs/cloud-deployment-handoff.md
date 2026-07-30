# Cloud deployment handoff

Status: **beginner handoff; live Production and 30-day acceptance remain HOLD**

이 문서는 처음 운영 환경을 연결하는 사람을 위한 안전 경계입니다. 실제 Manifold release의 receipt 순서와 rollback은 [Manifold staged release operations](manifold-release-operations.md)가 기준입니다. 이 문서의 설정을 끝냈다는 사실만으로 Production write, source activation, 또는 완료 판정을 허가하지 않습니다.

비밀번호, 토큰, 개인키, 전체 DB URL, provider 응답/DOM/청구 화면, 보호된 account/project/org ID는 터미널 출력·스크린샷·채팅·이슈·commit·public Actions artifact/cache에 남기지 않습니다. `<attemptDir>` 같은 표기는 설명용 placeholder일 뿐 실행 증거가 아닙니다.

## 0. 고정 이름과 현재 revision

운영 대상은 다음과 같습니다.

| 항목 | 고정값/역할 |
|---|---|
| GitHub repository | `63amg0010-cpu/prediction-market-monitor`, public |
| Vercel team | `63amg0010-5358-projects` |
| Vercel API project | `prediction-monitor-api`, Root Directory `apps/api` |
| Vercel Web project | `prediction-monitor-web`, Root Directory `apps/web` |
| Supabase | Preview와 Production을 분리한 Free project |
| Alembic start/compat/head | `20260726_0009` / `20260727_0010` / `20260727_0011` |

보호된 provider ID는 이름이 아니라 환경 변수 `GITHUB_REPOSITORY_ID`, `VERCEL_ORG_ID`, `VERCEL_API_PROJECT_ID`, `VERCEL_WEB_PROJECT_ID`, `SUPABASE_ORG_ID`, `SUPABASE_PROJECT_ID`에서만 읽습니다. 명령·receipt·로그·public artifact에 실제 값을 쓰지 않습니다.

`0010`은 compatibility/ledger revision이고 Manifold row가 없습니다. `0011`은 evidence를 준비하지만 Manifold를 disabled/null-pointer 상태로 둡니다. Migration 자체가 activation이 아닙니다.

## 1. 로컬 공개 전 검사

저장소 root에서 일반 Windows 설정 검사를 먼저 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Verify-LocalSetup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Test-ExactToolVersion.ps1
git check-ignore .env .env.local .vercel/project.json .vercel/repo.json
git ls-files .env .env.local .vercel/project.json .vercel/repo.json
```

마지막 명령이 한 줄이라도 출력되면 push하지 말고 노출된 값은 회전합니다. `.env`, `.env.local`, `.vercel`, `.gjc`, `.omo`, owner-only provider capture, binding prestate payload를 commit하지 않습니다.

검색/Manifold gate는 별도입니다. 두 URL 환경 변수는 loopback 또는 committed test-container의 정확한 `monitor_migration_qa`만 가리켜야 합니다. 다음 두 명령을 순서대로 실행합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-fresh-search.ps1 -AttemptDir "<attemptDir>\task-11-pwsh" -DatabaseAdminUrlEnv MIGRATION_QA_ADMIN_DATABASE_URL -DatabaseUrlEnv MIGRATION_QA_DATABASE_URL -BaseSha "$BASE_SHA" -ReviewedSha "$REVIEWED_SHA"
& "C:\Program Files\Git\bin\bash.exe" ./scripts/verify-fresh-search.sh --attempt-dir "<attemptDir>/task-11-git-bash" --database-admin-url-env MIGRATION_QA_ADMIN_DATABASE_URL --database-url-env MIGRATION_QA_DATABASE_URL --base-sha "$BASE_SHA" --reviewed-sha "$REVIEWED_SHA"
```

각 invocation은 guarded DB를 새로 만들고 정확한 `0009 -> 0011` 및 단일 head `20260727_0011`을 검증한 뒤 `finally`에서 폐기합니다. stale `0011` DB를 재사용하거나 임의 Alembic 명령으로 대체하거나 Supabase/Production URL을 넣으면 안 됩니다.

## 2. GitHub public/no-paid 경계

Repository는 의도적으로 Public이어야 하고 standard `ubuntu-latest`만 사용합니다. Actions budget/spend cap은 paid usage를 만들 수 없도록 설정하고 larger runner를 만들지 않습니다. `collect`와 `verify` schedule은 staged activation이 끝날 때까지 실행하지 않습니다.

다음 환경을 정확히 만듭니다.

- `preview-deploy`
- `production-deploy`
- `production-migration`
- `production-collector`
- `production-verifier`

배포·migration 환경은 protected main과 reviewer를 사용합니다. Collector/verifier 환경도 protected main만 허용합니다. CI/read-only workflow가 reservation claim만을 위해 Production migration credential을 받으면 안 됩니다.

Public repository이므로 commit/log/cache/attestation/artifact에는 schema-closed redacted receipt와 허용된 hash/status만 남깁니다. Plaintext DB dump, raw billing/API body, DOM, full-page billing screenshot, account ID, protected project/org ID, database URL, secret, binding payload, raw provider payload, structured author/profile/address data는 금지됩니다. Backup upload는 pinned/checksummed `age`로 암호화하고 protected 환경에서 decrypt-test한 `.age` ciphertext만 허용합니다.

## 3. Supabase Free 연결

Preview와 Production Free project를 분리합니다. Free plan, paid/overage/add-on disabled 상태를 fresh provider capture로 증명하지 못하면 `HOLD`입니다.

- Runtime `DATABASE_URL`: transaction pooler.
- Alembic `MIGRATION_DATABASE_URL`: direct `postgresql+asyncpg://`.
- Native backup/restore URL: `PG_DUMP_DATABASE_URL`와 `PG_RESTORE_DATABASE_URL`, direct `postgresql://`.

Pooler URL로 migration하지 않고 async-driver URL을 `pg_dump`/`pg_restore`에 넘기지 않습니다. Production measurement는 `free_tier_gate.py measure-production --read-only`만 사용하며 row text를 내보내지 않습니다.

Supabase connector/API lookup은 항상 비어 있지 않은 `SUPABASE_PROJECT_ID`/`SUPABASE_ORG_ID` 환경 값을 사용합니다. Actual ID를 command text나 receipt에 복사하지 않습니다.

## 4. Vercel Hobby 연결

두 project 모두 Hobby이며 paid/overage/add-on이 disabled여야 합니다.

- API Root Directory: `apps/api`
- Web Root Directory: `apps/web`
- 두 project 모두 Root 외부 source 포함: enabled
- system environment variables 자동 노출: enabled
- Vercel Git auto deployment: disabled
- CLI working directory: repository root
- isolated build output: root `.vercel/output`
- pinned CLI: `vercel@51.7.0`

API server-only 환경 키:

`DATABASE_URL`, `API_BASE_URL`, `WEB_PUBLIC_ORIGIN`, `MONITOR_SCOPE_VERSION`, `SERVICE_TOKEN_KEY_ID`, `SERVICE_TOKEN_ISSUER_PRIVATE_KEY`, `SERVICE_TOKEN_ISSUER_PUBLIC_KEY`, `BFF_CLIENT_CREDENTIAL`, `BFF_CREDENTIAL_VERSION`, `WORKER_BOOTSTRAP_SECRET`, `WORKER_CREDENTIAL_VERSION`, `CRON_SECRET`, `ADMIN_PASSWORD_ARGON2ID_HASH`, `SESSION_HMAC_SECRET`, `GITHUB_REPOSITORY`, `GITHUB_WORKFLOW_REFS`, `GITHUB_ALLOWED_REFS`, `GITHUB_ALLOWED_ENVIRONMENTS`.

Web server-only 환경 키:

`API_BASE_URL`, `BFF_CLIENT_CREDENTIAL`, `BFF_CREDENTIAL_VERSION`, `WEB_PUBLIC_ORIGIN`.

API/Web의 같은 환경은 BFF/origin 값이 일치해야 합니다. `MONITOR_SCOPE_VERSION`은 API와 protected GitHub collector/verifier에만 둡니다. 이 값을 `NEXT_PUBLIC_` 변수로 복제하지 않습니다.

Direct Vercel release 작업은 attempt-indexed `fresh_search_release_gate.py vercel-deploy`/`vercel-restore`만 사용합니다. 한 invocation은 pull/build/deploy/inspect/alias/health를 각각 한 번만 호출하고 internal retry를 하지 않습니다. `redeploy`, Promote, Instant Rollback은 Matrix B 대체 수단이 아닙니다.

## 5. GitHub 환경 값

환경 secret/variable 이름은 workflow와 일치해야 합니다.

- Deploy: `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_API_PROJECT_ID`, `VERCEL_WEB_PROJECT_ID`
- Protected migration: `MIGRATION_DATABASE_URL`, `PG_DUMP_DATABASE_URL`, `PG_RESTORE_DATABASE_URL`
- Verifier: `MONITOR_API_URL`, `MONITOR_SCOPE_VERSION`
- Collector: `MONITOR_API_URL`, `MONITOR_SCOPE_VERSION`, `MONITOR_DEPLOYMENT_ACTIVATION_AT`, `MONITOR_SOURCE_IDS`, `MONITOR_SOURCE_BINDINGS_JSON`
- Reddit: `REDDIT_USER_AGENT`, `REDDIT_OAUTH_ACCESS_TOKEN` only after separate approval

Activation evidence uses only Environment secret `MANIFOLD_ACTIVATION_ATTESTATION_JSON`; that workflow must not receive any DB credential. Binding state mutates only `production-collector` values through the serialized `source_bindings.py` chain. Do not hand-edit a Manifold-enabled binding.

## 6. Pre-first-write evidence

Create one fresh `ACTIVATION_NONCE` and derive the approved plan SHA only from the protected dual-review record. Before any Production mutation:

1. run `fresh_search_release_gate.py vercel-prestate`;
2. capture fresh public GitHub, both Vercel projects, Supabase, local measurement, and read-only Production measurement;
3. pass the six leaves through the exact `canonical-hash` -> content-addressed `evidence-import` branches and join them with `evidence-join`;
4. run `free_tier_gate.py verify --phase pre-0010`;
5. run `fresh_search_release_gate.py no-spend-preflight`.

The result path is immutable: `<attemptDir>/free-tier/pre-0010/free-tier-result.json`. Provider and Production captures must be younger than two hours. Unknown is not zero. Every applicable current/projected normal-workload dimension must be strictly below 70%, and public GitHub/Hobby/Free plus disabled paid/overage/add-ons must be proven.

No-spend is credential-free and may authorize only the single protected `0010` bootstrap. The separate Production measurement is read-only. Neither is a Production mutation.

## 7. Immutable dispatch and attempt rule

After `0010`, every manual workflow uses this exact prefix order:

```text
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py dispatch-reserve
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py dispatch-workflow
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py select-run
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py verify-receipt
```

The sole pre-ledger `0010` exception uses:

```text
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py bootstrap-dispatch
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py bootstrap-select
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py bootstrap-verify
```

Each post-`0010` path binds reviewed SHA, approved plan SHA-256, root activation UUID, distinct dispatch UUID, reservation SHA, exact attempt, protected ref, event, and checkout SHA. Unreserved, orphaned, ambiguous, stale, or mismatched runs are `HOLD`.

Exact manual run names are:

- `ci-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}`
- `collect-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}`
- `verify-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}`
- `activation-evidence-${{ inputs.activation_nonce }}-generation-${{ inputs.attestation_generation }}-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}`

Artifact names carry the same attempt suffix. Attempt 2 may consume only the exact terminal failed attempt-1 `verified.json` with retry permitted and a safe/compensated state. Never overwrite attempt 1, retry after success, use attempt 3, or use a non-attempt manual template.

## 8. Exact staged Production order

Proceed only when each receipt exits 0 and reports accepted.

1. Accept review root, prestate, fresh evidence graph, pre-`0010` free-tier result, and no-spend preflight.
2. Use `bootstrap-dispatch` -> `bootstrap-select` -> `bootstrap-verify` for exactly `0009 -> 0010`.
3. Use attempt-indexed `vercel-deploy` for compatible API then Web, compatibility aliases, and `compat-state`. Both must be READY at `REVIEWED_SHA`, with DB `0010` and DCInside healthy.
4. Use `attest`, `attestation-secret-upload`, and the exact generation/attempt activation-evidence workflow. Run reviewed CI.
5. Reserve/dispatch/select/verify exactly `0010 -> 0011`. Prove Manifold is prepared, disabled, and unlinked.
6. Follow the committed production-chain manifest: zero-provider binding-prestate workflow; `source_bindings.py capture-prestate`, `render`, `validate`, `apply-github`; zero-provider binding-handshake workflow; `handshake-github`; `fresh_search_release_gate.py activate --phase reserve`; `source_bindings.py finalize-github`; `fresh_search_release_gate.py activate --phase commit`.
7. Only after commit, reserve/dispatch/select/verify smoke collection and smoke verifier, then `source_bindings.py verify-github`.
8. Run production-chain `materialize-chain`, `cadence --phase initial`, release-chain `materialize-chain`, and the read-only `production` check.

Before activation commit, only reviewed authorization `manifold_evidence.py probe`/`refresh` HTTP traffic is permitted. Binding handshake makes zero provider requests. If a cutoff is missed after successful `0011`, never rerun `0011`; use same-generation fresh reserve only when allowed, otherwise restore binding if needed and use fresh generation `N+1` plus `activate --phase reprepare`.

Day-zero result is always `cadence_30d=HOLD` / `OPERATIONAL_PENDING_CADENCE`. Smoke/manual runs do not count.

## 9. Health and post-commit checks

API `/v1/health` must return HTTP 200 with `status: ok`, `db: ok`, and `X-Correlation-ID`. `status: degraded`/`db: unavailable` is not deployable health. Web login, dashboard, status, posts, reports, logout, literal positive/negative search, keyword+search AND behavior, DCInside 90d, and both source freshness displays must use durable Production data with no secrets in response/UI.

Enable protected-main schedules only after activation and smoke verification:

- collect: `17 */3 * * *`
- verifier: `*/15 * * * *`

## 10. 30-day acceptance

One frozen epoch uses the half-open interval `anchor <= t < anchor + 30d` and exact source set `{DCInside, Manifold}`. It contains exactly 240 workflow-level collection slots and 2,880 workflow-level verifier slots. Counts are not multiplied by two; each accepted slot has successful subreceipts for both sources.

Missing, failed, late, revoked, wrong-scope, partial-source, or duplicate-only slots fail. A retry may pass only inside the original slot window. Manual handshake and smoke modes never count. The acceptance interval is exactly the half-open 30-day epoch.

After the window closes, rerun fresh authorization/provider/Production capture, reuse only the content-addressed same-`REVIEWED_SHA` local measurement, and execute the exact `acceptance-input-manifest` -> acceptance free-tier verify -> `acceptance-capture` -> `acceptance-refresh` -> `cadence --phase acceptance` -> scope-fidelity -> final-lane -> aggregate chain. Only the exact fifteen-member current-state receipt plus durable 240/2,880 proof may emit `COMPLETE`.

## 11. Matrix B and privacy incidents

Before `0011`, a failed `0010` verifies safe `0009`/residue and remains `HOLD`; it does not restore across the enum boundary.

After `0011`, ordinary technical/data/search/free-tier failure uses Matrix B: protected `0011 -> 0010`, DCInside-only binding restore and zero-provider verification, attempt-indexed Vercel exact-`REVIEWED_SHA` rebuild for every changed project, `matrix-b-health`, Matrix-B `materialize-chain`, and `rollback-finalize`. Never deploy `BASE_SHA` against retained Manifold rows.

Privacy or authorization-scope failure uses Matrix P: run `privacy-contain`, then `privacy-purge`, then Matrix B while state remains `restore_writing`, and finally `privacy-verify`. Only accepted `privacy-verify` may append `restored`. If a public artifact/log/cache or provider surface cannot be deleted, searched, or confirmed expired, remain `PRIVACY_HOLD`.

Disposable QA uses only `apps/api/scripts/release_rollback_harness.py --mode disposable` against guarded `monitor_migration_qa` with stubbed external calls. It never authorizes Production migration, binding, deployment, or promotion.

## 완료 판정

설정과 day-zero release가 모두 성공해도 상태는 운영 대기입니다. `COMPLETE`는 current-state evidence가 fresh/identity-bound/public-safe이고 30일 동안 정확한 240/2,880 슬롯이 모두 통과한 뒤 acceptance aggregate가 승인한 경우에만 사용합니다. 확인하지 못한 항목은 `HOLD`, `blocked`, 또는 `not_started`로 남깁니다.
