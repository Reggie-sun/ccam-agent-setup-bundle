$ErrorActionPreference = 'Stop'
$scripts = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'claude-switcher\windows') -Recurse -File |
    Where-Object { $_.Extension -in '.ps1', '.psm1' }
$scripts += Get-Item -LiteralPath (Join-Path $PSScriptRoot 'ccam\install.ps1'), (Join-Path $PSScriptRoot 'ccam-board\start-windows.ps1')
foreach ($file in $scripts) {
    $tokens = $null
    $errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
}
& (Join-Path $PSScriptRoot 'ccam\tests\install-windows.Tests.ps1')
& (Join-Path $PSScriptRoot 'claude-switcher\windows\tests\install.Tests.ps1')
& python -X utf8 -m unittest discover -s (Join-Path $PSScriptRoot 'ccam-board\tests') -v
if ($LASTEXITCODE -ne 0) { throw 'Dashboard tests failed' }
& python -X utf8 -m compileall -q (Join-Path $PSScriptRoot 'ccam-board')
if ($LASTEXITCODE -ne 0) { throw 'Python compile check failed' }
foreach ($line in [IO.File]::ReadAllLines((Join-Path $PSScriptRoot 'SHA256SUMS'))) {
    if ($line -notmatch '^([0-9a-f]{64})  (.+)$') { throw 'Invalid checksum manifest entry' }
    $expected = $Matches[1]
    $file = Join-Path $PSScriptRoot $Matches[2]
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
        throw "Checksum mismatch: $file"
    }
}
Write-Host 'PASS: SHA256SUMS'
Write-Host 'PASS: Windows script parsing, installer regressions, dashboard tests and Python compile check'
