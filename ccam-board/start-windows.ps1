$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if (-not (Get-Command ccam.exe -ErrorAction SilentlyContinue)) {
    throw 'ccam.exe is missing from PATH. Install/build ccam first, then reopen PowerShell.'
}
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -X utf8 (Join-Path $PSScriptRoot 'server.py') --open-browser
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -X utf8 (Join-Path $PSScriptRoot 'server.py') --open-browser
} else {
    throw 'Python 3 is required. Install Python and reopen PowerShell.'
}
if ($LASTEXITCODE -ne 0) { throw "Dashboard exited with code $LASTEXITCODE" }
