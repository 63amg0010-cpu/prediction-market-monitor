param(
    [switch]$RunChecks,
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Results = [System.Collections.Generic.List[object]]::new()
$RequiredUvVersion = "0.5.30"
$RequiredNodeVersion = "22.14.0"
. (Join-Path $PSScriptRoot "ExactToolVersion.ps1")

function Add-Result {
    param(
        [string]$Name,
        [string]$Status,
        [string]$Detail
    )
    $Results.Add([pscustomobject]@{
        name = $Name
        status = $Status
        detail = $Detail
    }) | Out-Null
}

function Test-CommandAvailable {
    param([string]$Name)
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $found) {
        Add-Result $Name "missing" "command not found"
        return
    }
    Add-Result $Name "present" $found.Source
}

function Invoke-CheckedCommand {
    param(
        [string]$Name,
        [string[]]$Command,
        [int[]]$AllowedExitCodes = @(0),
        [string]$RequiredOutput = ""
    )
    if (-not $RunChecks) {
        Add-Result $Name "dry-run" ($Command -join " ")
        return
    }
    $arguments = @($Command | Select-Object -Skip 1)
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Command[0] @arguments 2>&1
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
    $text = ($output | Out-String).Trim()
    if ($AllowedExitCodes -notcontains $exitCode) {
        Add-Result $Name "failed" "exit=$exitCode"
        return
    }
    if ($RequiredOutput.Length -gt 0 -and -not $text.Contains($RequiredOutput)) {
        Add-Result $Name "failed" "required output not found"
        return
    }
    Add-Result $Name "passed" "exit=$exitCode"
}

function Test-ExactToolVersion {
    param(
        [string]$Name,
        [string[]]$Command,
        [string]$RequiredVersion
    )
    if (-not $RunChecks) {
        Add-Result "$Name exact version" "dry-run" (($Command -join " ") + " requires $RequiredVersion")
        return
    }
    $arguments = @($Command | Select-Object -Skip 1)
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Command[0] @arguments 2>&1
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $text = ($output | Out-String).Trim()
    if (Test-ExactVersionOutput -Name $Name -Text $text -RequiredVersion $RequiredVersion) {
        Add-Result "$Name exact version" "passed" "required=$RequiredVersion"
        return
    }
    Add-Result "$Name exact version" "failed" "required=$RequiredVersion"
}

function Read-DotEnvKeys {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) {
        return $map
    }
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        if ($parts.Count -eq 2) {
            $map[$parts[0]] = $parts[1]
        }
    }
    return $map
}

Push-Location $RepoRoot
try {
    Test-CommandAvailable "uv"
    Test-CommandAvailable "node"
    Test-CommandAvailable "corepack"
    Test-CommandAvailable "pnpm"
    if (-not $SkipDocker) {
        Test-CommandAvailable "docker"
    }

    foreach ($path in @(
        ".python-version",
        ".node-version",
        "pyproject.toml",
        "package.json",
        "pnpm-lock.yaml",
        "uv.lock",
        ".env.example",
        "docker-compose.yml"
    )) {
        if (Test-Path $path) {
            Add-Result $path "present" "file exists"
        } else {
            Add-Result $path "missing" "file missing"
        }
    }

    $envPath = Join-Path $RepoRoot ".env"
    if (-not (Test-Path $envPath)) {
        Add-Result ".env" "missing" "copy .env.example to .env and fill secrets"
    } else {
        Add-Result ".env" "present" "values redacted"
        $requiredKeys = @(
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "DATABASE_URL",
            "HOST_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "PG_DUMP_DATABASE_URL",
            "PG_RESTORE_DATABASE_URL",
            "CONTAINER_DATABASE_URL",
            "API_BASE_URL",
            "CONTAINER_API_BASE_URL",
            "WEB_DEPLOYMENT_ID",
            "WEB_PUBLIC_ORIGIN",
            "MONITOR_SCOPE_VERSION",
            "ADMIN_PASSWORD_ARGON2ID_HASH",
            "SESSION_HMAC_SECRET",
            "SERVICE_TOKEN_KEY_ID",
            "SERVICE_TOKEN_ISSUER_PRIVATE_KEY",
            "SERVICE_TOKEN_ISSUER_PUBLIC_KEY",
            "BFF_CLIENT_CREDENTIAL",
            "BFF_CREDENTIAL_VERSION",
            "CRON_SECRET",
            "WORKER_BOOTSTRAP_SECRET",
            "WORKER_CREDENTIAL_VERSION",
            "GITHUB_REPOSITORY",
            "GITHUB_WORKFLOW_REFS",
            "GITHUB_ALLOWED_REFS",
            "GITHUB_ALLOWED_ENVIRONMENTS"
        )
        $dotenv = Read-DotEnvKeys $envPath
        foreach ($key in $requiredKeys) {
            if (-not $dotenv.ContainsKey($key)) {
                Add-Result "env:$key" "missing" "key missing"
                continue
            }
            $value = [string]$dotenv[$key]
            if ($value.Contains("<") -or $value.Contains(">") -or $value.Trim().Length -eq 0) {
                Add-Result "env:$key" "placeholder" "replace placeholder"
            } else {
                Add-Result "env:$key" "set" "value redacted"
            }
        }
    }

    Invoke-CheckedCommand "uv version" @("uv", "--version")
    Invoke-CheckedCommand "node version" @("node", "--version")
    Test-ExactToolVersion "uv" @("uv", "--version") $RequiredUvVersion
    Test-ExactToolVersion "node" @("node", "--version") $RequiredNodeVersion
    Invoke-CheckedCommand "pnpm lock install check" @("pnpm", "install", "--frozen-lockfile", "--ignore-scripts")
    Invoke-CheckedCommand "api unit contract tests" @("uv", "run", "--package", "monitor-api", "pytest", "apps/api/tests/contracts", "apps/api/tests/unit", "-q")
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = "workers/codex-worker/src"
    try {
        Invoke-CheckedCommand "worker blocked capability check" @("uv", "run", "python", "-m", "monitor_worker") @(1, 2) "blocked_capability"
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
    }
    if (-not $SkipDocker) {
        Invoke-CheckedCommand "docker compose config quiet" @("docker", "compose", "config", "--quiet")
    }
}
finally {
    Pop-Location
}

$Results | Format-Table -AutoSize
$failed = $Results | Where-Object { $_.status -in @("missing", "placeholder", "failed") }
if (@($failed).Count -gt 0) {
    exit 1
}
exit 0
