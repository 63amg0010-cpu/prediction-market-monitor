# Cloud deployment handoff

Status: **beginner procedure; live production remains blocked until every success check passes**

이 문서는 처음 배포하는 운영자가 GitHub, Supabase, Vercel을 무료 범위에서 설정하는 순서입니다. 위에서 아래로 한 단계씩 진행하세요. 실패 조건이 하나라도 나오면 그 자리에서 멈추고, 유료 플랜·IPv4 추가 기능·대형 러너·유료 스케줄러를 켜지 마세요.

비밀번호, 토큰, 개인키, 전체 데이터베이스 URL은 터미널 출력, 스크린샷, GitHub 이슈, 채팅, 저장소 파일에 남기지 않습니다. 값은 암호 관리자와 공급자 비밀 입력창 사이에서만 이동하고, 붙여넣기가 끝나면 Windows 클립보드를 비웁니다.

공식 참고 문서:

- [GitHub 새 저장소](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)
- [GitHub 배포 환경](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [GitHub Actions 예산 중지](https://docs.github.com/en/billing/how-tos/set-up-budgets)
- [Supabase 연결 방식](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [Vercel 프로젝트 링크](https://vercel.com/docs/cli/link)
- [Vercel 모노레포 루트](https://vercel.com/docs/monorepos)
- [Vercel Root Directory와 CLI 적용](https://vercel.com/docs/builds/configure-a-build#root-directory)
- [Vercel 환경 변수](https://vercel.com/docs/environment-variables)
- [Vercel CLI 배포](https://vercel.com/docs/projects/deploy-from-cli)

## 0. 종이에 적을 이름만 정하기

비밀값을 적지 않는 배포 기록을 하나 만드세요. 다음 이름을 정합니다.

| 항목 | 예시 | 비밀 여부 |
|---|---|---|
| GitHub 저장소 | `prediction-market-monitor` | 공개 정보 |
| Vercel API 프로젝트 | `prediction-monitor-api` | 공개 정보 |
| Vercel Web 프로젝트 | `prediction-monitor-web` | 공개 정보 |
| Supabase Preview 프로젝트 | `prediction-monitor-preview` | 공개 정보 |
| Supabase Production 프로젝트 | `prediction-monitor-production` | 공개 정보 |
| 범위 버전 | `scope-2026-01` | 공개 정보 |

성공: 위 여섯 이름이 서로 구분됩니다. 실패: 프로젝트 이름에 비밀번호나 토큰을 넣었다면 새 이름으로 바꿉니다.

## 1. 로컬 공개 전 검사

PowerShell을 열어 저장소 폴더로 이동합니다.

```powershell
cd "C:\Users\UserK\Desktop\개발폴더\예측시장 커뮤니티 반응"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Verify-LocalSetup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Test-ExactToolVersion.ps1
```

두 번째 명령은 7개 줄이 모두 `passed`이고 종료 코드가 0이어야 합니다. 첫 번째 명령의 `dry-run`은 실행 예정 항목을 보여줍니다. `missing`, `placeholder`, `failed`가 나오면 공개 저장소 생성 전에 해결합니다.

로컬 비밀 파일과 Vercel 링크 파일이 Git에서 제외되는지 확인합니다.

```powershell
git check-ignore .env .env.local .vercel/project.json .vercel/repo.json
git ls-files .env .env.local .vercel/project.json .vercel/repo.json
```

성공: 첫 명령은 네 경로를 출력하고, 두 번째 명령은 아무것도 출력하지 않습니다. 실패: 두 번째 명령이 한 줄이라도 출력하면 푸시하지 말고 해당 파일을 스테이징에서 제거한 뒤 비밀값을 회전합니다.

현재 커밋 대상 파일 이름을 확인합니다.

```powershell
git add .
git status --short
git diff --cached --name-only
git diff --cached -- .env .env.local .vercel/project.json .vercel/repo.json
```

성공: 마지막 명령 출력이 비어 있고 `.env`, `.env.local`, `.vercel/project.json`, `.gjc`, `.omo`가 커밋 목록에 없습니다. 실패: 이 중 하나가 보이면 커밋과 공개 작업을 중단합니다.

## 2. GitHub 공개 저장소 만들기

1. 브라우저에서 GitHub에 로그인합니다.
2. 오른쪽 위 `+`를 누르고 `New repository`를 누릅니다.
3. `Owner`를 본인 계정으로 선택하고 0단계의 저장소 이름을 입력합니다.
4. `Public`을 선택합니다. 15분 무료 검증은 공개 저장소의 표준 러너가 필수입니다.
5. `Add a README`, `.gitignore`, `Choose a license`는 모두 선택하지 않습니다. 기존 로컬 저장소와 충돌을 막기 위한 설정입니다.
6. `Create repository`를 누릅니다.

로컬에서 첫 커밋을 만들고 GitHub가 새 저장소 화면에 보여 준 HTTPS 주소를 연결합니다.

```powershell
git branch -M main
git commit -m "Initial prediction market monitor"
git remote add origin https://github.com/<owner>/<repository>.git
git push -u origin main
git remote -v
```

성공: GitHub `Code` 화면에 `Public` 표시와 `main` 브랜치 파일이 보이고 `git remote -v`가 같은 저장소를 가리킵니다. 실패: push가 거절되거나 다른 저장소 주소가 보이면 비밀을 추가하지 말고 remote 주소부터 수정합니다.

`Actions` 탭을 열고 `collect`와 `verify` 워크플로를 각각 선택한 뒤 오른쪽 `...` 메뉴에서 `Disable workflow`를 누릅니다. 초기 배포가 끝날 때까지 두 예약 워크플로만 비활성화합니다. `ci`와 `migrate`는 활성 상태로 둡니다.

성공: `collect`와 `verify` 화면에 비활성 상태 안내가 보입니다. 실패: 예약 실행이 이미 시작됐다면 해당 실행을 취소하고 아직 설정되지 않은 URL이나 비밀로 성공했다고 판단하지 않습니다.

## 3. GitHub 유료 사용 차단과 Actions 설정

개인 계정은 `https://github.com/settings/billing`을 열고 `Budgets and alerts`를 누릅니다. 조직 저장소라면 조직 `Settings`의 `Billing and licensing`에서 같은 메뉴를 엽니다.

1. `New budget`을 누릅니다.
2. `Product-level budget`에서 `Actions`를 선택합니다.
3. 범위를 이 저장소 또는 저장소 소유 계정으로 지정합니다.
4. 유료 예산을 `$0`로 입력합니다.
5. `Stop usage when budget limit is reached`를 반드시 선택합니다.
6. `Receive budget threshold alerts`를 선택하고 `Create budget`을 누릅니다.

성공: Actions 예산 행에 `$0`와 사용 중지 표시가 함께 보입니다. 실패: 0달러 또는 사용 중지를 저장할 수 없다면 결제 수단을 추가하지 말고 배포를 중단합니다. GitHub는 새 예산 생성 이전의 같은 결제 주기 사용량은 소급 차단하지 않을 수 있으므로 현재 사용량도 함께 확인합니다.

저장소 `Settings` → `Actions` → `General`로 이동합니다. Actions 사용을 허용하고 `Workflow permissions`는 `Read repository contents and packages permissions`를 선택합니다. `Allow GitHub Actions to create and approve pull requests`는 끕니다. 표준 `ubuntu-latest`만 사용하며 larger runner를 만들지 않습니다.

성공: Actions 탭에서 `ci`와 `migrate`를 수동으로 열 수 있습니다. 실패: Actions가 조직 정책으로 막혔다면 우회하지 말고 조직 관리자 설정이 끝날 때까지 중단합니다.

## 4. GitHub 환경 만들기

저장소 `Settings` → `Environments` → `New environment`에서 다음 다섯 개를 정확히 만듭니다.

- `preview-deploy`
- `production-deploy`
- `production-migration`
- `production-collector`
- `production-verifier`

`preview-deploy`, `production-deploy`, `production-migration`은 각각 `Required reviewers`에 본인 계정을 추가합니다. 한 명 운영자이므로 `Prevent self-review`는 켜지 않습니다. `Deployment branches and tags`는 `Selected branches and tags`를 선택하고 `main`만 추가합니다. Preview PR 병합 전 배포를 허용해야 한다면 `preview-deploy`에만 `refs/pull/*/merge` 규칙을 추가합니다.

`production-collector`와 `production-verifier`에는 반복 예약 작업을 막는 reviewer를 두지 않습니다. 대신 `Deployment branches and tags`에서 `main`만 허용합니다.

성공: 다섯 환경이 보이고 배포·마이그레이션 환경은 승인 대기, 예약 환경은 main 제한으로 표시됩니다. 실패: 이름이 한 글자라도 다르면 워크플로가 다른 빈 환경을 자동 생성할 수 있으므로 삭제 후 정확히 다시 만듭니다.

## 5. Supabase Free 프로젝트 두 개 만들기

Preview가 Production 데이터를 바꾸지 않도록 Free 프로젝트를 두 개 사용합니다. Supabase 대시보드가 두 번째 Free 프로젝트 생성을 허용하지 않으면 Preview를 Production DB에 연결하지 말고 여기서 중단합니다.

각 프로젝트에 대해 다음을 반복합니다.

1. `https://supabase.com/dashboard`에 로그인합니다.
2. 조직을 선택하고 `New project`를 누릅니다.
3. Preview 또는 Production 프로젝트 이름을 입력합니다.
4. 암호 관리자에서 긴 무작위 DB 비밀번호를 생성해 `Database Password`에 넣습니다.
5. 사용자와 가까운 한 지역을 선택하되 두 프로젝트는 같은 지역으로 맞춥니다.
6. Free plan인지 확인합니다. 유료 Compute, IPv4 add-on, PITR을 선택하지 않습니다.
7. `Create new project`를 누르고 상태가 `Healthy`가 될 때까지 기다립니다.

성공: 두 프로젝트의 Overview가 `Healthy`이고 Billing/Usage 화면에 Free가 표시됩니다. 실패: 카드나 결제 승인을 요구하면 유료 옵션을 선택한 것이므로 뒤로 돌아갑니다.

각 프로젝트 상단의 `Connect`를 누릅니다.

- `Transaction pooler`, 포트 `6543`: Vercel API의 `DATABASE_URL`입니다.
- `Direct connection`, 포트 `5432`, 호스트 `db.<project-ref>.supabase.co`: 같은 endpoint를 Alembic용 async SQLAlchemy URL과 libpq 도구용 native URL로 각각 저장합니다.

Direct 연결은 Free에서 IPv6입니다. Windows 또는 GitHub 러너가 해당 호스트에 연결하지 못하면 transaction pooler로 마이그레이션하지 말고 Production migration을 `blocked`로 둡니다.

Supabase가 보여 주는 `[YOUR-PASSWORD]` 템플릿에 URL 인코딩된 비밀번호를 넣되 화면이나 명령 기록에 출력하지 않습니다. 다음 PowerShell을 필요한 secret마다 다시 실행합니다. `ASYNC`는 Alembic용 `postgresql+asyncpg://`, `NATIVE`는 `pg_dump`와 `pg_restore`용 `postgresql://`만 클립보드에 둡니다.

```powershell
$template = Read-Host "Supabase에서 복사한 connection string 템플릿"
$kind = Read-Host "Type ASYNC for Alembic or NATIVE for pg_dump/pg_restore"
if ($kind -cnotin @("ASYNC", "NATIVE")) { throw "Type exactly ASYNC or NATIVE" }
$secure = Read-Host "Database password" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $encoded = [Uri]::EscapeDataString($plain)
    $native = $template.Replace("[YOUR-PASSWORD]", $encoded).Replace("postgres://", "postgresql://")
    if ($native -notmatch "^postgresql://") { throw "Direct template must use a native PostgreSQL scheme" }
    $selected = if ($kind -ceq "ASYNC") {
        $native -replace "^postgresql://", "postgresql+asyncpg://"
    } else {
        $native
    }
    Set-Clipboard -Value $selected
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    Remove-Variable plain, encoded, native, selected, secure -ErrorAction SilentlyContinue
}
```

성공: `ASYNC` 결과는 `MIGRATION_DATABASE_URL`에만, `NATIVE` 결과는 `PG_DUMP_DATABASE_URL` 또는 `PG_RESTORE_DATABASE_URL`에만 붙여 넣고 값은 다시 표시되지 않습니다. 실패: 두 스킴을 반대로 넣었거나 URL이 터미널·화면 캡처에 나타났다면 저장을 중단하고, 노출 시 DB 비밀번호를 재설정합니다. 붙여넣기가 끝날 때마다 `Set-Clipboard -Value ""`를 실행합니다.

## 6. Vercel API와 Web 프로젝트 만들고 연결하기

PowerShell에서 고정된 CLI를 설치하고 브라우저 로그인 절차를 완료합니다.

```powershell
npm install --global vercel@51.7.0
vercel login
vercel --version
```

성공: 버전이 `51.7.0`이고 로그인한 계정/팀이 본인 것입니다. 실패: 다른 팀이 선택되면 프로젝트를 만들기 전에 `vercel teams switch <team-slug>`로 바꿉니다.

Vercel Dashboard에서 `Add New...` → `Project`를 누르고 같은 GitHub 저장소를 두 번 import해 API와 Web 프로젝트를 각각 만듭니다. 각 프로젝트의 생성 화면 또는 `Settings` → `Build and Deployment` → `Root Directory`에서 `Edit`을 누릅니다.

- API 프로젝트 Root Directory: `apps/api`
- Web 프로젝트 Root Directory: `apps/web`

두 프로젝트 모두 Root Directory 아래의 `Include source files outside of the Root Directory in the Build Step`을 켭니다. API는 저장소 루트의 검토된 `config/*.yml`을 런타임 설정으로 읽고, Web은 루트의 `pnpm-lock.yaml`을 사용하므로 이 설정을 끄면 안 됩니다. 두 프로젝트의 `Settings` → `Environment Variables`에서 `Automatically expose System Environment Variables`도 켭니다. Web은 Vercel이 생성하는 `VERCEL_DEPLOYMENT_ID`, `VERCEL_URL`, `VERCEL`, `VERCEL_ENV`를 배포 식별과 보안 헤더 판정에 사용합니다. 두 `vercel.json`의 `git.deploymentEnabled`는 `false`로 유지합니다. 배포는 GitHub Actions의 승인 환경에서만 실행하며, Vercel Git 연동의 자동 배포를 함께 켜면 같은 커밋이 중복 배포되고 승인 게이트를 우회합니다.

`Save`를 누릅니다. API는 `apps/api/vercel.json`, Web은 `apps/web/vercel.json` 설정을 사용합니다. Root Directory는 Vercel CLI에도 적용되므로 CLI를 `apps/api`나 `apps/web`에서 실행하면 경로가 두 번 적용됩니다.

PowerShell이 저장소 루트인지 확인한 뒤 Git 통합된 두 프로젝트를 한 번에 연결합니다.

```powershell
if (-not (Test-Path .github\workflows\ci.yml)) { throw "Run this command from the repository root" }
vercel link --repo
vercel project inspect prediction-monitor-api
vercel project inspect prediction-monitor-web
git status --short -- .vercel
```

성공: 두 `project inspect` 명령이 서로 다른 Project ID와 올바른 이름을 보여 주고, Dashboard Root Directory는 각각 `apps/api`와 `apps/web`이며, 두 프로젝트의 외부 소스 포함 설정과 시스템 환경 변수 자동 노출이 켜져 있고, Vercel Git 자동 배포가 꺼져 있으며, `git status`에는 `.vercel`이 없습니다. 실패: 하위 앱 폴더에서 CLI를 실행했거나 두 프로젝트 ID가 같거나 필수 설정이 다르거나 `.vercel`이 Git에 보이면 배포하지 말고 설정을 바로잡습니다.

각 프로젝트 `Settings` → `Environment Variables`에서 `Preview`와 `Production` 값을 별도로 추가합니다. `Sensitive` 옵션이 있으면 비밀값에 사용합니다.

API 프로젝트에 필요한 키:

`DATABASE_URL`, `API_BASE_URL`, `WEB_PUBLIC_ORIGIN`, `MONITOR_SCOPE_VERSION`, `SERVICE_TOKEN_KEY_ID`, `SERVICE_TOKEN_ISSUER_PRIVATE_KEY`, `SERVICE_TOKEN_ISSUER_PUBLIC_KEY`, `BFF_CLIENT_CREDENTIAL`, `BFF_CREDENTIAL_VERSION`, `WORKER_BOOTSTRAP_SECRET`, `WORKER_CREDENTIAL_VERSION`, `CRON_SECRET`, `ADMIN_PASSWORD_ARGON2ID_HASH`, `SESSION_HMAC_SECRET`, `GITHUB_REPOSITORY`, `GITHUB_WORKFLOW_REFS`, `GITHUB_ALLOWED_REFS`, `GITHUB_ALLOWED_ENVIRONMENTS`.

Web 프로젝트에 필요한 키:

`API_BASE_URL`, `BFF_CLIENT_CREDENTIAL`, `BFF_CREDENTIAL_VERSION`, `WEB_PUBLIC_ORIGIN`.

Preview의 `DATABASE_URL`은 Preview Supabase transaction pooler, Production은 Production Supabase transaction pooler를 사용합니다. 같은 환경의 API와 Web은 `BFF_CLIENT_CREDENTIAL`, `BFF_CREDENTIAL_VERSION`, `WEB_PUBLIC_ORIGIN`이 정확히 같아야 합니다. API의 `MONITOR_SCOPE_VERSION`은 `config/sources.reviewed.yml`의 reviewed scope와 GitHub collector/verifier 환경 변수의 값과 같아야 합니다. 서로 다른 환경끼리는 별도 비밀값을 사용합니다. `WEB_PUBLIC_ORIGIN`과 scope는 서버 설정이며 `NEXT_PUBLIC_` 접두사로 복제하지 않습니다.

`GITHUB_WORKFLOW_REFS`, `GITHUB_ALLOWED_REFS`, `GITHUB_ALLOWED_ENVIRONMENTS`는 같은 순서와 같은 길이의 JSON 배열이어야 합니다. collect와 verify 두 workflow ref에 대응해 `GITHUB_ALLOWED_REFS`는 `["refs/heads/main","refs/heads/main"]`, `GITHUB_ALLOWED_ENVIRONMENTS`는 `["production-collector","production-verifier"]`입니다. `<owner>`와 `<repository>`는 실제 공개 저장소 값으로 바꿉니다.

성공: 각 프로젝트 Dashboard의 Preview와 Production 범위에 올바른 키 이름만 보이고 값은 노출되지 않으며, API/Web origin과 BFF 값 및 API/GitHub scope가 환경별로 일치합니다. 실패: Production DB URL이 Preview에 있거나 origin/BFF/scope 값이 불일치하면 배포 전에 수정합니다.

Vercel 계정 아바타 → `Settings` → `Tokens` → `Create Token`에서 CI 전용 토큰을 만들고 암호 관리자에 한 번만 저장합니다. 토큰 값은 저장소나 `.env.example`에 넣지 않습니다.

Vercel Team `Settings`에서 Team ID를 `VERCEL_ORG_ID`로, 각 프로젝트 `Settings` → `General`의 Project ID를 `VERCEL_API_PROJECT_ID`와 `VERCEL_WEB_PROJECT_ID`로 기록합니다. ID 자체는 인증 비밀이 아니지만 GitHub 환경 secret으로 취급하고 로컬 링크 파일은 커밋하지 않습니다.

## 7. GitHub 환경 값 넣기

GitHub 저장소 `Settings` → `Environments`에서 환경 이름을 누르고 `Environment secrets`의 `Add secret`, `Environment variables`의 `Add variable`을 사용합니다.

`preview-deploy`와 `production-deploy` secrets:

- `VERCEL_TOKEN`
- `VERCEL_ORG_ID`
- `VERCEL_API_PROJECT_ID`
- `VERCEL_WEB_PROJECT_ID`

`production-migration` secret:

- `MIGRATION_DATABASE_URL`: Production Supabase direct connection의 `postgresql+asyncpg://` 표현, Alembic 전용
- `PG_DUMP_DATABASE_URL`: 같은 direct connection의 `postgresql://` 표현, `pg_dump` 전용
- `PG_RESTORE_DATABASE_URL`: 같은 direct connection의 `postgresql://` 표현, 승인된 rollback의 `pg_restore` 전용

`production-verifier` variables:

- `MONITOR_API_URL`: Production API HTTPS URL
- `MONITOR_SCOPE_VERSION`: 0단계 범위 버전

`production-collector` variables:

- `MONITOR_API_URL`
- `MONITOR_SCOPE_VERSION`
- `MONITOR_DEPLOYMENT_ACTIVATION_AT`: 활성화 직전 UTC ISO-8601 값
- `MONITOR_SOURCE_IDS`: 승인되고 DB에 등록된 UUID 목록
- `REDDIT_USER_AGENT`: Reddit 승인 후에만

`production-collector` secrets:

- `MONITOR_SOURCE_BINDINGS_JSON`: 승인된 source ID와 authorization의 JSON
- `REDDIT_OAUTH_ACCESS_TOKEN`: Reddit 승인 후에만

현재 `config/sources.reviewed.yml`이 모두 `enabled: false`이면 collector의 source 변수와 secrets를 임의로 만들지 않고 collector를 비활성 상태로 유지합니다.

성공: GitHub 화면에는 이름만 보이고 secret 값은 다시 표시되지 않습니다. 실패: secret을 Repository variable에 넣었거나 환경 이름이 다르면 삭제하고 올바른 environment secret으로 다시 만듭니다.

## 8. Preview DB 마이그레이션과 Preview 배포

Preview direct connection에 대해 다음 순서를 그대로 실행합니다. URL 자체를 명령 기록이나 출력에 쓰지 않습니다.

1. 5단계 도우미에 `ASYNC`를 입력하고 끝나면 다음 두 줄을 실행합니다.

```powershell
$env:MIGRATION_DATABASE_URL = Get-Clipboard
Set-Clipboard -Value ""
```

2. 5단계 도우미에 `NATIVE`를 입력하고 끝나면 다음 두 줄을 실행합니다.

```powershell
$env:PG_DUMP_DATABASE_URL = Get-Clipboard
Set-Clipboard -Value ""
```

3. 5단계 도우미에 `NATIVE`를 다시 입력하고 끝나면 다음 두 줄을 실행합니다.

```powershell
$env:PG_RESTORE_DATABASE_URL = Get-Clipboard
Set-Clipboard -Value ""
```

세 값을 모두 설정한 뒤에만 backup과 Alembic을 실행합니다.

```powershell
$env:PYTHONPATH = "apps/api"
pg_dump --format=custom --no-owner --no-acl --file "$env:TEMP\prediction-monitor-preview-before.dump" "$env:PG_DUMP_DATABASE_URL"
uv run --package monitor-api alembic -c apps/api/alembic.ini current
uv run --package monitor-api alembic -c apps/api/alembic.ini heads
uv run --package monitor-api alembic -c apps/api/alembic.ini upgrade head
uv run --package monitor-api alembic -c apps/api/alembic.ini current
```

성공: `pg_dump` 종료 코드가 0이고 마지막 `current` revision이 `heads`의 단일 revision `20260723_0005`과 같습니다. 실패: direct host DNS/IPv6 연결, `20260723_0005`가 아닌 결과, 둘 이상의 head, upgrade 오류가 있으면 Preview 배포를 멈춥니다. Upgrade 후 실패했다면 다음으로 복원합니다.

```powershell
pg_restore --clean --if-exists --no-owner --no-acl --dbname "$env:PG_RESTORE_DATABASE_URL" "$env:TEMP\prediction-monitor-preview-before.dump"
```

Preview migration이 녹색인 뒤, real PostgreSQL 보존·재현 증명(RP-07)을 한 번 실행합니다. 이 명령은 direct async URL을 화면에 출력하지 않으며 고정 테스트 행과 restricted reader role을 만듭니다. Preview에서만 실행하고 Production에는 절대 적용하지 마세요.

```powershell
$env:RP07_DATABASE_URL = $env:MIGRATION_DATABASE_URL
uv run --package monitor-api pytest apps/api/tests/integration/test_postgres_report_retention.py -q -rs
Remove-Item Env:RP07_DATABASE_URL
```

성공: `1 passed`. 실패 또는 `1 skipped`이면 real-DB RP-07 증명이 없으므로 Preview/Production 승격을 중단합니다. 일반 CI의 같은 명령은 URL을 주지 않아 `1 skipped`가 예상되며, 둘은 서로 다른 gate입니다.

새 브랜치를 만들고 빈 커밋을 push해 `ci.yml`의 Preview 배포를 실행합니다.

```powershell
git switch -c deployment-preview
git commit --allow-empty -m "Trigger deployment preview"
git push -u origin deployment-preview
```

GitHub에서 Pull Request를 만들고 `Actions` → `ci` 실행을 엽니다. `preview-deploy` 승인 요청에서 `Review deployments` → `preview-deploy` → `Approve and deploy`를 누릅니다.

CI의 API/Web matrix는 각각 격리된 runner의 저장소 루트에서 `vercel pull`, `vercel build`, `test -d .vercel/output`, `vercel deploy --prebuilt` 순서로 실행합니다. `VERCEL_PROJECT_ID`만 matrix 대상에 따라 바뀌며 `working-directory: apps/...`를 사용하지 않습니다.

성공: API와 Web matrix가 모두 녹색이고 루트 `.vercel/output` 검사 직후 배포된 각 Vercel deployment 상태가 `READY`입니다. `vercel inspect <preview-url>`도 `READY`를 보여야 합니다. 실패: 한 프로젝트이라도 `ERROR`, `CANCELED`, 루트 `.vercel/output` 없음이면 PR을 merge하지 않습니다.

Preview API의 `/v1/health`, Web `/login`, 로그인, `/status`, `/posts`, `/reports`를 확인합니다. Preview에 실제 source secret을 넣지 않습니다.

## 9. 보호된 Production 마이그레이션

Preview가 통과한 뒤에도 main을 merge하기 전에 8단계의 세 URL 할당 순서를 Production direct connection으로 다시 실행합니다. Preview 값이 프로세스에 남아 있으면 Production 명령을 실행하지 않습니다. Production `MIGRATION_DATABASE_URL`은 `ASYNC`, Production `PG_DUMP_DATABASE_URL`과 `PG_RESTORE_DATABASE_URL`은 각각 `NATIVE` 결과여야 합니다. 그 뒤 저장소 밖 `$env:TEMP`에 local private backup을 만듭니다.

```powershell
pg_dump --format=custom --no-owner --no-acl --file "$env:TEMP\prediction-monitor-production-before.dump" "$env:PG_DUMP_DATABASE_URL"
```

성공: 종료 코드 0이고 dump 파일 크기가 0보다 큽니다. 실패: migration workflow를 실행하지 않습니다.

GitHub `Actions` → `migrate` → `Run workflow`를 누르고 `confirm`에 정확히 `migrate-production`을 입력합니다. 실행이 `Waiting`이 되면 `Review deployments`에서 `production-migration`을 선택하고 승인합니다.

로그에서 다음 단계가 순서대로 녹색이어야 합니다.

1. `Validate database URL drivers without disclosure`
2. `Export pre-migration backup`
3. `Check current migration revision`
4. `Check repository migration head`
5. `Apply reviewed migrations`
6. `Verify migrated revision`

성공: 전체 workflow가 녹색이고 마지막 current revision이 단일 repository head `20260723_0005`와 같습니다. 실패: `Roll back failed migration from ephemeral backup`이 실행되어 녹색인지 확인합니다. rollback도 실패하면 수동 restore를 실행하고 Production 배포를 중단합니다. RP-07 fixture는 Production에서 실행하지 않습니다; Preview proof artifact가 없는 상태는 Production acceptance를 차단합니다.

```powershell
pg_restore --clean --if-exists --no-owner --no-acl --dbname "$env:PG_RESTORE_DATABASE_URL" "$env:TEMP\prediction-monitor-production-before.dump"
uv run --package monitor-api alembic -c apps/api/alembic.ini current
```

## 10. Production 배포 또는 검증된 Preview 승격

기본 경로는 PR을 main에 merge해 `ci.yml`의 `deploy-production`이 Production 환경 변수로 다시 build하고 `--prebuilt --prod`로 배포하게 하는 것입니다.

1. Preview 결과와 Production migration이 녹색인지 다시 확인합니다.
2. Pull Request에서 `Merge pull request`를 누릅니다.
3. `Actions` → 새 `ci` 실행을 엽니다.
4. `production-deploy` 승인 요청에서 `Approve and deploy`를 누릅니다.

성공: API/Web 두 matrix가 녹색이고 Vercel `Deployments`에서 두 Production deployment가 `READY`입니다. 실패: production alias를 수동으로 바꾸지 말고 `vercel inspect <url>`의 build 오류를 고친 뒤 새 Preview부터 반복합니다.

동일한 Preview artifact를 재빌드 없이 승격하기로 명시적으로 결정한 경우에만 각 링크된 프로젝트 디렉터리에서 다음을 사용할 수 있습니다.

```powershell
vercel promote <validated-preview-url> --yes
vercel promote status
```

Preview와 Production 환경 변수가 다르면 기본 CI Production build 경로를 사용합니다.

## 11. Postdeploy 핵심 경로와 스케줄 활성화

API부터 확인합니다.

```powershell
$apiHealth = Invoke-WebRequest "https://<api-domain>/v1/health" -UseBasicParsing
$healthBody = $apiHealth.Content | ConvertFrom-Json
$apiHealth.StatusCode
$healthBody
$apiHealth.Headers["X-Correlation-ID"]
```

성공: HTTP `200`, JSON의 `status`가 `ok`, `db`가 `ok`이고 `X-Correlation-ID`가 있습니다. 실패: DB를 사용할 수 없어도 공개 health는 HTTP `200`과 `{"status":"degraded","version":"0.1.0","db":"unavailable"}`를 반환하므로 배포를 승인하지 않고 `DATABASE_URL`과 Supabase 상태를 확인합니다. HTTP `503 service_unavailable`은 로그인·대시보드·수집 같은 보호 작업의 필수 어댑터가 구성되지 않은 경우에만 기대하며 health 성공으로 해석하지 않습니다. HTTP `404`는 Vercel route/root 오류입니다.

Web Production URL을 브라우저에서 엽니다.

1. `/login`에서 운영자 비밀번호로 로그인합니다.
2. `/` 대시보드가 mock 데이터 없이 열리는지 봅니다.
3. `/status`에서 DB, source, verifier, worker 상태를 봅니다.
4. `/posts`와 `/reports`가 인증 세션 안에서 열리는지 봅니다.
5. 로그아웃 후 보호 페이지가 로그인으로 돌아가는지 확인합니다.

성공: 로그인 cookie가 동작하고 Dashboard API가 200이며 비밀값이 화면/응답에 없습니다. 실패: 401은 admin hash/session/BFF 값을, 403은 `WEB_PUBLIC_ORIGIN`, 503은 API dependency와 DB를 점검합니다.

GitHub `Actions` → `verify` → `Enable workflow`를 누른 뒤 `Run workflow`로 한 번 실행합니다. 공개 저장소에서는 `authorize_private_minutes`를 false로 둡니다.

성공: verifier job이 녹색이고 Production DB에 해당 15분 expected slot observation이 생깁니다. 실패: OIDC 401이면 `GITHUB_REPOSITORY`, 두 workflow refs, allowed refs, `production-verifier` 환경 이름을 확인합니다.

승인된 source가 DB에 enabled 상태이고 collector variables/secrets가 완성된 경우에만 `Actions` → `collect` → `Enable workflow`를 누르고 수동 1회를 실행합니다. `MONITOR_DEPLOYMENT_ACTIVATION_AT`은 활성화 직전 UTC로 설정합니다.

성공: collect, page commit, completion, attached verify가 녹색이고 dashboard source timestamp가 갱신됩니다. 실패: source가 disabled/unauthorized이면 정상적인 fail-closed 상태이므로 예약 collector를 다시 disable합니다. 유료 또는 비승인 source로 대체하지 않습니다.

## 12. 30일 240/2,880 증거 캡처

검증 첫 슬롯 전에 UTC 시작 경계를 기록합니다. 시작은 완전한 UTC 날짜 `00:00:00Z`, 종료는 정확히 30일 뒤입니다. 같은 `MONITOR_SCOPE_VERSION`, workflow commit SHA, 공개 visibility를 30일 동안 유지합니다. 변경되면 새 창으로 다시 시작합니다.

매일 확인할 것:

- `verify` 예약 실행이 15분마다 관측되고 durable observation을 남기는지
- `collect`의 하루 8개 minute-17 슬롯이 materialize되는지
- missing, delayed, unauthorized, quota-blocked, failed 상태를 pass로 바꾸지 않았는지
- 공개 저장소 활동 중단으로 scheduled workflow가 비활성화되지 않았는지

30일 뒤 Supabase Production → `SQL Editor` → `New query`에서 아래 쿼리를 실행합니다. `<scope>`, `<start>`, `<end>`만 비밀이 아닌 실제 값으로 바꿉니다.

```sql
with params as (
  select
    '<scope>'::text as scope_version,
    '<start>'::timestamptz as window_start,
    '<end>'::timestamptz as window_end
), enabled_sources as (
  select id
  from community_sources, params
  where enabled and community_sources.scope_version = params.scope_version
), collection as (
  select count(*)::int as slot_count
  from collection_slots, params
  where collection_slots.scope_version = params.scope_version
    and due_slot_utc >= window_start and due_slot_utc < window_end
), verifier as (
  select
    count(distinct expected_slot_utc)::int as slot_count,
    count(*)::int as observation_count,
    count(*) filter (where status <> 'passed')::int as failed_observation_count
  from verification_observations, params
  where verification_observations.scope_version = params.scope_version
    and expected_slot_utc >= window_start and expected_slot_utc < window_end
)
select
  collection.slot_count as collection_slots,
  verifier.slot_count as verifier_slots,
  verifier.observation_count,
  (select count(*) from enabled_sources) * 2880 as expected_observation_count,
  verifier.failed_observation_count
from collection cross join verifier;
```

성공은 정확히 다음과 같습니다.

- `collection_slots = 240`
- `verifier_slots = 2880`
- `observation_count = expected_observation_count`
- `failed_observation_count = 0`

결과 표의 `Download CSV`를 눌러 저장소 밖 전용 증거 폴더에 저장합니다. 같은 폴더에 GitHub repository visibility 화면, collect/verify workflow 파일 commit SHA, 30일 Actions run history, Vercel Production deployment IDs, Supabase project refs, 시작/종료 UTC를 비밀 없이 기록합니다.

```powershell
$evidence = "C:\Users\UserK\Documents\prediction-monitor-private-evidence"
New-Item -ItemType Directory -Force $evidence | Out-Null
Get-FileHash "$evidence\freshness-30d.csv" -Algorithm SHA256 | Format-List
```

성공: `docs/source-compliance.md` 절차와 숫자가 모두 맞고 CSV SHA-256, reviewer, review time이 기록됩니다. 실패: 한 슬롯이라도 없거나 failed observation이 있으면 Production acceptance는 계속 blocked이고 수정 후 새로운 30일 창을 시작합니다.

## 완료 판정

이 문서를 끝까지 실행해도 live Docker, source authorization, Windows Codex capability, 400개 benchmark가 별도로 통과하지 않았다면 전체 제품은 아직 Production accepted가 아닙니다. 공급자 화면이나 URL이 실제로 검증되지 않은 항목은 `not_started` 또는 `blocked`로 남깁니다.
