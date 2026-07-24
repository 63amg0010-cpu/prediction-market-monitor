# Windows setup

Status: **operator guide for a non-developer Windows user**

이 문서는 Windows에서 저장소를 안전하게 준비하고, 현재 가능한 검증을 실행하는 절차입니다. 아직 승인되지 않은 수집원, Windows Codex 분석, 30일 freshness, 400개 인간 라벨 정확도, live DB 배포는 완료로 표시하지 않습니다.

로컬 검증 뒤 실제 무료 클라우드를 처음 만드는 절차는 [Cloud deployment handoff](cloud-deployment-handoff.md)를 0단계부터 따르세요.

## 1. 설치할 프로그램

Windows PowerShell에서 아래 명령이 실행되어야 합니다.

```powershell
uv --version
node --version
corepack --version
docker --version
```

필요 버전:

- Python: `.python-version`의 `3.12.8`
- Node.js: `.node-version`의 `22.14.0`
- pnpm: `package.json`의 `9.15.4`
- Docker Desktop: PostgreSQL 15 컨테이너 실행용

## 2. 저장소 준비

PowerShell을 열고 저장소 폴더로 이동합니다.

```powershell
cd "C:\Users\UserK\Desktop\개발폴더\예측시장 커뮤니티 반응"
uv sync --all-packages
corepack enable
corepack prepare pnpm@9.15.4 --activate
pnpm install --frozen-lockfile
Copy-Item .env.example .env
```

`.env` 파일을 메모장으로 열고 `<...>` 값을 실제 값으로 바꿉니다. 비밀번호, 토큰, 개인키는 채팅이나 이슈에 붙이지 마세요.

```powershell
notepad .env
```

관리자 비밀번호는 평문이 아니라 Argon2id 해시만 저장합니다.

```powershell
$env:PYTHONPATH = "apps/api"
uv run --package monitor-api python -c "from app.services.identity.admin import AdminPasswordVerifier; from pydantic import SecretStr; print(AdminPasswordVerifier.hash_password(SecretStr(input('관리자 비밀번호: '))).get_secret_value())"
```

출력된 `$argon2id$...` 한 줄을 `.env`의 `ADMIN_PASSWORD_ARGON2ID_HASH`에 넣습니다.

`DATABASE_URL`, `HOST_DATABASE_URL`, `MIGRATION_DATABASE_URL`은 Windows에서 실행하는 SQLAlchemy/Alembic용 `postgresql+asyncpg://` 주소입니다. `PG_DUMP_DATABASE_URL`과 `PG_RESTORE_DATABASE_URL`은 같은 호스트의 libpq 전용 `postgresql://` 주소입니다. `CONTAINER_DATABASE_URL`은 Docker Compose 안의 API 컨테이너용이므로 `db`를 사용합니다. `API_BASE_URL`은 Windows 호스트에서 볼 API 주소이고, `CONTAINER_API_BASE_URL`은 web 컨테이너가 API 컨테이너로 접속하는 주소입니다.

웹 대시보드는 `.env`의 `WEB_PUBLIC_ORIGIN`과 동일한 주소로 접속합니다. 로컬 기본값은 `http://127.0.0.1:3000`이며, 운영 환경에서는 실제 HTTPS 주소를 정확히 설정해야 로그인·로그아웃·관리 명령의 동일 출처 검사가 통과합니다.

API도 같은 `WEB_PUBLIC_ORIGIN`을 받아 관리자 세션과 CSRF origin을 구성합니다. `MONITOR_SCOPE_VERSION`은 `config/sources.reviewed.yml`의 scope와 같아야 verifier, 관리자 명령, daily cron이 실제 어댑터로 연결됩니다. 둘 중 하나가 없거나 다르면 해당 보호 기능은 `503`으로 닫힙니다.

## 3. 안전 검증 스크립트

기본 실행은 dry-run입니다. 비밀값을 출력하지 않고, 어떤 검사를 실행할지 보여줍니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\Verify-LocalSetup.ps1
```

실제 검증까지 실행하려면 다음을 사용합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\Verify-LocalSetup.ps1 -RunChecks
```

