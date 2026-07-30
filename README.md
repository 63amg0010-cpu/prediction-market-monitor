# 예측시장 커뮤니티 반응 모니터

한국과 미국의 예측시장 커뮤니티에서 허용된 공개 글을 모아 개인용 대시보드로 확인하기 위한 프로젝트입니다. 저장소에는 비밀번호·API 키·서비스 토큰을 넣지 않으며, 승인되지 않은 수집원은 자동으로 켜지지 않습니다.

## 현재 구현 및 운영 상태

로컬 코드와 계약 테스트는 Phase 0–5 범위와 검색 가능한 Manifold 호환/활성화 경계를 포함합니다. 현재 Alembic 단일 head는 `20260727_0011`이지만, 이 migration은 Manifold를 준비된 비활성·미연결 상태로만 만듭니다. 실제 Production 활성화나 30일 완료를 의미하지 않습니다. 이전 Phase 5 검증 결과는 [Phase 5 evidence summary](docs/evidence/deployment-validation/summary.md)를 참고하세요.

- 수집원 어댑터는 공식 승인·약관·한도 증거가 없으면 `enabled: false`와 fail-closed 상태를 유지합니다.
- Windows Codex 분석 작업자는 capability proof가 실패한 현재 `blocked_capability` 상태이며, 승인된 sandbox 증명 없이는 실행하지 않습니다.
- 다음은 외부 증거가 필요한 **HOLD**입니다: live Docker·Supabase·Vercel 및 로그인→대시보드 핵심 경로, Manifold의 단계형 Production 활성화, 30일 freshness(정확히 workflow-level collection 240 슬롯 / verifier 2,880 슬롯), N400 인간 라벨 벤치마크, PC-off recovery(PC 전원 종료 후 복구). Day-zero smoke나 수동 실행은 30일 증거로 계산하지 않습니다.

## Phase 5 문서와 Windows 검증

비개발자용 실행 절차와 운영 계약은 `docs/`에 분리했습니다.

- [Windows 첫 설정과 안전 검증](docs/windows-setup.md)
- [Cloud deployment handoff](docs/cloud-deployment-handoff.md): GitHub·Supabase·Vercel 배포 절차와 외부 HOLD 조건
- [현재 API 라우트와 fail-closed 계약](docs/api-contract.md)
- [소스 승인/차단 기준](docs/source-compliance.md)
- [Manifold 단계형 배포·활성화·rollback 계약](docs/manifold-release-operations.md)
- [무료 한도 운영 정책](docs/free-tier-operations.md)
- [장애와 차단 상태 대응](docs/runbook.md)
- [30일/180일 보관과 재현 규칙](docs/data-retention.md)

PowerShell 검증 스크립트는 기본적으로 dry-run입니다. 실제 검증을 실행해도 비밀값은 출력하지 않습니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\Verify-LocalSetup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\windows\Verify-LocalSetup.ps1 -RunChecks
```

검색/Manifold release gate는 위 일반 설정 검사와 별개입니다. `MIGRATION_QA_ADMIN_DATABASE_URL`과 `MIGRATION_QA_DATABASE_URL`은 loopback 또는 커밋된 테스트 컨테이너의 정확한 `monitor_migration_qa` DB만 가리켜야 합니다. 다음 두 명령은 순서대로 실행하며, 각각 DB를 새로 만들어 `20260726_0009 -> 20260727_0011`을 검증한 뒤 성공·실패와 관계없이 폐기합니다. `<attemptDir>`, `$BASE_SHA`, `$REVIEWED_SHA`는 문서 표기이므로 실제 검토된 값으로 바꿉니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-fresh-search.ps1 -AttemptDir "<attemptDir>\task-11-pwsh" -DatabaseAdminUrlEnv MIGRATION_QA_ADMIN_DATABASE_URL -DatabaseUrlEnv MIGRATION_QA_DATABASE_URL -BaseSha "$BASE_SHA" -ReviewedSha "$REVIEWED_SHA"
& "C:\Program Files\Git\bin\bash.exe" ./scripts/verify-fresh-search.sh --attempt-dir "<attemptDir>/task-11-git-bash" --database-admin-url-env MIGRATION_QA_ADMIN_DATABASE_URL --database-url-env MIGRATION_QA_DATABASE_URL --base-sha "$BASE_SHA" --reviewed-sha "$REVIEWED_SHA"
```

