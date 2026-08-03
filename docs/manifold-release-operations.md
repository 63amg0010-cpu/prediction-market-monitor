# Manifold staged release operations

Status: **operator contract; Production execution remains HOLD until every named receipt is accepted**

This document is the operator-facing release order for the reviewed Manifold source. It does not grant Production-write authority. Values such as `<attemptDir>` are documentation placeholders only; they are never accepted as evidence. Run commands only from the reviewed commit, with protected values supplied through the named environment variables.

The repository has one Alembic head: `20260727_0011`. The release path is deliberately split:

- `20260726_0009`: guarded local/Production starting revision;
- `20260727_0010`: compatibility revision and durable release ledger, with no Manifold source row;
- `20260803_0010a`: generic release-receipt foundation, with collection still disabled;
- `20260803_0010b`: append-only reviewed-root rebind after the receipt-export repair, with no source mutation;
- `20260803_0010c`: append-only reviewed-root rebind after aligning the workflow dispatcher with the durable reservation receipt schema, with no source mutation;
- `20260803_0010d`: append-only reviewed-root rebind after exposing authenticated release routes in the Vercel API routing table, with no source mutation;
- `20260727_0011`: append-only activation evidence and a prepared, disabled, unlinked Manifold source.

Neither migration activates collection. Manifold becomes active only at the later activation commit.

## No-secret and no-provider boundary

Before activation commit, the only permitted Manifold HTTP traffic is the owner-authorized read-only authorization probe/refresh through:

```text
uv run --package monitor-api python apps/api/scripts/manifold_evidence.py probe
uv run --package monitor-api python apps/api/scripts/manifold_evidence.py refresh
```

Those calls use the reviewed official GET routes and emit only an allowlisted, content-addressed projection. They are not collector runs and do not count toward cadence. Every collector, smoke collector, cadence request, or other Manifold request remains forbidden until activation commit.

The activation-evidence workflow receives no `DATABASE_URL`, `MIGRATION_DATABASE_URL`, or other database credential. Protected GitHub, Vercel, and Supabase identifiers are read only from the environment variables named by an `*-id-env` option, compared without printing, and retained only as permitted one-way hashes. No provider response body, structured author/profile/address field, secret, full database URL, or binding payload is allowed in a public receipt.

## Fresh guarded local verification

The two operating-system entrypoints are identical fail-fast orchestrators. Each invocation:

1. uses `fresh_search_release_gate.py local-db --phase reprovision` to accept only a loopback or committed test-container host and the exact database name `monitor_migration_qa`;
2. drops/recreates only that guarded disposable database, upgrades it to exact `20260726_0009`, and proves there is no Manifold row or pointer;
3. runs the ordered 20-command manifest, including the exact `0009 -> 0011` upgrade and verification, sole-head check, API/Web checks, secret scan, plan/scope checks, and link check;
4. disposes the same guarded database in `finally`, on success or failure.

Before manifest command 1, each wrapper invokes:

```text
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py local-db --phase reprovision --admin-database-url-env MIGRATION_QA_ADMIN_DATABASE_URL --database-url-env MIGRATION_QA_DATABASE_URL --expected-database monitor_migration_qa --required-start 20260726_0009 --guard-file apps/api/tests/fixtures/release-gate/local-qa-db-guard.json --json-out "<attemptDir>/migration-provision.json"
```