Docker Desktop이 없는 상태에서는 Docker 검사만 건너뜁니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\Verify-LocalSetup.ps1 -RunChecks -SkipDocker
```

스크립트가 확인하는 것:

- `uv`, `node`, `corepack`, `pnpm`, `docker` 명령 존재 여부
- 필수 lock/config 파일 존재 여부
- `.env` 필수 키 존재 여부와 `<...>` 자리표시자 잔존 여부
- Python/API 단위 및 계약 테스트
- URL이 없는 RP-07 실제 PostgreSQL 보존·재현 증명은 `skipped`로 명확히 표시되는지
- Windows worker가 현재 `blocked_capability`로 닫혀 있는지
- Docker compose 설정 문법, 단 `--quiet`만 사용해 비밀값을 출력하지 않음

## 4. 로컬 API와 DB 실행

`.env`가 준비된 뒤 실행합니다.

```powershell
docker compose up -d db
$dotenv = Get-Content .env | Where-Object { $_ -match "^[^#][^=]+=" }
foreach ($line in $dotenv) { $name, $value = $line.Split("=", 2); Set-Item -Path "Env:$name" -Value $value }
$env:PYTHONPATH = "apps/api"
uv run --package monitor-api alembic -c apps/api/alembic.ini upgrade head
uv run --package monitor-api uvicorn app.main:app --host 127.0.0.1 --port 8000
```

다른 PowerShell 창에서 상태를 확인합니다.

```powershell
Invoke-WebRequest http://127.0.0.1:8000/v1/health | Select-Object StatusCode, Content
```

정상 응답은 HTTP `200`, `status: ok`, `db: ok`입니다. DB 연결 실패도 HTTP `200`이지만 `status: degraded`, `db: unavailable`이므로 Docker health와 배포 승인을 실패로 처리합니다. HTTP `503`은 필수 구성이 없는 보호 작업의 실패-폐쇄 응답이지 공개 health의 DB 상태 응답이 아닙니다.

모든 컨테이너를 백그라운드로 실행하려면 migration 완료 뒤 다음을 실행합니다. 웹은 `http://127.0.0.1:3000`에서 열고, 상태 확인 후 멈출 때는 마지막 명령만 실행합니다. `down -v`는 로컬 DB 데이터를 지우므로 사용하지 마세요.

```powershell
docker compose up -d --build api web
docker compose ps
docker compose stop
```

## 5. RP-07 실제 PostgreSQL 증명은 Preview/로컬에서만 실행

기본 검사와 GitHub CI는 `RP07_DATABASE_URL`이 없으면 정확히 한 테스트를 `skipped`로 표시합니다. 이는 비밀 URL을 CI에 넣지 않았다는 뜻이지 실제 DB 증명을 통과했다는 뜻이 아닙니다.

로컬 Docker DB 또는 별도 Preview Supabase DB가 migration head `20260723_0005`까지 적용된 경우에만, 새 PowerShell에서 아래를 한 번 실행할 수 있습니다. 이 테스트는 보존·재현 경로를 실제 PostgreSQL 권한으로 검증하기 위해 고정 테스트 행과 제한된 reader role을 만듭니다. Production에서는 실행하지 마세요.

```powershell
$dotenv = Get-Content .env | Where-Object { $_ -match "^[^#][^=]+=" }
foreach ($line in $dotenv) { $name, $value = $line.Split("=", 2); Set-Item -Path "Env:$name" -Value $value }
$env:RP07_DATABASE_URL = $env:MIGRATION_DATABASE_URL
uv run --package monitor-api pytest apps/api/tests/integration/test_postgres_report_retention.py -q -rs
Remove-Item Env:RP07_DATABASE_URL
```

성공은 `1 passed`입니다. `1 skipped`는 URL을 전달하지 않은 일반 CI 상태이고, failure는 migration/권한/보존 계약 문제이므로 배포를 중단해야 합니다.

## 6. GitHub 무료 15분 검증 조건

운영 승인에 필요한 `.github/workflows/verify.yml`은 `*/15 * * * *`로 독립 실행됩니다. 이 무료 경로는 저장소가 의도적으로 공개되어 있고 표준 `ubuntu-latest` 러너를 사용할 때만 가능합니다. 공개하기 전에 저장소와 Git 기록에 비밀값이 커밋되지 않았는지 확인하세요.

비공개 저장소에서는 예약 검증 잡이 자동으로 건너뛰므로 운영 승인 증거를 모을 수 없습니다. 잔여 무료 Actions 시간을 직접 확인한 뒤 한 번만 수동 검증할 때는 `authorize_private_minutes=true`를 선택할 수 있지만, 이는 예약 실행이나 유료 사용을 승인하지 않습니다. 공개 여부를 확인할 수 없으면 실패-폐쇄 상태로 두세요.

## 7. 현재 차단된 작업

- 수집원 활성화: `config/sources.reviewed.yml`의 모든 소스가 `enabled: false`입니다.
- Windows Codex 분석: `docs/evidence/codex-capability-proof.md`의 전체 판정이 `FAIL - blocked_capability`입니다.
- 85% 관련성 정확도: 인간 라벨 400개 벤치마크가 아직 없습니다.
- 3시간 freshness: 30 consecutive UTC days 증거가 아직 없습니다.
- 실제 무료 배포: Vercel/Supabase/GitHub 계정 설정과 live URL 검증 증거가 필요합니다.