## 준비물

- Windows 10/11과 Docker Desktop
- Git
- Python 3.12.8 및 `uv 0.5.30`
- Node.js 22.14.0 및 Corepack `pnpm 9.15.4`
- PostgreSQL 15 호환 Docker 이미지

버전은 `.python-version`, `.node-version`, `package.json`에 고정되어 있습니다. 다른 버전은 잠금 파일이나 테스트 결과를 바꿀 수 있습니다.

## Windows에서 처음 설정하기

PowerShell을 열어 저장소 폴더에서 실행합니다.

```powershell
uv sync --all-packages
corepack enable
corepack prepare pnpm@9.15.4 --activate
pnpm install --frozen-lockfile
Copy-Item .env.example .env
```

`.env`를 메모장으로 열고 `<...>` 표시를 실제 배포 값으로 모두 바꿉니다. 꺾쇠괄호가 하나라도 남으면 보안 설정이 완성되지 않은 것입니다. `.env`는 절대 커밋하거나 다른 사람에게 보내지 마세요.

관리자 비밀번호는 평문으로 저장하지 않고 Argon2id 해시를 사용합니다. 다음 명령을 실행하고 질문에 답한 뒤 출력된 `$argon2id$...` 한 줄을 `ADMIN_PASSWORD_ARGON2ID_HASH`에 넣습니다.

```powershell
$env:PYTHONPATH = "apps/api"
uv run --package monitor-api python -c "from app.services.identity.admin import AdminPasswordVerifier; from pydantic import SecretStr; print(AdminPasswordVerifier.hash_password(SecretStr(input('관리자 비밀번호: '))).get_secret_value())"
```

`SERVICE_TOKEN_ISSUER_PRIVATE_KEY`와 `SERVICE_TOKEN_ISSUER_PUBLIC_KEY`에는 승인된 Ed25519 키만 넣습니다. 키를 발급받지 않았다면 인증 교환을 사용하지 말고 운영 담당자의 승인을 먼저 받으세요. GitHub 값은 예시처럼 JSON 배열의 따옴표와 대괄호를 유지해야 합니다.

`WEB_PUBLIC_ORIGIN`은 Web뿐 아니라 API의 관리자 세션/CSRF 구성에도 사용되므로 두 서비스에 같은 환경별 origin을 넣습니다. `MONITOR_SCOPE_VERSION`은 reviewed source configuration과 같은 값으로 API에만 넣습니다. 두 값 모두 브라우저 공개 변수가 아닙니다.

## Docker로 API와 데이터베이스 실행하기

`.env`의 `DATABASE_URL`, `HOST_DATABASE_URL`, `MIGRATION_DATABASE_URL`은 SQLAlchemy/Alembic용 `postgresql+asyncpg://` 주소입니다. `PG_DUMP_DATABASE_URL`과 `PG_RESTORE_DATABASE_URL`은 native PostgreSQL 도구 전용 `postgresql://` 주소이며 async-driver URL을 `pg_dump`나 `pg_restore`에 전달하면 안 됩니다. `CONTAINER_DATABASE_URL`은 API 컨테이너가 PostgreSQL 서비스 이름 `db`로 접속하는 주소입니다.

```powershell
docker compose config
docker compose build api web
docker compose up -d db
docker compose ps
```

`db`의 상태가 `healthy`가 될 때까지 기다립니다. 그 뒤 마이그레이션을 적용합니다.

