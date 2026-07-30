[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AttemptDir,
    [Parameter(Mandatory = $true)][string]$DatabaseAdminUrlEnv,
    [Parameter(Mandatory = $true)][string]$DatabaseUrlEnv,
    [Parameter(Mandatory = $true)][string]$BaseSha,
    [Parameter(Mandatory = $true)][string]$ReviewedSha,
    [string]$FailureFixture,
    [switch]$ExpectMetaFailure
)

$ErrorActionPreference = "Stop"
$arguments = @(
    "apps/api/scripts/local_qa_orchestrator.py",
    "--attempt-dir", $AttemptDir,
    "--database-admin-url-env", $DatabaseAdminUrlEnv,
    "--database-url-env", $DatabaseUrlEnv,
    "--base-sha", $BaseSha,
    "--reviewed-sha", $ReviewedSha,
    "--wrapper", "powershell"
)
if ($FailureFixture) {
    $arguments += @("--failure-fixture", $FailureFixture)
}
if ($ExpectMetaFailure) {
    $arguments += "--expect-meta-failure"
}

& uv run --no-sync --package monitor-api python @arguments
exit $LASTEXITCODE
