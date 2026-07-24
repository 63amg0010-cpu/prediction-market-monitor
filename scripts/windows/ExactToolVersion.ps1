function Test-ExactVersionOutput {
    param(
        [ValidateSet("uv", "node")]
        [string]$Name,
        [string]$Text,
        [string]$RequiredVersion
    )

    $numericVersionPattern = '\A(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\z'
    $regexOptions = [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
    if (-not [regex]::IsMatch($RequiredVersion, $numericVersionPattern, $regexOptions)) {
        return $false
    }

    if ($Name -eq "node") {
        return $Text -ceq "v$RequiredVersion"
    }

    $prefix = "uv "
    if (-not $Text.StartsWith($prefix, [System.StringComparison]::Ordinal)) {
        return $false
    }
    $remainder = $Text.Substring($prefix.Length)
    $separatorIndex = $remainder.IndexOf(" ", [System.StringComparison]::Ordinal)
    if ($separatorIndex -lt 0) {
        return $remainder -ceq $RequiredVersion
    }

    $versionToken = $remainder.Substring(0, $separatorIndex)
    if ($versionToken -cne $RequiredVersion) {
        return $false
    }
    $vendorSuffix = $remainder.Substring($separatorIndex + 1)
    $vendorSuffixPattern = '\A\([0-9a-f]{7,40} [0-9]{4}-[0-9]{2}-[0-9]{2}(?: [A-Za-z0-9_.-]+)?\)\z'
    return [regex]::IsMatch($vendorSuffix, $vendorSuffixPattern, $regexOptions)
}