```powershell
$dotenv = Get-Content .env | Where-Object { $_ -match "^[^#][^=]+=" }
foreach ($line in $dotenv) { $name, $value = $line.Split("=", 2); Set-Item -Path "Env:$name" -Value $value }
$env:PYTHONPATH = "apps/api"
uv run --package monitor-api alembic -c apps/api/alembic.ini upgrade 20260727_0011
docker compose up api
```

API가 실행되면 다음 요청으로 공개 상태를 확인합니다.

```powershell
Invoke-WebRequest http://localhost:8000/v1/health | Select-Object StatusCode, Content, Headers
```

정상 상태는 HTTP `200`과 `{"status":"ok","version":"0.1.0","db":"ok"}`입니다. DB를 사용할 수 없는 경우에도 공개 health는 HTTP `200`과 `{"status":"degraded","version":"0.1.0","db":"unavailable"}`를 반환하며 Compose health와 배포 승인은 실패합니다. `503 service_unavailable`은 필수 어댑터가 구성되지 않은 로그인·대시보드 같은 보호 작업에서 기대하는 실패-폐쇄 응답입니다.

## OpenAPI와 테스트

FastAPI 앱은 Vercel의 `apps/api/api/index.py`에서도 같은 앱을 요청 단위로 내보냅니다. 서버리스 함수에서 수집이나 백그라운드 루프를 시작하지 않습니다. OpenAPI JSON은 같은 입력에서 항상 같은 바이트로 생성됩니다.

```powershell
$env:PYTHONPATH = "apps/api"
uv run --package monitor-api python -c "from pathlib import Path; from app.main import app; from app.openapi import write_openapi; write_openapi(app, Path('apps/api/openapi.json'))"
```

주요 검증 명령은 다음과 같습니다.

```powershell
uv run --package monitor-api pytest apps/api/tests/contracts apps/api/tests/unit -q
uv run --all-packages ruff format --check apps/api/app/main.py apps/api/app/openapi.py apps/api/api apps/api/tests/contracts/test_app_entrypoint.py apps/api/app/services/configuration/budget.py apps/api/app/services/configuration/loaders.py
uv run --all-packages ruff check apps/api/app/main.py apps/api/app/openapi.py apps/api/api apps/api/tests/contracts/test_app_entrypoint.py apps/api/app/services/configuration/budget.py apps/api/app/services/configuration/loaders.py
uv run --all-packages basedpyright apps/api/app/main.py apps/api/app/openapi.py apps/api/api/index.py apps/api/tests/contracts/test_app_entrypoint.py
```

## 보안 및 현재 운영 제한

- CORS 허용 출처는 기본값이 비어 있어 브라우저의 교차 출처 요청을 허용하지 않습니다.
- 모든 응답에 `X-Correlation-ID`가 붙고, 인증 및 입력 오류는 typed envelope로 반환되며 비밀값을 반향하지 않습니다.
- 기존 BFF·서비스 토큰 라우터는 등록되어 있지만, 저장소 어댑터가 주입되지 않은 상태에서는 임의 성공 대신 `503 service_unavailable`로 닫힙니다.
- Reddit·디시인사이드·Manifold·금융 커뮤니티는 약관·robots·승인 증거·무료 한도를 확인한 뒤에만 활성화합니다. DCInside는 검토된 활성 source이고, Manifold는 `0011`만 적용된 상태에서는 계속 비활성입니다.
- Windows Codex 작업자는 무도구·무네트워크·비밀값 제거 격리와 capability proof를 통과하기 전까지 실행하지 않습니다. PC가 켜져 있다고 해서 분석이 자동 완료된다고 표시하지 않습니다.

문제를 신고할 때는 오류 본문의 `correlation_id`, 실행한 명령, `docker compose ps` 결과만 공유하세요. 비밀번호·토큰·원문·작성자 정보는 로그나 이슈에 붙이지 마세요.