PowerShell and Git Bash must be run sequentially so each gets a newly provisioned `0009` start:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-fresh-search.ps1 -AttemptDir "<attemptDir>\task-11-pwsh" -DatabaseAdminUrlEnv MIGRATION_QA_ADMIN_DATABASE_URL -DatabaseUrlEnv MIGRATION_QA_DATABASE_URL -BaseSha "$BASE_SHA" -ReviewedSha "$REVIEWED_SHA"
& "C:\Program Files\Git\bin\bash.exe" ./scripts/verify-fresh-search.sh --attempt-dir "<attemptDir>/task-11-git-bash" --database-admin-url-env MIGRATION_QA_ADMIN_DATABASE_URL --database-url-env MIGRATION_QA_DATABASE_URL --base-sha "$BASE_SHA" --reviewed-sha "$REVIEWED_SHA"
```

The migration portion is fixed: `local-db --phase upgrade` requires current `20260726_0009`, targets `20260727_0011`, and invokes Alembic with that explicit target; `local-db --phase verify` requires both head and current revision `20260727_0011` and verifies the generated search expression, collation, trigram index, enum, prepared-disabled source, and inert pointers. A stale `0011` database is replaced before either OS run. Do not substitute an ad-hoc Alembic command, reuse a previous database, or point these scripts at Supabase/Production.

Both wrappers own this exact ordered command surface:

1. `uv sync --frozen --all-packages`
2. `pnpm install --frozen-lockfile`
3. `uv run --all-packages ruff check apps/api/app apps/api/scripts apps/api/tests workers/codex-worker/src workers/codex-worker/tests`
4. `uv run --all-packages basedpyright apps/api/app apps/api/scripts apps/api/tests workers/codex-worker/src workers/codex-worker/tests`
5. `uv run --package monitor-api pytest -p no:cacheprovider --basetemp <attemptDir>/pytest-command-05 apps/api/tests/contracts apps/api/tests/unit -q`
6. `uv run --package monitor-api pytest -p no:cacheprovider --basetemp <attemptDir>/pytest-command-06 apps/api/tests/integration/test_postgres_report_retention.py apps/api/tests/integration/test_collector_workflow.py apps/api/tests/integration/test_dashboard_api.py apps/api/tests/integration/test_verification.py -q -rs`
7. `uv run --package monitor-api pytest -p no:cacheprovider --basetemp <attemptDir>/pytest-command-07 apps/api/tests/migrations/test_20260727_manifold_search.py apps/api/tests/migrations/test_20260727_prepare_manifold.py -q`
8. `uv run --package monitor-api alembic -c apps/api/alembic.ini heads`, requiring sole head `20260727_0011`
9. `uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py local-db --phase upgrade`, with the exact guarded DB/start/target/output arguments supplied by the wrapper
10. `uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py local-db --phase verify`, with exact head/current/index/output arguments supplied by the wrapper
11. `uv run --package monitor-api python -m app.openapi`
12. `pnpm --filter @prediction-market/web check:api`
13. `pnpm --filter @prediction-market/web test`
14. `pnpm --filter @prediction-market/web typecheck`
15. `pnpm --filter @prediction-market/web lint`
16. `pnpm --filter @prediction-market/web build`
17. `uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py secret-static-scan`
18. `uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py plan-compliance`
19. `uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py scope-fidelity`
20. `uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py links`

The wrappers supply the plan-defined suffix arguments, write one redacted provision/command/exit/dispose/SHA manifest, and stop on the first failure. These listed prefixes are not permission to run a shortened command manually.

## Immutable evidence, reservation, and attempt protocol

Every release receipt is RFC-8785 canonical JSON and binds the reviewed commit SHA, approved plan SHA-256, root activation UUID, predecessor receipt SHA-256, and command-specific identity. Evidence is append-only and content-addressed:

- attestation generation `N`: `<attemptDir>/attestations/generation-N/activation-attestation-generation-N.json`;
- pre-first-write quota decision: `<attemptDir>/free-tier/pre-0010/free-tier-result.json`;
- workflow attempt: an immutable `attempt-1` or `attempt-2` directory containing its reservation, dispatch, selection, operation, and verified receipts.

After accepted `0010`, every manual workflow must first run the exact entrypoint prefix:

```text
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py dispatch-reserve
```

The reservation stores the workflow, exact display title, reviewed/plan SHA, activation nonce, distinct dispatch nonce, predecessor hash, database reservation time, second-truncated selection floor, and initially null claimed run ID. The workflow claims that reservation through the scoped API/OIDC endpoint before mutation. Selection then uses, in order:

```text
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py dispatch-workflow
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py select-run
uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py verify-receipt
```

`select-run` accepts only the claimed GitHub REST run ID with the exact workflow, title, protected ref, event, checkout SHA, nonces, reservation SHA, attempt, and creation floor. It searches no more than 10 pages/1,000 runs, polls every 5 seconds for at most 24 attempts, and requires two identical completed snapshots 10 seconds apart. Zero, multiple, orphaned, mismatched, or unstable runs are `HOLD`.

The one pre-ledger exception is the protected `0010` bootstrap. Its exact prefixes are `bootstrap-dispatch`, `bootstrap-select`, and `bootstrap-verify`; it consumes the descriptor-bound deployment prestate and credential-free no-spend receipt and seeds the ledger inside `0010`.

The exact attempt-indexed manual run names are:

- `ci-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}`;
- `collect-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}`;
- `verify-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}`;
- `activation-evidence-${{ inputs.activation_nonce }}-generation-${{ inputs.attestation_generation }}-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}`.

Artifact names carry the same attempt suffix. Activation evidence uses exactly the fourth string for both run and artifact identity. A generationless name, a non-attempt manual name, or the obsolete `activation-attestation-<nonce>-...` archive form is rejected.

Attempt 1 always writes to `attempt-1`. Attempt 2 is legal only when attempt 1 has a schema-valid terminal `verified.json` with `accepted=false`, `retry_permitted=true`, and a nonmutating or compensated state. Attempt 2 consumes that exact failed receipt and writes only to `attempt-2`. There is no internal retry, overwrite, retry after success, or third attempt.

## Exact staged Production sequence

Each stage starts only after the preceding receipt is accepted. A nonzero exit, `accepted=false` outside the defined retry envelope, missing/unknown value, predecessor mismatch, identity drift, or stale evidence means stop at `HOLD`.

1. **Review root and pre-first-write evidence.** Create one fresh activation nonce. Run `vercel-prestate`; capture same-day GitHub/Vercel/Supabase and read-only Production measurements; pass each immutable leaf through `canonical-hash` and `evidence-import`; join exactly the declared branches with `evidence-join`; run `free_tier_gate.py verify --phase pre-0010`; then run `no-spend-preflight`. The fixed result remains `<attemptDir>/free-tier/pre-0010/free-tier-result.json`. Nothing in this stage writes Production.
2. **One protected bootstrap to `0010`.** Use the exact `bootstrap-dispatch` -> `bootstrap-select` -> `bootstrap-verify` path. A committed operation whose external artifact is lost is recovered read-only with `recover-operation-receipt`; it is not retried. A precommit failed attempt may use the defined attempt-2 branch.
3. **Compatibility deployment.** Run attempt-indexed `vercel-deploy` for API and Web, then the compatibility alias operations and `compat-state`. Both deployments must be READY at `REVIEWED_SHA`, DB must be `0010`, and DCInside must remain healthy. The compatible API must exist before any reservation claim that uses its scoped endpoint.
4. **Attestation, activation evidence, reviewed CI, then `0011`.** Create the generation-specific record with `attest`, upload only through `attestation-secret-upload`, reserve/dispatch/select/verify the exact activation-evidence run, and run the reviewed CI attempt. Only then reserve/dispatch/select/verify the protected `0011` migration. It must finish at `20260727_0011` with Manifold prepared, disabled, and unlinked.
5. **Binding and activation, still with zero provider requests.** Follow the committed production-chain manifest: zero-provider binding-prestate workflow; `source_bindings.py capture-prestate`; `render`; `validate`; `apply-github`; zero-provider binding-handshake workflow; `handshake-github`; `fresh_search_release_gate.py activate --phase reserve`; `source_bindings.py finalize-github`; then `fresh_search_release_gate.py activate --phase commit`. The GitHub scope marker is written last. A missed cutoff leaves Manifold disabled.
6. **Post-commit smoke and proof.** Only after activation commit, reserve/dispatch/select/verify `smoke-collection` and `smoke-verifier`, then run `source_bindings.py verify-github`. Smoke modes never count as scheduled cadence.
7. **Materialize and report truthful day-zero state.** Run `materialize-chain` for the production chain, `cadence --phase initial`, then `materialize-chain` for the release chain and the read-only `production` check. Initial cadence must report `cadence_30d=HOLD`.

If a cutoff is missed after successful `0011`, do not rerun `0011`. When allowed, use a fresh reserve for the same untouched generation; otherwise restore any binding write, create generation `N+1`, verify fresh activation evidence, and use `activate --phase reprepare` at the immutable next-generation path.

The committed manifests are the executable source of step ordering:

- `apps/api/tests/fixtures/release-gate/production-chain-manifest.json`;
- `apps/api/tests/fixtures/release-gate/release-chain-manifest.json`;
- `apps/api/tests/fixtures/release-gate/matrix-b-chain-manifest.json`.

Do not replace them with an ad-hoc SQL write, browser action, bare helper name, mutable “latest” path, or hand-built linear list.

## Day-zero and 30-day truth

Day-zero activation proves only the reviewed release path. Aggregate status is `OPERATIONAL_PENDING_CADENCE`; it can never be `COMPLETE`.

One cadence epoch freezes the exact source set `{DCInside, Manifold}` and the half-open window `anchor <= t < anchor + 30d`. It contains exactly:

- 240 workflow-level collection slots for `17 */3 * * *`;
- 2,880 workflow-level verifier slots at 15-minute intervals.

Counts are not multiplied by source count. Each accepted slot contains successful subreceipts for both frozen sources. A missing, failed, late, revoked, wrong-scope, duplicate-only, or DCInside-only attempt does not pass the slot. A timely retry may pass only inside the original slot window. Smoke and manual handshake modes never count.

After the full 30-day window closes, the acceptance path takes fresh, younger-than-two-hour authorization/provider/Production captures, reuses only the same-SHA immutable local measurement, builds the exact eight-leaf input set and ninth free-tier result, captures the fifteen-member current-state manifest, and then runs the fixed `acceptance-refresh`, `cadence --phase acceptance`, scope-fidelity, final-lane, and aggregate chain. Only the exact current-state membership plus durable 240/2,880 evidence may produce `COMPLETE`.

## Rollback and privacy matrix

Rollback is not one generic restore:

- **Matrix A — before `0011`:** a failed `0010` operation verifies the safe prestate and remains `HOLD`; it does not restore across the enum boundary. A split compatibility deployment is compensated from captured protected prestates.
- **Matrix B — ordinary technical failure after `0011`:** run the protected `0011 -> 0010` downgrade attempt, restore and zero-provider-verify the DCInside-only binding, rebuild each changed Vercel project from exact protected `REVIEWED_SHA` into a new Production deployment when needed, and run `matrix-b-health`, Matrix-B `materialize-chain`, then `rollback-finalize`. Do not use `redeploy`, Promote, Instant Rollback, or deploy `BASE_SHA` against retained Manifold rows. Only `rollback-finalize` may append ordinary technical `restored`.
- **Matrix P — privacy or authorization-scope incident:** while `0011` evidence still identifies the activation, run `privacy-contain` and `privacy-purge` first; then execute Matrix B. Matrix-B receipts must leave state at `restore_writing`. Run `privacy-verify` last, including DB/search/API zero-content proof and public GitHub artifact/log/cache deletion checks. Only accepted `privacy-verify` may append `restored`; any unsearchable or undeletable surface is `PRIVACY_HOLD`.

Disposable QA uses only `apps/api/scripts/release_rollback_harness.py --mode disposable` with the guarded `monitor_migration_qa` database and stubbed external calls. It is not authorization for a Production downgrade, binding write, Vercel deployment, or promotion.

## Public repository evidence restrictions

The Production repository is public. Public commits, logs, caches, attestations, and Actions artifacts may contain only schema-closed redacted receipts and permitted hashes/statuses. They must not contain:

- plaintext database dumps or PostgreSQL/archive magic bytes;
- raw billing/API responses, DOM exports, or full-page billing screenshots;
- account identifiers or protected project/organization IDs;
- provider secrets, database URLs, binding payloads, author/profile/address fields, raw provider payloads, or offending privacy-incident text/URLs/IDs.

Provider screenshots, responses, DOM exports, and restorable binding bytes stay in owner-only, uncommitted local storage. A migration backup may enter a public artifact only as authenticated-encryption `.age` ciphertext produced by the pinned/checksummed release; the identity remains only in the protected environment, plaintext is decrypt-tested and deleted before upload, and only schema metadata plus plaintext/ciphertext hashes accompany it.

`secret-static-scan`, `plan-compliance`, `scope-fidelity`, and `links` are mandatory gates. Unknown provider counters are not zero, application counters are not provider-capacity reservations, and public-repository free-runner eligibility is not proof that quota, cadence, or privacy passed.
