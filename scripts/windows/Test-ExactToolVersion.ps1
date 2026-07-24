$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "ExactToolVersion.ps1")

$cases = @(
    [pscustomobject]@{ label = "uv bare exact"; name = "uv"; output = "uv 0.5.30"; required = "0.5.30"; expected = $true },
    [pscustomobject]@{ label = "uv vendor exact"; name = "uv"; output = "uv 0.5.30 (c4d0caa14 2025-02-12 x86_64-pc-windows-msvc)"; required = "0.5.30"; expected = $true },
    [pscustomobject]@{ label = "node exact"; name = "node"; output = "v22.14.0"; required = "22.14.0"; expected = $true },
    [pscustomobject]@{ label = "uv longer token"; name = "uv"; output = "uv 0.5.300"; required = "0.5.30"; expected = $false },
    [pscustomobject]@{ label = "node zero-padded token"; name = "node"; output = "v22.14.01"; required = "22.14.0"; expected = $false },
    [pscustomobject]@{ label = "uv unapproved suffix"; name = "uv"; output = "uv 0.5.30-beta"; required = "0.5.30"; expected = $false },
    [pscustomobject]@{ label = "node trailing text"; name = "node"; output = "v22.14.0 extra"; required = "22.14.0"; expected = $false }
)

$failed = $false
foreach ($case in $cases) {
    $actual = Test-ExactVersionOutput -Name $case.name -Text $case.output -RequiredVersion $case.required
    $status = if ($actual -eq $case.expected) { "passed" } else { "failed" }
    Write-Output ("{0}: {1}" -f $case.label, $status)
    if ($status -eq "failed") {
        $failed = $true
    }
}

if ($failed) {
    exit 1
}
exit 0
